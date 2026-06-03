"""Memory analytics and usage tracking (Phase 5 Tier 3).

Tracks retrieval events, injection events, and slash command usage
to provide insights into memory effectiveness and cold observation detection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TypedDict


class RetrievalEvent(TypedDict, total=False):
    """Record of a memory retrieval operation."""

    timestamp: str
    query: str
    result_count: int
    avg_score: float
    observations: list[str]


class InjectionEvent(TypedDict, total=False):
    """Record of a memory injection operation."""

    timestamp: str
    context_size: int
    observation_count: int
    aider_interaction: bool
    injected_ids: list[str]


class CommandEvent(TypedDict, total=False):
    """Record of a slash command execution."""

    timestamp: str
    command: str
    success: bool
    result_count: int


@dataclass
class ObservationAccess:
    """Track access patterns for a single observation."""

    obs_id: str
    access_count: int = 0
    last_access: str | None = None
    first_access: str | None = None
    contexts: list[str] = field(default_factory=list)

    def record_access(self) -> None:
        """Record an access event."""
        now = datetime.now().isoformat()
        self.access_count += 1
        self.last_access = now
        if not self.first_access:
            self.first_access = now


class MemoryAnalytics:
    """Track memory usage and provide analytics."""

    def __init__(self) -> None:
        """Initialize analytics tracker."""
        self.retrieval_events: list[RetrievalEvent] = []
        self.injection_events: list[InjectionEvent] = []
        self.command_events: list[CommandEvent] = []
        self.observation_access: dict[str, ObservationAccess] = {}

    def record_retrieval(
        self,
        query: str,
        observations: list[dict],
        avg_score: float | None = None,
    ) -> None:
        """Record a memory retrieval event.

        Args:
            query: Search query
            observations: Retrieved observations list
            avg_score: Average relevance score of results
        """
        obs_ids = [obs.get("id", f"obs-{i}") for i, obs in enumerate(observations)]

        if avg_score is None and observations:
            scores = [obs.get("score", 0) for obs in observations]
            avg_score = sum(scores) / len(scores) if scores else 0

        event: RetrievalEvent = {
            "timestamp": datetime.now().isoformat(),
            "query": query,
            "result_count": len(observations),
            "avg_score": avg_score or 0,
            "observations": obs_ids,
        }

        self.retrieval_events.append(event)

        # Track observation access
        for obs_id in obs_ids:
            if obs_id not in self.observation_access:
                self.observation_access[obs_id] = ObservationAccess(obs_id)
            self.observation_access[obs_id].record_access()

    def record_injection(
        self,
        observations: list[dict],
        context_size: int,
        aider_interaction: bool = True,
    ) -> None:
        """Record a memory injection event.

        Args:
            observations: Injected observations
            context_size: Total injected context size in chars
            aider_interaction: Whether aider used the context
        """
        obs_ids = [obs.get("id", f"obs-{i}") for i, obs in enumerate(observations)]

        event: InjectionEvent = {
            "timestamp": datetime.now().isoformat(),
            "context_size": context_size,
            "observation_count": len(observations),
            "aider_interaction": aider_interaction,
            "injected_ids": obs_ids,
        }

        self.injection_events.append(event)

        # Track observation usage
        for obs_id in obs_ids:
            if obs_id not in self.observation_access:
                self.observation_access[obs_id] = ObservationAccess(obs_id)
            # Injection is a form of usage
            self.observation_access[obs_id].contexts.append("injected")

    def record_command(
        self,
        command: str,
        success: bool,
        result_count: int = 0,
    ) -> None:
        """Record a slash command execution.

        Args:
            command: Command name (recall, remember, forget)
            success: Whether command succeeded
            result_count: Number of results returned (for /recall)
        """
        event: CommandEvent = {
            "timestamp": datetime.now().isoformat(),
            "command": command,
            "success": success,
            "result_count": result_count,
        }
        self.command_events.append(event)

    def most_retrieved_observations(self, limit: int = 10) -> list[tuple[str, int]]:
        """Get observations most frequently retrieved.

        Args:
            limit: Maximum number of results

        Returns:
            List of (obs_id, access_count) tuples, sorted by count descending.
        """
        items = sorted(
            self.observation_access.items(),
            key=lambda x: x[1].access_count,
            reverse=True,
        )
        return [(obs_id, acc.access_count) for obs_id, acc in items[:limit]]

    def cold_observations(
        self,
        days_inactive: int = 7,
        min_age_days: int = 1,
    ) -> list[str]:
        """Get observations not accessed in N days.

        Args:
            days_inactive: Threshold for "cold" (default: 7 days)
            min_age_days: Minimum age to consider cold (default: 1 day)

        Returns:
            List of observation IDs unused for days_inactive.
        """
        now = datetime.now()
        threshold = now - timedelta(days=days_inactive)
        min_created = now - timedelta(days=min_age_days)

        cold = []
        for obs_id, access in self.observation_access.items():
            if not access.last_access:
                # Never accessed
                if access.first_access:
                    created = datetime.fromisoformat(access.first_access)
                    if created < min_created:
                        cold.append(obs_id)
            else:
                # Check last access
                last = datetime.fromisoformat(access.last_access)
                if last < threshold:
                    cold.append(obs_id)

        return cold

    def retrieval_stats(self) -> dict:
        """Get retrieval operation statistics.

        Returns:
            Dict with retrieval stats (count, avg results, etc).
        """
        if not self.retrieval_events:
            return {
                "total_retrievals": 0,
                "avg_results_per_retrieval": 0,
                "avg_relevance_score": 0,
            }

        events = self.retrieval_events
        total = len(events)
        avg_results = sum(e.get("result_count", 0) for e in events) / total
        avg_score = sum(e.get("avg_score", 0) for e in events) / total

        return {
            "total_retrievals": total,
            "avg_results_per_retrieval": avg_results,
            "avg_relevance_score": avg_score,
        }

    def injection_stats(self) -> dict:
        """Get injection operation statistics.

        Returns:
            Dict with injection stats (count, avg size, etc).
        """
        if not self.injection_events:
            return {
                "total_injections": 0,
                "avg_context_size": 0,
                "avg_observations_injected": 0,
                "total_chars_injected": 0,
            }

        events = self.injection_events
        total = len(events)
        avg_size = sum(e.get("context_size", 0) for e in events) / total
        avg_obs = sum(e.get("observation_count", 0) for e in events) / total
        total_chars = sum(e.get("context_size", 0) for e in events)

        return {
            "total_injections": total,
            "avg_context_size": avg_size,
            "avg_observations_injected": avg_obs,
            "total_chars_injected": total_chars,
        }

    def command_stats(self) -> dict:
        """Get slash command statistics.

        Returns:
            Dict with command stats grouped by command type.
        """
        if not self.command_events:
            return {}

        stats: dict[str, dict] = {}
        for event in self.command_events:
            cmd = event.get("command", "unknown")
            if cmd not in stats:
                stats[cmd] = {
                    "total": 0,
                    "successful": 0,
                    "failed": 0,
                    "avg_results": 0,
                }

            stats[cmd]["total"] += 1
            if event.get("success"):
                stats[cmd]["successful"] += 1
            else:
                stats[cmd]["failed"] += 1

        return stats

    def memory_contribution(self, total_context_size: int = 100000) -> float:
        """Calculate memory's contribution to context as percentage.

        Args:
            total_context_size: Total context window size (default: 100k)

        Returns:
            Percentage (0-100) of context used by memory.
        """
        inj_stats = self.injection_stats()
        total_chars = inj_stats.get("total_chars_injected", 0)
        return (total_chars / total_context_size * 100) if total_context_size else 0

    def get_summary(self) -> str:
        """Get human-readable analytics summary.

        Returns:
            Formatted summary string for logging.
        """
        retr = self.retrieval_stats()
        inj = self.injection_stats()
        cmd = self.command_stats()

        avg_retr_results = retr.get("avg_results_per_retrieval", 0)
        avg_inj_size = inj.get("avg_context_size", 0)

        lines = [
            "=== Memory Analytics Summary ===",
            f"Retrievals: {retr.get('total_retrievals', 0)} "
            f"(avg {avg_retr_results:.1f} results)",
            f"Injections: {inj.get('total_injections', 0)} "
            f"(avg {avg_inj_size:.0f} chars)",
            f"Commands: {len(cmd)} types",
            f"Observations tracked: {len(self.observation_access)}",
        ]

        return "\n".join(lines)
