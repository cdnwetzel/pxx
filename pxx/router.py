"""9router lifecycle management for token compression and provider routing.

Manages startup, shutdown, health checks, and API queries for the 9router
OpenAI-compatible proxy (default port 20128).
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import requests


class NineroterManager:
    """Lifecycle manager for 9router subprocess."""

    def __init__(self, config_path: Path | None = None):
        self.config_path = config_path or Path.home() / ".9router" / "config.yml"
        self.process: subprocess.Popen[bytes] | None = None
        self.api_base = "http://127.0.0.1:20128"

    def start(self) -> None:
        """Start 9router subprocess and wait for health check."""
        env = os.environ.copy()
        env["NINE_ROUTER_CONFIG"] = str(self.config_path)
        env["NODE_ENV"] = "production"

        self.process = subprocess.Popen(
            ["9router"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Wait for port 20128 to be ready
        self._wait_for_ready(timeout=5)

    def stop(self) -> None:
        """Gracefully terminate 9router subprocess."""
        if self.process is None:
            return

        self.process.terminate()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()

    def get_usage(self) -> dict:
        """Query token usage and cost from 9router."""
        try:
            resp = requests.get(f"{self.api_base}/v1/usage", timeout=2)
            return resp.json()
        except (requests.RequestException, ValueError):
            return {}

    def get_status(self) -> dict:
        """Query provider status and fallback chain from 9router."""
        try:
            resp = requests.get(f"{self.api_base}/v1/status", timeout=2)
            return resp.json()
        except (requests.RequestException, ValueError):
            return {}

    def _wait_for_ready(self, timeout: int = 5) -> None:
        """Block until 9router responds to health check."""
        start = time.time()
        while time.time() - start < timeout:
            try:
                requests.get(f"{self.api_base}/health", timeout=1)
                return
            except requests.RequestException:
                time.sleep(0.1)
        raise TimeoutError("9router failed to start within timeout")
