"""Tests for cost and resource metrics (Phase 5 Tier 4)."""

from __future__ import annotations

from datetime import datetime, timedelta

from pxx.cost_metrics import CostMetrics, TokenMetrics


class TestTokenMetrics:
    """Tests for token usage metrics."""

    def test_token_metrics_creation(self) -> None:
        """Test creating token metrics."""
        metrics = TokenMetrics(
            session_id="test-session",
            prompt_tokens=10000,
            completion_tokens=500,
            total_tokens=10500,
            cached_tokens=8000,
            cache_creation_tokens=0,
        )

        assert metrics.prompt_tokens == 10000
        assert metrics.total_tokens == 10500

    def test_cache_hit_rate(self) -> None:
        """Test cache hit rate calculation."""
        metrics = TokenMetrics(
            session_id="test",
            prompt_tokens=10000,
            completion_tokens=500,
            total_tokens=10500,
            cached_tokens=8000,
        )

        hit_rate = metrics.cache_hit_rate
        assert abs(hit_rate - 0.762) < 0.01  # 8000/10500 ≈ 0.762

    def test_cache_hit_rate_zero(self) -> None:
        """Test cache hit rate when no cache."""
        metrics = TokenMetrics(
            session_id="test",
            prompt_tokens=10000,
            completion_tokens=500,
            total_tokens=10500,
            cached_tokens=0,
        )

        assert metrics.cache_hit_rate == 0.0

    def test_cache_hit_rate_no_tokens(self) -> None:
        """Test cache hit rate with no tokens (avoid division by zero)."""
        metrics = TokenMetrics(session_id="test")

        assert metrics.cache_hit_rate == 0.0

    def test_effective_tokens_with_cache(self) -> None:
        """Test effective token count with cache discount."""
        metrics = TokenMetrics(
            session_id="test",
            prompt_tokens=10000,
            completion_tokens=500,
            total_tokens=10500,
            cached_tokens=8000,
        )

        # Cached tokens cost ~10% of normal tokens
        # 8000 * 0.1 = 800 (cache cost)
        # (10500 - 8000) + 800 = 2500 + 800 = 3300
        effective = metrics.effective_tokens
        assert effective == 3300

    def test_token_metrics_to_dict(self) -> None:
        """Test token metrics serialization."""
        metrics = TokenMetrics(
            session_id="test",
            prompt_tokens=10000,
            completion_tokens=500,
            total_tokens=10500,
            cached_tokens=8000,
        )

        data = metrics.to_dict()
        assert data["session_id"] == "test"
        assert data["total_tokens"] == 10500
        assert "cache_hit_rate" in data
        assert "effective_tokens" in data


class TestCostMetrics:
    """Tests for session cost metrics."""

    def test_cost_metrics_creation(self) -> None:
        """Test creating cost metrics."""
        start = datetime.now().isoformat()
        metrics = CostMetrics(
            session_id="test-session",
            start_time=start,
            memory_observations_count=100,
            memory_total_mb=5.0,
        )

        assert metrics.session_id == "test-session"
        assert metrics.memory_observations_count == 100

    def test_calculate_estimated_cost(self) -> None:
        """Test estimated cost calculation."""
        metrics = CostMetrics(
            session_id="test",
            start_time=datetime.now().isoformat(),
            tokens=TokenMetrics(
                session_id="test",
                prompt_tokens=10000,
                completion_tokens=1000,
                total_tokens=11000,
                cached_tokens=0,
            ),
        )

        cost = metrics.calculate_estimated_cost(
            prompt_cost_per_1k=0.003,
            completion_cost_per_1k=0.012,
        )

        # 10000 * 0.003 / 1000 = 0.03
        # 1000 * 0.012 / 1000 = 0.012
        # Total = 0.042
        assert abs(cost - 0.042) < 0.001

    def test_estimated_cost_with_cache(self) -> None:
        """Test estimated cost with cache discount."""
        metrics = CostMetrics(
            session_id="test",
            start_time=datetime.now().isoformat(),
            tokens=TokenMetrics(
                session_id="test",
                prompt_tokens=10000,
                completion_tokens=500,
                total_tokens=10500,
                cached_tokens=8000,
            ),
        )

        cost = metrics.calculate_estimated_cost(
            prompt_cost_per_1k=0.003,
            completion_cost_per_1k=0.012,
        )

        # Without cache: (10000 * 0.003) + (500 * 0.012) / 1000 = 0.036
        # Cache discount: 8000 * 0.003 * 0.9 / 1000 = 0.0216
        # With cache: 0.036 - 0.0216 = 0.0144
        # Plus completion: 0.0144 + (500 * 0.012 / 1000) = 0.0144 + 0.006 = 0.0204
        assert cost < 0.025  # Should be significantly lower than uncached

    def test_cost_never_negative(self) -> None:
        """Test that cost calculation never returns negative."""
        metrics = CostMetrics(
            session_id="test",
            start_time=datetime.now().isoformat(),
            tokens=TokenMetrics(
                session_id="test",
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                cached_tokens=0,
            ),
        )

        cost = metrics.calculate_estimated_cost()
        assert cost >= 0.0

    def test_cost_metrics_to_dict(self) -> None:
        """Test cost metrics serialization."""
        start = datetime.now().isoformat()
        end = (datetime.now() + timedelta(seconds=30)).isoformat()

        metrics = CostMetrics(
            session_id="test",
            start_time=start,
            end_time=end,
            tokens=TokenMetrics(
                session_id="test",
                prompt_tokens=1000,
                completion_tokens=100,
                total_tokens=1100,
            ),
            memory_observations_count=50,
            memory_total_mb=3.0,
            router_requests_count=5,
            router_avg_latency_ms=100.0,
        )

        data = metrics.to_dict()
        assert data["session_id"] == "test"
        assert "duration" in data
        assert data["memory"]["observations_count"] == 50
        assert data["router"]["requests_count"] == 5
        assert "estimated_cost_usd" in data

    def test_cost_summary_string(self) -> None:
        """Test cost summary string generation."""
        metrics = CostMetrics(
            session_id="test",
            start_time=datetime.now().isoformat(),
            tokens=TokenMetrics(
                session_id="test",
                prompt_tokens=10000,
                completion_tokens=1000,
                total_tokens=11000,
                cached_tokens=5000,
            ),
            memory_observations_count=100,
            memory_total_mb=5.0,
            router_requests_count=10,
            router_avg_latency_ms=150.0,
            estimated_cost_usd=0.05,
        )

        summary = metrics.get_summary()
        assert "Session Cost Summary" in summary
        assert "11000" in summary
        assert "100 observations" in summary
        assert "10 requests" in summary
        assert "0.05" in summary

    def test_cost_summary_no_cache(self) -> None:
        """Test cost summary when no cache is used."""
        metrics = CostMetrics(
            session_id="test",
            start_time=datetime.now().isoformat(),
            tokens=TokenMetrics(
                session_id="test",
                prompt_tokens=1000,
                completion_tokens=100,
                total_tokens=1100,
                cached_tokens=0,
            ),
        )

        summary = metrics.get_summary()
        assert "Session Cost Summary" in summary
        assert "1100" in summary
        # Cache section should not appear if no cached tokens
        assert "Cache:" not in summary or "0 tokens" in summary
