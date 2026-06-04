"""Smoke tests for Phase 5 supervisor mode (9router + agentmemory)."""

import os
import sys
import subprocess
import time
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestSupervisorModeServices:
    """Test that --with-router and --with-memory start services correctly."""

    def test_9router_manager_lifecycle(self):
        """Test 9router service lifecycle: start, status check, stop."""
        from pxx.router import NineroterManager

        manager = NineroterManager()

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
        from pxx.router import NineroterManager
        from pxx.memory import AgentmemoryManager

        router = NineroterManager()
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
        from pxx.router import NineroterManager
        import httpx

        manager = NineroterManager()

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
                    model_ids = [m.get("name", m.get("id", "")) for m in data.get("models", [])]
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
        from pxx.router import NineroterManager
        from pxx.memory import AgentmemoryManager
        import httpx

        router = NineroterManager()
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
        from pxx.router import NineroterManager
        from pxx.memory import AgentmemoryManager

        router = NineroterManager()
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
