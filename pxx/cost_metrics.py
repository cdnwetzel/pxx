"""Cost and resource metrics for aider sessions (Phase 5 Tier 4).

Tracks usage metrics including token counts, router stats, and memory usage
to provide cost insights and optimization recommendations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class TokenMetrics:
    """Metrics for token usage in a session."""

    session_id: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0
    cache_creation_tokens: int = 0

    @property
    def cache_hit_rate(self) -> float:
        """Calculate cache hit rate as percentage of cached vs total tokens.

        Returns:
            Float 0.0-1.0 representing cache hit rate.
        """
        if self.total_tokens == 0:
            return 0.0
        return self.cached_tokens / self.total_tokens

    @property
    def effective_tokens(self) -> int:
        """Calculate effective tokens accounting for cache hits.

        Cached tokens cost ~10% of normal tokens (prompt caching discount).

        Returns:
            Adjusted token count with cache cost reduction.
        """
        cached_cost = int(self.cached_tokens * 0.1)
        regular_cost = self.total_tokens - self.cached_tokens
        return cached_cost + regular_cost

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for logging.

        Returns:
            Dict representation of token metrics.
        """
        return {
            "session_id": self.session_id,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cached_tokens": self.cached_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "cache_hit_rate": self.cache_hit_rate,
            "effective_tokens": self.effective_tokens,
        }


@dataclass
class CostMetrics:
    """Session cost and resource metrics."""

    session_id: str
    start_time: str
    end_time: str | None = None
    tokens: TokenMetrics = field(default_factory=lambda: TokenMetrics(""))
    memory_observations_count: int = 0
    memory_total_mb: float = 0.0
    router_requests_count: int = 0
    router_avg_latency_ms: float = 0.0
    estimated_cost_usd: float = 0.0

    def calculate_estimated_cost(
        self,
        prompt_cost_per_1k: float = 0.003,
        completion_cost_per_1k: float = 0.012,
    ) -> float:
        """Calculate estimated cost based on token usage.

        Costs are for Claude models (approximate). Adjust for actual pricing.

        Args:
            prompt_cost_per_1k: Cost per 1k prompt tokens
            completion_cost_per_1k: Cost per 1k completion tokens

        Returns:
            Estimated cost in USD.
        """
        prompt_cost = (self.tokens.prompt_tokens / 1000) * prompt_cost_per_1k
        completion_cost = (
            self.tokens.completion_tokens / 1000
        ) * completion_cost_per_1k

        # Apply cache discount if available
        if self.tokens.cached_tokens > 0:
            cache_discount = (
                (self.tokens.cached_tokens / 1000) * prompt_cost_per_1k * 0.9
            )
            prompt_cost -= cache_discount

        return max(0.0, prompt_cost + completion_cost)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for logging.

        Returns:
            Dict representation of cost metrics.
        """
        duration_str = "unknown"
        if self.end_time:
            try:
                start = datetime.fromisoformat(self.start_time)
                end = datetime.fromisoformat(self.end_time)
                duration = (end - start).total_seconds()
                duration_str = f"{duration:.1f}s"
            except ValueError:
                pass

        return {
            "session_id": self.session_id,
            "duration": duration_str,
            "tokens": self.tokens.to_dict(),
            "memory": {
                "observations_count": self.memory_observations_count,
                "total_mb": self.memory_total_mb,
            },
            "router": {
                "requests_count": self.router_requests_count,
                "avg_latency_ms": self.router_avg_latency_ms,
            },
            "estimated_cost_usd": round(self.estimated_cost_usd, 4),
        }

    def get_summary(self) -> str:
        """Get human-readable cost summary.

        Returns:
            Formatted summary string for logging.
        """
        lines = [
            "=== Session Cost Summary ===",
            f"Tokens: {self.tokens.total_tokens} total "
            f"({self.tokens.prompt_tokens} prompt, {self.tokens.completion_tokens} completion)",
        ]

        if self.tokens.cached_tokens > 0:
            lines.append(
                f"Cache: {self.tokens.cached_tokens} tokens cached "
                f"({self.tokens.cache_hit_rate * 100:.1f}% hit rate)"
            )

        lines.append(
            f"Memory: {self.memory_observations_count} observations, {self.memory_total_mb:.1f}MB"
        )

        if self.router_requests_count > 0:
            lines.append(
                f"Router: {self.router_requests_count} requests, "
                f"avg {self.router_avg_latency_ms:.0f}ms latency"
            )

        lines.append(f"Estimated cost: ${self.estimated_cost_usd:.4f}")

        return "\n".join(lines)
