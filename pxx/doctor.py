"""System health check and diagnostics (Phase 5 Tier 4).

Extends scripts/doctor.sh with runtime diagnostics for router, memory, and aider.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime

import requests


@dataclass
class RouterStats:
    """Statistics from the 9router."""

    available: bool
    endpoint: str | None
    active_requests: int | None
    latency_p99: float | None
    error_rate: float | None

    def __str__(self) -> str:
        """Format router stats for display."""
        if not self.available:
            return "  9router: unreachable"

        status = "OK"
        if (self.error_rate or 0) > 0.05:
            status = "⚠️  HIGH ERROR RATE"

        parts = [f"  9router ({self.endpoint}): {status}"]
        if self.active_requests is not None:
            parts.append(f"active_requests={self.active_requests}")
        if self.latency_p99 is not None:
            parts.append(f"p99={self.latency_p99:.0f}ms")
        if self.error_rate is not None:
            parts.append(f"error_rate={self.error_rate * 100:.1f}%")

        return " | ".join(parts) if len(parts) > 1 else parts[0]


@dataclass
class MemoryStats:
    """Statistics from agentmemory server."""

    available: bool
    endpoint: str | None
    observation_count: int | None
    total_size_mb: float | None
    hit_rate: float | None
    avg_retrieval_ms: float | None

    def __str__(self) -> str:
        """Format memory stats for display."""
        if not self.available:
            return "  agentmemory: unreachable"

        status = "OK"
        if (self.hit_rate or 0) < 0.3:
            status = "⚠️  LOW HIT RATE"

        parts = [f"  agentmemory ({self.endpoint}): {status}"]
        if self.observation_count is not None:
            parts.append(f"observations={self.observation_count}")
        if self.total_size_mb is not None:
            parts.append(f"size={self.total_size_mb:.1f}MB")
        if self.hit_rate is not None:
            parts.append(f"hit_rate={self.hit_rate * 100:.1f}%")
        if self.avg_retrieval_ms is not None:
            parts.append(f"retrieval={self.avg_retrieval_ms:.0f}ms")

        return " | ".join(parts) if len(parts) > 1 else parts[0]


class Doctor:
    """System health diagnostics."""

    def __init__(
        self,
        router_api: str | None = None,
        memory_api: str | None = None,
    ):
        """Initialize doctor.

        Args:
            router_api: 9router API base URL. Defaults to env var PXX_ROUTER_API
            memory_api: agentmemory API base URL. Defaults to env var PXX_MEMORY_API
        """
        self.router_api = router_api or os.getenv("PXX_ROUTER_API")
        self.memory_api = memory_api or os.getenv("PXX_MEMORY_API")

    def check_router(self) -> RouterStats:
        """Check 9router health.

        Returns:
            RouterStats with availability and metrics.
        """
        if not self.router_api:
            return RouterStats(
                available=False,
                endpoint=None,
                active_requests=None,
                latency_p99=None,
                error_rate=None,
            )

        try:
            resp = requests.get(
                f"{self.router_api}/health",
                timeout=2,
            )
            if resp.status_code != 200:
                return RouterStats(
                    available=False,
                    endpoint=self.router_api,
                    active_requests=None,
                    latency_p99=None,
                    error_rate=None,
                )

            data = resp.json()
            return RouterStats(
                available=True,
                endpoint=self.router_api,
                active_requests=data.get("active_requests"),
                latency_p99=data.get("latency_p99_ms"),
                error_rate=data.get("error_rate"),
            )
        except (requests.RequestException, ValueError):
            return RouterStats(
                available=False,
                endpoint=self.router_api,
                active_requests=None,
                latency_p99=None,
                error_rate=None,
            )

    def check_memory(self) -> MemoryStats:
        """Check agentmemory health.

        Returns:
            MemoryStats with availability and metrics.
        """
        if not self.memory_api:
            return MemoryStats(
                available=False,
                endpoint=None,
                observation_count=None,
                total_size_mb=None,
                hit_rate=None,
                avg_retrieval_ms=None,
            )

        try:
            resp = requests.get(
                f"{self.memory_api}/health",
                timeout=2,
            )
            if resp.status_code != 200:
                return MemoryStats(
                    available=False,
                    endpoint=self.memory_api,
                    observation_count=None,
                    total_size_mb=None,
                    hit_rate=None,
                    avg_retrieval_ms=None,
                )

            data = resp.json()
            size_mb = data.get("total_size_bytes", 0) / (1024 * 1024)
            return MemoryStats(
                available=True,
                endpoint=self.memory_api,
                observation_count=data.get("observation_count"),
                total_size_mb=size_mb,
                hit_rate=data.get("hit_rate"),
                avg_retrieval_ms=data.get("avg_retrieval_ms"),
            )
        except (requests.RequestException, ValueError):
            return MemoryStats(
                available=False,
                endpoint=self.memory_api,
                observation_count=None,
                total_size_mb=None,
                hit_rate=None,
                avg_retrieval_ms=None,
            )

    def print_report(self) -> None:
        """Print health check report to stdout."""
        print("\n=== pxx doctor (extended) ===\n")

        print("Routing & Memory:")
        router_stats = self.check_router()
        print(str(router_stats))

        memory_stats = self.check_memory()
        print(str(memory_stats))

        print("\n=== Run full doctor ===")
        print("  bash scripts/doctor.sh  (Ollama, endpoints, drift)")

    def get_summary(self) -> dict:
        """Get diagnostics summary as dict.

        Returns:
            Dict with router and memory stats for logging.
        """
        router = self.check_router()
        memory = self.check_memory()

        return {
            "timestamp": datetime.now().isoformat(),
            "router": {
                "available": router.available,
                "endpoint": router.endpoint,
                "active_requests": router.active_requests,
                "latency_p99_ms": router.latency_p99,
                "error_rate": router.error_rate,
            },
            "memory": {
                "available": memory.available,
                "endpoint": memory.endpoint,
                "observation_count": memory.observation_count,
                "total_size_mb": memory.total_size_mb,
                "hit_rate": memory.hit_rate,
                "avg_retrieval_ms": memory.avg_retrieval_ms,
            },
        }
