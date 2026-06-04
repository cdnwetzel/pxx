from dataclasses import dataclass, field
from threading import Lock
from typing import Optional


@dataclass
class RouterMetrics:
    """Track request metrics for 9router."""

    active_requests: int = 0
    total_requests: int = 0
    total_errors: int = 0
    latencies: list[float] = field(default_factory=list)
    total_tokens: int = 0
    cached_tokens: int = 0
    _lock: Lock = field(default_factory=Lock)

    @property
    def latency_p99(self) -> Optional[float]:
        """99th percentile latency in ms."""
        with self._lock:
            if not self.latencies:
                return None
            sorted_latencies = sorted(self.latencies)
            idx = int(len(sorted_latencies) * 0.99)
            return sorted_latencies[idx] * 1000.0

    @property
    def error_rate(self) -> float:
        """Error rate as fraction."""
        if self.total_requests == 0:
            return 0.0
        return self.total_errors / self.total_requests

    @property
    def compression_ratio(self) -> float:
        """Tokens saved / total tokens."""
        if self.total_tokens == 0:
            return 0.0
        return self.cached_tokens / self.total_tokens

    def record_request_start(self) -> None:
        """Increment active request count."""
        with self._lock:
            self.active_requests += 1
            self.total_requests += 1

    def record_request_end(self, elapsed_sec: float, error: bool = False) -> None:
        """Record request completion."""
        with self._lock:
            self.active_requests = max(0, self.active_requests - 1)
            self.latencies.append(elapsed_sec)
            if error:
                self.total_errors += 1
            # Keep only last 1000 latencies to avoid unbounded memory
            if len(self.latencies) > 1000:
                self.latencies = self.latencies[-1000:]

    def record_tokens(self, total: int, cached: int) -> None:
        """Record token usage."""
        with self._lock:
            self.total_tokens += total
            self.cached_tokens += cached

    def to_dict(self) -> dict:
        """Export metrics as dict."""
        with self._lock:
            return {
                "active_requests": self.active_requests,
                "total_requests": self.total_requests,
                "total_errors": self.total_errors,
                "error_rate": self.error_rate,
                "latency_p99_ms": self.latency_p99,
                "total_tokens": self.total_tokens,
                "cached_tokens": self.cached_tokens,
                "compression_ratio": self.compression_ratio,
            }


# Global metrics instance
metrics = RouterMetrics()
