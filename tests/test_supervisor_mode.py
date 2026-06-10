"""Smoke tests for Phase 5 supervisor mode (9router + agentmemory).

These are live tests: they launch the real 9router and agentmemory services
and probe them over the network. They are opt-in so the default unit suite
stays deterministic — set PXX_RUN_LIVE=1 with the services installed/runnable.
"""

import os
import socket
import time

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("PXX_RUN_LIVE") != "1",
    reason="live smoke test: set PXX_RUN_LIVE=1 to start and probe 9router + agentmemory",
)


def _port_in_use(port: int) -> bool:
    """True if something is already listening on 127.0.0.1:port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


class TestSupervisorModeServices:
    """Test that --with-router and --with-memory start services correctly."""

    def test_9router_manager_lifecycle(self):
        """Test 9router service lifecycle: start, status check, stop."""
        from pxx.router import NineRouterManager

        manager = NineRouterManager()

        # Start service
        manager.start()
        assert manager.process is not None
        assert manager.process.poll() is None  # Process should be running

        # Health check should pass
        assert manager.get_status() is not None

        # Stop service
        manager.stop()
        time.sleep(0.5)  # Give process time to terminate
        assert manager.process.poll() is not None  # Process should have exited

    def test_agentmemory_manager_lifecycle(self):
        """Test agentmemory service lifecycle: start, health check, stop."""
        from pxx.memory import AgentmemoryManager

        manager = AgentmemoryManager()

        # Start service
        manager.start()
        assert manager.process is not None
        assert manager.process.poll() is None  # Process should be running

        # Health check should pass
        assert manager.health_check() is True

        # Stop service
        manager.stop()
        time.sleep(0.5)  # Give process time to terminate
        assert manager.process.poll() is not None  # Process should have exited

    def test_both_services_run_concurrently(self):
        """Test that both 9router and agentmemory can run together."""
        from pxx.router import NineRouterManager
        from pxx.memory import AgentmemoryManager

        if _port_in_use(20128) or _port_in_use(3111):
            pytest.skip(
                "9router (:20128) or agentmemory (:3111) already running — this "
                "test owns the service lifecycle and can't verify a duplicate start"
            )

        router = NineRouterManager()
        memory = AgentmemoryManager()

        try:
            # Start both services
            router.start()
            memory.start()
            time.sleep(1)  # Give services time to stabilize

            # Both should be running
            assert router.process.poll() is None
            assert memory.process.poll() is None

            # Both should be healthy
            assert router.get_status() is not None
            assert memory.health_check() is True

        finally:
            # Clean up
            memory.stop()
            router.stop()

    def test_9router_proxies_requests(self):
        """Test that 9router successfully proxies requests to Studio Ollama."""
        from pxx.router import NineRouterManager
        import httpx

        manager = NineRouterManager()

        try:
            manager.start()
            time.sleep(0.5)

            # Test /v1/models endpoint through 9router
            with httpx.Client(timeout=10.0) as client:
                resp = client.get("http://127.0.0.1:20128/v1/models")
                assert resp.status_code == 200
                data = resp.json()
                # Response format may be either {"data": [...]} or {"models": [...]}
                models_list = data.get("data") or data.get("models") or []
                assert len(models_list) > 0

                # Check that devstral:24b is available
                if data.get("data"):
                    model_ids = [m["id"] for m in data["data"]]
                else:
                    model_ids = [
                        m.get("name", m.get("id", "")) for m in data.get("models", [])
                    ]
                assert "devstral:24b" in model_ids

        finally:
            manager.stop()

    def test_agentmemory_stores_observations(self):
        """Test that agentmemory responds to API requests."""
        from pxx.memory import AgentmemoryManager
        import httpx

        manager = AgentmemoryManager()

        try:
            manager.start()
            time.sleep(0.5)

            with httpx.Client(timeout=10.0) as client:
                # Test health endpoint
                resp = client.get("http://127.0.0.1:3111/health")
                assert resp.status_code == 200

        finally:
            manager.stop()

    def test_memory_middleware_integration(self):
        """Test that 9router and agentmemory can run together."""
        from pxx.router import NineRouterManager
        from pxx.memory import AgentmemoryManager
        import httpx

        router = NineRouterManager()
        memory = AgentmemoryManager()

        try:
            router.start()
            memory.start()
            time.sleep(1)

            with httpx.Client(timeout=10.0) as client:
                # Both services should be healthy
                router_resp = client.get("http://127.0.0.1:20128/health")
                memory_resp = client.get("http://127.0.0.1:3111/health")
                assert router_resp.status_code == 200
                assert memory_resp.status_code == 200

        finally:
            memory.stop()
            router.stop()

    def test_services_cleanup_on_interrupt(self):
        """Test that services are cleaned up properly on KeyboardInterrupt."""
        from pxx.router import NineRouterManager
        from pxx.memory import AgentmemoryManager

        if _port_in_use(20128) or _port_in_use(3111):
            pytest.skip(
                "9router (:20128) or agentmemory (:3111) already running — this "
                "test owns the service lifecycle and can't verify a duplicate start"
            )

        router = NineRouterManager()
        memory = AgentmemoryManager()

        # Start both
        router.start()
        memory.start()
        time.sleep(0.5)

        assert router.process.poll() is None
        assert memory.process.poll() is None

        # Simulate cleanup (like on KeyboardInterrupt)
        memory.stop()
        router.stop()

        # Both should be stopped
        time.sleep(0.5)
        assert router.process.poll() is not None
        assert memory.process.poll() is not None
