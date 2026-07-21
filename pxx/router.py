"""9router lifecycle management for token compression and provider routing.

Manages startup, shutdown, health checks, and API queries for the 9router
OpenAI-compatible proxy (default port 20128).
"""

from __future__ import annotations

import os
import random
import subprocess
import time
from pathlib import Path

import requests

# Source dir for the bundled 9router service, resolved relative to this package
# (repo_root/services/9router) rather than an absolute path. Used only by the
# dev-mode `uv run` fallback; an installed `nine-router` console script (Try 1)
# doesn't need it. Override with PXX_9ROUTER_DIR.
_SERVICE_DIR = os.environ.get(
    "PXX_9ROUTER_DIR",
    str(Path(__file__).resolve().parent.parent / "services" / "9router"),
)


class NineRouterManager:
    """Lifecycle manager for 9router subprocess."""

    def __init__(self, config_path: Path | None = None):
        self.config_path = config_path or Path.home() / ".9router" / "config.yml"
        self.process: subprocess.Popen[bytes] | None = None
        self.api_base = "http://127.0.0.1:20128"

    def start(self, env: dict[str, str] | None = None) -> None:
        """Start 9router subprocess and wait for health check.

        Args:
            env: Optional environment dict to merge with os.environ.
        """
        if env is None:
            env = os.environ.copy()
        else:
            # Merge with current environment
            merged = os.environ.copy()
            merged.update(env)
            env = merged

        env["PXX_ROUTER_PORT"] = "20128"
        env["PXX_ROUTER_HOST"] = "127.0.0.1"

        # Try console script first (installed mode), then Python module (dev mode)
        self.process = None
        last_error = None

        # Try 1: console script (production install)
        try:
            self.process = subprocess.Popen(
                ["nine-router"],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._wait_for_ready(timeout=5)
            return
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as e:
            last_error = e
            pass

        # Try 2: uv run (dev mode with uv)
        try:
            self.process = subprocess.Popen(
                ["uv", "run", "-m", "nine_router_pkg.main"],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=_SERVICE_DIR,
            )
            self._wait_for_ready(timeout=5)
            return
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as e2:
            last_error = e2
            pass

        # Both failed
        raise RuntimeError(f"Failed to start 9router: {last_error}")

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

    def _start_with_retries(self, max_attempts: int = 3) -> None:
        """Start the service with retry logic and exponential backoff."""
        attempt = 0
        while attempt < max_attempts:
            try:
                self.start()
                return
            except (
                RuntimeError,
                FileNotFoundError,
                OSError,
                subprocess.TimeoutExpired,
            ) as e:
                attempt += 1
                if attempt >= max_attempts:
                    raise RuntimeError(
                        f"Failed to start service after {max_attempts} attempts: {e}"
                    )
                wait_time = min(2**attempt + random.random(), 60)
                print(
                    f"Retrying in {wait_time:.2f} seconds... (attempt {attempt}/{max_attempts})"
                )
                time.sleep(wait_time)
