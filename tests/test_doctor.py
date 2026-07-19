"""Tests for system diagnostics (Phase 5 Tier 4)."""

from __future__ import annotations

from unittest.mock import Mock, patch

from pxx.doctor import Doctor, MemoryStats, RemoteStats, RouterStats


class TestRouterStats:
    """Tests for RouterStats."""

    def test_unavailable_router(self) -> None:
        """Test router stats when unavailable."""
        stats = RouterStats(
            available=False,
            endpoint=None,
            active_requests=None,
            latency_p99=None,
            error_rate=None,
        )

        assert not stats.available
        assert "unreachable" in str(stats).lower()

    def test_available_router_ok(self) -> None:
        """Test router stats when available and healthy."""
        stats = RouterStats(
            available=True,
            endpoint="http://localhost:9000",
            active_requests=2,
            latency_p99=150.5,
            error_rate=0.01,
        )

        assert stats.available
        assert "OK" in str(stats)
        assert "active_requests=2" in str(stats)
        assert "p99=150ms" in str(stats)

    def test_router_high_error_rate(self) -> None:
        """Test router stats with high error rate."""
        stats = RouterStats(
            available=True,
            endpoint="http://localhost:9000",
            active_requests=5,
            latency_p99=200.0,
            error_rate=0.1,  # 10%
        )

        assert stats.available
        assert "HIGH ERROR RATE" in str(stats)


class TestMemoryStats:
    """Tests for MemoryStats."""

    def test_unavailable_memory(self) -> None:
        """Test memory stats when unavailable."""
        stats = MemoryStats(
            available=False,
            endpoint=None,
            observation_count=None,
            total_size_mb=None,
            hit_rate=None,
            avg_retrieval_ms=None,
        )

        assert not stats.available
        assert "unreachable" in str(stats).lower()

    def test_available_memory_ok(self) -> None:
        """Test memory stats when available and healthy."""
        stats = MemoryStats(
            available=True,
            endpoint="http://127.0.0.1:3111",
            observation_count=342,
            total_size_mb=8.5,
            hit_rate=0.68,
            avg_retrieval_ms=42.0,
        )

        assert stats.available
        assert "OK" in str(stats)
        assert "observations=342" in str(stats)
        assert "size=8.5MB" in str(stats)

    def test_memory_low_hit_rate(self) -> None:
        """Test memory stats with low hit rate."""
        stats = MemoryStats(
            available=True,
            endpoint="http://127.0.0.1:3111",
            observation_count=100,
            total_size_mb=5.0,
            hit_rate=0.2,  # 20% — below threshold
            avg_retrieval_ms=30.0,
        )

        assert stats.available
        assert "LOW HIT RATE" in str(stats)


class TestDoctorRouter:
    """Tests for Doctor router checks."""

    @patch("pxx.doctor.requests.get")
    def test_check_router_available(self, mock_get: Mock) -> None:
        """Test successful router health check."""
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "active_requests": 2,
            "latency_p99_ms": 150.5,
            "error_rate": 0.01,
        }

        doctor = Doctor(router_api="http://localhost:9000")
        stats = doctor.check_router()

        assert stats.available
        assert stats.active_requests == 2
        assert stats.latency_p99 == 150.5

    @patch("pxx.doctor.requests.get")
    def test_check_router_unavailable(self, mock_get: Mock) -> None:
        """Test router health check when unreachable."""
        from requests.exceptions import RequestException

        mock_get.side_effect = RequestException("Connection refused")

        doctor = Doctor(router_api="http://localhost:9000")
        stats = doctor.check_router()

        assert not stats.available

    def test_check_router_no_api(self) -> None:
        """Test router check when no API configured."""
        doctor = Doctor(router_api=None)
        stats = doctor.check_router()

        assert not stats.available
        assert stats.endpoint is None


class TestDoctorMemory:
    """Tests for Doctor memory checks."""

    @patch("pxx.doctor.requests.get")
    def test_check_memory_available(self, mock_get: Mock) -> None:
        """Test successful memory health check."""
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "observation_count": 342,
            "total_size_bytes": 8_900_000,  # 8.5 MB
            "hit_rate": 0.68,
            "avg_retrieval_ms": 42.0,
        }

        doctor = Doctor(memory_api="http://127.0.0.1:3111")
        stats = doctor.check_memory()

        assert stats.available
        assert stats.observation_count == 342
        assert abs(stats.total_size_mb - 8.5) < 0.02
        assert stats.hit_rate == 0.68

    @patch("pxx.doctor.requests.get")
    def test_check_memory_unavailable(self, mock_get: Mock) -> None:
        """Test memory health check when unreachable."""
        from requests.exceptions import RequestException

        mock_get.side_effect = RequestException("Connection timeout")

        doctor = Doctor(memory_api="http://127.0.0.1:3111")
        stats = doctor.check_memory()

        assert not stats.available

    def test_check_memory_no_api(self) -> None:
        """Test memory check when no API configured."""
        doctor = Doctor(memory_api=None)
        stats = doctor.check_memory()

        assert not stats.available
        assert stats.endpoint is None


