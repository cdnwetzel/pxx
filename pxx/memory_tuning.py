"""Dynamic memory tuning for injection (Phase 5 Tier 3).

Provides relevance thresholds, result limits, and context-aware sizing
to prevent memory from bloating aider's prompt context.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class MemoryTuneConfig:
    """Configuration for memory tuning."""

    min_relevance_score: float
    max_observations: int
    max_context_chars: int
    leave_context_headroom: int

    @classmethod
    def from_env(cls) -> MemoryTuneConfig:
        """Load tuning config from environment variables.

        Returns:
            MemoryTuneConfig with values from env or defaults.
        """
        return cls(
            min_relevance_score=float(os.getenv("PXX_MEMORY_THRESHOLD", "0.5")),
            max_observations=int(os.getenv("PXX_MEMORY_LIMIT", "5")),
            max_context_chars=int(os.getenv("PXX_MEMORY_MAX_CONTEXT", "8000")),
            leave_context_headroom=int(os.getenv("PXX_MEMORY_HEADROOM", "2000")),
        )

    def __post_init__(self) -> None:
        """Validate config values."""
        if not 0.0 <= self.min_relevance_score <= 1.0:
            raise ValueError(
                f"min_relevance_score must be 0.0-1.0, got {self.min_relevance_score}"
            )
        if self.max_observations < 1:
            raise ValueError(
                f"max_observations must be >= 1, got {self.max_observations}"
            )
        if self.max_context_chars < 500:
            raise ValueError(
                f"max_context_chars must be >= 500, got {self.max_context_chars}"
            )
        if self.leave_context_headroom < 0:
            raise ValueError(
                f"leave_context_headroom must be >= 0, got {self.leave_context_headroom}"
            )


class MemoryTuner:
    """Apply relevance thresholds and result limits to memory injections."""

    def __init__(
        self,
        min_relevance_score: float = 0.5,
        max_observations: int = 5,
        max_context_chars: int = 8000,
        leave_context_headroom: int = 2000,
    ):
        """Initialize tuner with thresholds and limits.

        Args:
            min_relevance_score: Minimum score [0.0, 1.0] to include (default: 0.5)
            max_observations: Max observations to return (default: 5)
            max_context_chars: Max total context size in chars (default: 8000)
            leave_context_headroom: Reserve for aider responses (default: 2000)
        """
        self.config = MemoryTuneConfig(
            min_relevance_score=min_relevance_score,
            max_observations=max_observations,
            max_context_chars=max_context_chars,
            leave_context_headroom=leave_context_headroom,
        )

    def filter_by_relevance(self, observations: list[dict]) -> list[dict]:
        """Filter observations by minimum relevance score.

        Args:
            observations: List of observation dicts with 'score' field

        Returns:
            Filtered list where all scores >= min_relevance_score.
        """
        return [
            obs
            for obs in observations
            if obs.get("score", 0) >= self.config.min_relevance_score
        ]

    def limit_by_count(self, observations: list[dict]) -> list[dict]:
        """Limit observations to maximum count.

        Args:
            observations: List of observation dicts

        Returns:
            Truncated list to max_observations length.
        """
        return observations[: self.config.max_observations]

    def limit_by_size(
        self,
        observations: list[dict],
        base_context_size: int = 0,
    ) -> tuple[list[dict], int]:
        """Limit observations to fit within context window.

        Stops adding observations when total size would exceed
        (max_context_chars - leave_context_headroom).

        Args:
            observations: List of observation dicts
            base_context_size: Existing context size (e.g., prompt size)

        Returns:
            (filtered_observations, total_size) tuple
        """
        max_size = self.config.max_context_chars - self.config.leave_context_headroom

        result = []
        total_size = base_context_size

        for obs in observations:
            # Estimate size: title + content + metadata
            title = obs.get("title", "")
            content = obs.get("content", "")
            est_size = len(title) + len(content) + 100

            if total_size + est_size <= max_size:
                result.append(obs)
                total_size += est_size
            else:
                break

        return result, total_size

    def tune_observations(
        self,
        observations: list[dict],
        base_context_size: int = 0,
    ) -> tuple[list[dict], dict]:
        """Apply all tuning rules to observations.

        Pipeline:
        1. Filter by relevance score
        2. Limit by count
        3. Limit by context size

        Args:
            observations: Raw observations from retrieve
            base_context_size: Existing context size

        Returns:
            (tuned_observations, stats) tuple with tuning metrics.
        """
        original_count = len(observations)

        # Step 1: Relevance filter
        by_score = self.filter_by_relevance(observations)
        score_filtered = original_count - len(by_score)

        # Step 2: Count limit
        by_count = self.limit_by_count(by_score)
        count_limited = len(by_score) - len(by_count)

        # Step 3: Size limit
        by_size, total_size = self.limit_by_size(by_count, base_context_size)
        size_limited = len(by_count) - len(by_size)

        stats = {
            "original_count": original_count,
            "after_relevance_filter": len(by_score),
            "relevance_filtered_out": score_filtered,
            "after_count_limit": len(by_count),
            "count_limited_out": count_limited,
            "final_count": len(by_size),
            "size_limited_out": size_limited,
            "total_context_size": total_size,
            "context_limit": self.config.max_context_chars,
        }

        return by_size, stats

    def get_config_summary(self) -> str:
        """Return human-readable config summary.

        Returns:
            Formatted config string for logging/debugging.
        """
        cfg = self.config
        return (
            f"MemoryTuner: "
            f"min_score={cfg.min_relevance_score}, "
            f"max_obs={cfg.max_observations}, "
            f"max_context={cfg.max_context_chars}, "
            f"headroom={cfg.leave_context_headroom}"
        )
