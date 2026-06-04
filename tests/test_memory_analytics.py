"""Tests for memory analytics (Phase 5 Tier 3)."""

from __future__ import annotations

from datetime import datetime, timedelta

from pxx.memory_analytics import MemoryAnalytics, ObservationAccess


class TestObservationAccess:
    """Tests for ObservationAccess tracking."""

    def test_record_access_increments_count(self) -> None:
        """Test recording access increments counter."""
        access = ObservationAccess("obs-1")
        assert access.access_count == 0

        access.record_access()
        assert access.access_count == 1

        access.record_access()
        assert access.access_count == 2

    def test_record_access_sets_first_last(self) -> None:
        """Test recording access sets first/last timestamps."""
        access = ObservationAccess("obs-1")
        assert access.first_access is None
        assert access.last_access is None

        access.record_access()
        assert access.first_access is not None
        assert access.last_access is not None

        # First access should not change on second call
        first = access.first_access
        access.record_access()
        assert access.first_access == first  # Same
        assert access.last_access > first  # Last updated


class TestRetrievalTracking:
    """Tests for retrieval event tracking."""

    def test_record_retrieval_basic(self) -> None:
        """Test recording a retrieval event."""
        analytics = MemoryAnalytics()
        obs = [
            {"id": "obs-1", "score": 0.9},
            {"id": "obs-2", "score": 0.8},
        ]
        analytics.record_retrieval("test query", obs)

        assert len(analytics.retrieval_events) == 1
        event = analytics.retrieval_events[0]
        assert event["query"] == "test query"
        assert event["result_count"] == 2
        assert event["observations"] == ["obs-1", "obs-2"]

    def test_record_retrieval_computes_avg_score(self) -> None:
        """Test average score computation."""
        analytics = MemoryAnalytics()
        obs = [
            {"id": "obs-1", "score": 0.8},
            {"id": "obs-2", "score": 0.6},
        ]
        analytics.record_retrieval("query", obs)

        event = analytics.retrieval_events[0]
        assert event["avg_score"] == 0.7

    def test_record_retrieval_tracks_observations(self) -> None:
        """Test observation access tracking."""
        analytics = MemoryAnalytics()
        obs = [{"id": "obs-1", "score": 0.9}]
        analytics.record_retrieval("query", obs)

        assert "obs-1" in analytics.observation_access
        assert analytics.observation_access["obs-1"].access_count == 1


class TestInjectionTracking:
    """Tests for injection event tracking."""

    def test_record_injection_basic(self) -> None:
        """Test recording an injection event."""
        analytics = MemoryAnalytics()
        obs = [
            {"id": "obs-1"},
            {"id": "obs-2"},
        ]
        analytics.record_injection(obs, context_size=500)

        assert len(analytics.injection_events) == 1
        event = analytics.injection_events[0]
        assert event["observation_count"] == 2
        assert event["context_size"] == 500

    def test_record_injection_tracks_observations(self) -> None:
        """Test observation tracking in injections."""
        analytics = MemoryAnalytics()
        obs = [{"id": "obs-1"}]
        analytics.record_injection(obs, context_size=100)

        assert "obs-1" in analytics.observation_access
        assert "injected" in analytics.observation_access["obs-1"].contexts


class TestCommandTracking:
    """Tests for slash command tracking."""

    def test_record_command_success(self) -> None:
        """Test recording successful command."""
        analytics = MemoryAnalytics()
        analytics.record_command("recall", True, result_count=3)

        assert len(analytics.command_events) == 1
        event = analytics.command_events[0]
        assert event["command"] == "recall"
        assert event["success"]
        assert event["result_count"] == 3

    def test_record_command_failure(self) -> None:
        """Test recording failed command."""
        analytics = MemoryAnalytics()
        analytics.record_command("remember", False, result_count=0)

        event = analytics.command_events[0]
        assert not event["success"]


class TestMostRetrievedObservations:
    """Tests for most-retrieved observations query."""

    def test_most_retrieved_observations(self) -> None:
        """Test ranking observations by access count."""
        analytics = MemoryAnalytics()

        # Record retrievals with different observations
        analytics.record_retrieval("q1", [{"id": "obs-1"}])
        analytics.record_retrieval("q2", [{"id": "obs-1"}])  # obs-1 twice
        analytics.record_retrieval("q3", [{"id": "obs-2"}])

        result = analytics.most_retrieved_observations(limit=10)

        # obs-1 should be first (2 accesses), obs-2 second (1 access)
        assert result[0] == ("obs-1", 2)
        assert result[1] == ("obs-2", 1)

    def test_most_retrieved_observations_respects_limit(self) -> None:
        """Test limit parameter."""
        analytics = MemoryAnalytics()

        for i in range(5):
            analytics.record_retrieval(f"q{i}", [{"id": f"obs-{i}"}])

        result = analytics.most_retrieved_observations(limit=2)
        assert len(result) == 2