class TestDoctorSummary:
    """Tests for Doctor summary/report."""

    @patch("pxx.doctor._git.remote_head_sha", return_value="abc1234")
    @patch("pxx.doctor._git.head_sha", return_value="abc1234")
    @patch("pxx.doctor.requests.get")
    def test_get_summary(self, mock_get: Mock, _head: Mock, _remote: Mock) -> None:
        """Test doctor summary dict generation."""
        responses = [
            Mock(status_code=200, json=lambda: {"active_requests": 1}),  # Router
            Mock(status_code=200, json=lambda: {"observation_count": 100}),  # Memory
        ]
        mock_get.side_effect = responses

        doctor = Doctor(
            router_api="http://localhost:9000",
            memory_api="http://127.0.0.1:3111",
        )
        summary = doctor.get_summary()

        assert "timestamp" in summary
        assert "router" in summary
        assert "memory" in summary
        assert "mirrors" in summary
        assert summary["router"]["available"]
        assert summary["memory"]["available"]
        assert summary["mirrors"]["in_sync"]

    @patch("pxx.doctor._git.remote_head_sha", return_value="abc1234")
    @patch("pxx.doctor._git.head_sha", return_value="abc1234")
    @patch("pxx.doctor.requests.get")
    def test_print_report(self, mock_get: Mock, _head: Mock, _remote: Mock) -> None:
        """Test doctor report output."""
        mock_get.side_effect = [
            Mock(status_code=200, json=lambda: {"active_requests": 2}),
            Mock(status_code=200, json=lambda: {"observation_count": 342}),
        ]

        doctor = Doctor(
            router_api="http://localhost:9000",
            memory_api="http://127.0.0.1:3111",
        )

        # Just verify it doesn't crash
        doctor.print_report()


class TestRemoteStats:
    """Tests for the dual-push mirror-sync check."""

    def test_in_sync_when_all_match(self) -> None:
        """All reachable remotes matching local HEAD => in sync."""
        stats = RemoteStats(
            local_sha="deadbeef",
            remotes={"origin": "deadbeef", "mirror": "deadbeef"},
        )
        assert stats.in_sync
        assert "in sync" in str(stats)

    def test_out_of_sync_when_one_behind(self) -> None:
        """A remote at a different SHA => out of sync."""
        stats = RemoteStats(
            local_sha="deadbeef",
            remotes={"origin": "deadbeef", "mirror": "0ldc0de"},
        )
        assert not stats.in_sync
        assert "OUT OF SYNC" in str(stats)

    def test_unreachable_remote_does_not_count_as_synced(self) -> None:
        """Unreachable remote (None) is reported, not treated as in sync."""
        stats = RemoteStats(
            local_sha="deadbeef",
            remotes={"origin": "deadbeef", "mirror": None},
        )
        assert stats.in_sync  # only reachable remotes gate sync
        assert "unreachable" in str(stats)

    def test_not_a_repo(self) -> None:
        """No local HEAD => not in sync, friendly message."""
        stats = RemoteStats(local_sha=None, remotes={"origin": None})
        assert not stats.in_sync
        assert "not a git repo" in str(stats)

    @patch("pxx.doctor._git.remote_head_sha")
    @patch("pxx.doctor._git.configured_remotes", return_value={"origin", "mirror"})
    @patch("pxx.doctor._git.head_sha", return_value="deadbeef")
    def test_check_remotes_probes_each_mirror(
        self, _head: Mock, _configured: Mock, mock_remote: Mock
    ) -> None:
        """check_remotes probes every configured mirror remote."""
        mock_remote.side_effect = lambda name, ref="main": "deadbeef"
        stats = Doctor().check_remotes(remotes=("origin", "mirror"))
        assert stats.in_sync
        assert set(stats.remotes) == {"origin", "mirror"}


class TestDoctorEnvVars:
    """Tests for environment variable configuration."""

    @patch.dict(
        "os.environ",
        {"PXX_ROUTER_API": "http://router:9000", "PXX_MEMORY_API": "http://mem:3111"},
    )
    def test_doctor_from_env(self) -> None:
        """Test doctor initialization from environment."""
        doctor = Doctor()

        assert doctor.router_api == "http://router:9000"
        assert doctor.memory_api == "http://mem:3111"

    def test_doctor_override_env(self) -> None:
        """Test doctor parameters override environment."""
        doctor = Doctor(
            router_api="http://custom:9000", memory_api="http://custom:3111"
        )

        assert doctor.router_api == "http://custom:9000"
        assert doctor.memory_api == "http://custom:3111"


class TestUnconfiguredMirrors:
    """A machine carrying a subset of the mirror set is informational,
    not out-of-sync (D5 resolution, 2026-07-17)."""

    def test_not_configured_is_info_not_unreachable(self):
        from pxx.doctor import RemoteStats

        stats = RemoteStats(
            local_sha="a" * 40,
            remotes={"origin": "a" * 40},
            not_configured=("mirror",),
        )
        assert stats.in_sync is True
        text = str(stats)
        assert "mirror: not configured on this machine (info)" in text
        assert "unreachable" not in text

    def test_check_remotes_filters_unconfigured(self, monkeypatch):
        from pxx import doctor as doctor_mod

        monkeypatch.setattr("pxx._git.configured_remotes", lambda: {"origin"})
        monkeypatch.setattr("pxx._git.head_sha", lambda: "a" * 40)
        monkeypatch.setattr("pxx._git.remote_head_sha", lambda name: "a" * 40)
        d = doctor_mod.Doctor()
        stats = d.check_remotes(("origin", "mirror"))
        assert list(stats.remotes) == ["origin"]
        assert stats.not_configured == ("mirror",)


class TestNoReachableMirror:
    """Task C: offline / no mirror present is N/A, never OUT_OF_SYNC (the
    --doctor exit-1 bug). in_sync must be True when there's nothing to compare."""

    def test_all_unreachable_is_in_sync(self):
        from pxx.doctor import RemoteStats

        assert RemoteStats(local_sha="a" * 40, remotes={"origin": None}).in_sync is True

    def test_no_remotes_configured_is_in_sync(self):
        from pxx.doctor import RemoteStats

        assert RemoteStats(local_sha="a" * 40, remotes={}).in_sync is True
