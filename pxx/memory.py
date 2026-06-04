"""agentmemory lifecycle management for persistent memory with hybrid retrieval.

Manages startup, shutdown, and health checks for agentmemory server
(BM25 + vector + knowledge graph, default port 3111).
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import requests


class AgentmemoryManager:
    """Lifecycle manager for agentmemory subprocess."""

    def __init__(self, config_path: Path | None = None):
        self.config_path = config_path or Path.home() / ".agentmemory" / ".env"
        self.process: subprocess.Popen[bytes] | None = None
        self.api_base = "http://127.0.0.1:3111"
        self._ensure_config()

    def _ensure_config(self) -> None:
        """Create config file if it doesn't exist."""
        if self.config_path.exists():
            return

        self.config_path.parent.mkdir(parents=True, exist_ok=True)

        config_content = """\
# agentmemory configuration
EMBEDDING_PROVIDER=local
AGENTMEMORY_AUTO_COMPRESS=true
BM25_WEIGHT=0.5
VECTOR_WEIGHT=0.5
TOKEN_BUDGET=2000
MEMORY_ARCHIVE_AFTER_DAYS=7
STATE_BACKEND=sqlite
STATE_PATH=~/.pxx/memory.db
"""
        self.config_path.write_text(config_content)

    def start(self) -> None:
        """Start agentmemory subprocess and wait for health check."""
        import sys

        env = os.environ.copy()
        env["PXX_MEMORY_PORT"] = "3111"
        env["PXX_MEMORY_HOST"] = "127.0.0.1"

        # Try console script first (installed mode), then Python module (dev mode)
        try:
            self.process = subprocess.Popen(
                ["agentmemory"],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (FileNotFoundError, OSError):
            # Dev mode: run as Python module using uv run
            try:
                self.process = subprocess.Popen(
                    ["uv", "run", "-m", "agentmemory_pkg.main"],
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    cwd=os.path.dirname(os.path.dirname(__file__)),
                )
            except (FileNotFoundError, OSError):
                # Fallback: direct Python module (if in venv)
                self.process = subprocess.Popen(
                    [sys.executable, "-m", "agentmemory_pkg.main"],
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

        # Wait for port 3111 to be ready
        self._wait_for_ready(timeout=5)

    def stop(self) -> None:
        """Gracefully terminate agentmemory subprocess."""
        if self.process is None:
            return

        self.process.terminate()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()

    def health_check(self) -> bool:
        """Check if agentmemory server is responding."""
        try:
            resp = requests.get(f"{self.api_base}/health", timeout=2)
            return resp.status_code == 200
        except requests.RequestException:
            return False

    def _wait_for_ready(self, timeout: int = 5) -> None:
        """Block until agentmemory responds to health check."""
        start = time.time()
        while time.time() - start < timeout:
            try:
                requests.get(f"{self.api_base}/health", timeout=1)
                return
            except requests.RequestException:
                time.sleep(0.1)
        raise TimeoutError("agentmemory failed to start within timeout")