class TestColdObservations:
    """Tests for cold observation detection."""

    def test_cold_observations_never_accessed(self) -> None:
        """Test detecting never-accessed observations."""
        analytics = MemoryAnalytics()

        # Create observations without accessing them
        access1 = ObservationAccess("obs-1")
        access1.first_access = (datetime.now() - timedelta(days=10)).isoformat()
        analytics.observation_access["obs-1"] = access1

        cold = analytics.cold_observations(days_inactive=7, min_age_days=1)
        assert "obs-1" in cold

    def test_cold_observations_respects_threshold(self) -> None:
        """Test cold threshold."""
        analytics = MemoryAnalytics()

        # Create observation accessed long ago
        access = ObservationAccess("obs-1")
        access.first_access = (datetime.now() - timedelta(days=3)).isoformat()
        access.last_access = (datetime.now() - timedelta(days=10)).isoformat()
        analytics.observation_access["obs-1"] = access

        # Should be cold with 7-day threshold
        cold = analytics.cold_observations(days_inactive=7)
        assert "obs-1" in cold

        # Should not be cold with 15-day threshold
        cold = analytics.cold_observations(days_inactive=15)
        assert "obs-1" not in cold


class TestStatistics:
    """Tests for statistics generation."""

    def test_retrieval_stats(self) -> None:
        """Test retrieval statistics."""
        analytics = MemoryAnalytics()
        analytics.record_retrieval(
            "q1",
            [
                {"id": "obs-1", "score": 0.9},
                {"id": "obs-2", "score": 0.8},
            ],
        )
        analytics.record_retrieval("q2", [{"id": "obs-3", "score": 0.7}])

        stats = analytics.retrieval_stats()
        assert stats["total_retrievals"] == 2
        assert stats["avg_results_per_retrieval"] == 1.5
        assert abs(stats["avg_relevance_score"] - 0.775) < 0.001

    def test_injection_stats(self) -> None:
        """Test injection statistics."""
        analytics = MemoryAnalytics()
        analytics.record_injection([{"id": "obs-1"}], context_size=1000)
        analytics.record_injection(
            [{"id": "obs-2"}, {"id": "obs-3"}], context_size=2000
        )

        stats = analytics.injection_stats()
        assert stats["total_injections"] == 2
        assert stats["avg_context_size"] == 1500
        assert stats["avg_observations_injected"] == 1.5
        assert stats["total_chars_injected"] == 3000

    def test_command_stats(self) -> None:
        """Test command statistics."""
        analytics = MemoryAnalytics()
        analytics.record_command("recall", True, result_count=5)
        analytics.record_command("recall", True, result_count=3)
        analytics.record_command("recall", False, result_count=0)
        analytics.record_command("remember", True, result_count=0)

        stats = analytics.command_stats()
        assert stats["recall"]["total"] == 3
        assert stats["recall"]["successful"] == 2
        assert stats["recall"]["failed"] == 1
        assert stats["remember"]["total"] == 1


class TestMemoryContribution:
    """Tests for memory contribution calculation."""

    def test_memory_contribution_percentage(self) -> None:
        """Test memory contribution calculation."""
        analytics = MemoryAnalytics()
        analytics.record_injection([{"id": "obs-1"}], context_size=1000)
        analytics.record_injection([{"id": "obs-2"}], context_size=1000)

        # 2000 chars out of 10000 = 20%
        contrib = analytics.memory_contribution(total_context_size=10000)
        assert contrib == 20.0

    def test_memory_contribution_zero_size(self) -> None:
        """Test with zero total context size."""
        analytics = MemoryAnalytics()
        contrib = analytics.memory_contribution(total_context_size=0)
        assert contrib == 0


class TestSummary:
    """Tests for summary generation."""

    def test_get_summary_no_data(self) -> None:
        """Test summary with no data."""
        analytics = MemoryAnalytics()
        summary = analytics.get_summary()

        assert "Memory Analytics Summary" in summary
        assert "Retrievals: 0" in summary

    def test_get_summary_with_data(self) -> None:
        """Test summary with data."""
        analytics = MemoryAnalytics()
        analytics.record_retrieval("query", [{"id": "obs-1", "score": 0.9}])
        analytics.record_injection([{"id": "obs-1"}], context_size=500)
        analytics.record_command("recall", True, result_count=1)

        summary = analytics.get_summary()

        assert "Retrievals: 1" in summary
        assert "Injections: 1" in summary
        assert "Observations tracked:" in summary
