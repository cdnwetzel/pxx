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

# Source dir for the bundled agentmemory service, resolved relative to this
# package (repo_root/services/agentmemory) rather than an absolute path. Used
# only by the dev-mode `uv run` fallback; an installed `agentmemory` console
# script (Try 1) doesn't need it. Override with PXX_AGENTMEMORY_DIR.
_SERVICE_DIR = os.environ.get(
    "PXX_AGENTMEMORY_DIR",
    str(Path(__file__).resolve().parent.parent / "services" / "agentmemory"),
)


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

    def start(self, env: dict[str, str] | None = None) -> None:
        """Start agentmemory subprocess and wait for health check.

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

        env["PXX_MEMORY_HOST"] = "127.0.0.1"
        env["PXX_MEMORY_PORT"] = "3111"
        env["DOTENV_PATH"] = str(self.config_path)

        # Try console script first (installed mode), then Python module (dev mode)
        self.process = None
        last_error = None

        # Try 1: console script (production install)
        try:
            self.process = subprocess.Popen(
                ["agentmemory", "server"],
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
                ["uv", "run", "-m", "agentmemory_pkg.main"],
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
        raise RuntimeError(f"Failed to start agentmemory: {last_error}")

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
