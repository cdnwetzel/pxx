"""Memory injection for aider system prompt (Phase 5 Tier 2).

Retrieves relevant observations from agentmemory and formats them as
context to inject into aider's system prompt via the --read flag.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import requests


class MemoryInjector:
    """Query agentmemory and format observations for system prompt injection."""

    def __init__(self, memory_api_base: str = "http://127.0.0.1:3111"):
        self.memory_api = memory_api_base

    def retrieve(
        self,
        repo_root: str | None = None,
        cwd: str | None = None,
        limit: int = 5,
        timeout: float = 3.0,
    ) -> dict:
        """Query agentmemory /mem/retrieve with project context.

        Args:
            repo_root: Git repository root path (optional filter)
            cwd: Current working directory (optional filter)
            limit: Maximum observations to retrieve
            timeout: HTTP request timeout

        Returns:
            Dict with 'observations' list and metadata. Returns empty dict on error.
        """
        try:
            payload = {
                "limit": limit,
                "filters": {},
            }
            if repo_root:
                payload["filters"]["repo_root"] = repo_root
            if cwd:
                payload["filters"]["cwd"] = cwd

            resp = requests.post(
                f"{self.memory_api}/mem/retrieve",
                json=payload,
                timeout=timeout,
            )
            if resp.status_code == 200:
                return resp.json()
            return {}
        except (requests.RequestException, ValueError):
            return {}

    def format_context(self, observations: list[dict]) -> str:
        """Format observations as markdown context for aider prompt.

        Args:
            observations: List of observation dicts from /mem/retrieve

        Returns:
            Formatted markdown string ready for --read injection.
        """
        if not observations:
            return ""

        lines = [
            "# Session Memory",
            "",
            "Recent observations from previous sessions in this project:",
            "",
        ]

        for i, obs in enumerate(observations, 1):
            title = obs.get("title", f"Observation {i}")
            content = obs.get("content", "")
            source = obs.get("source", "")
            score = obs.get("score", 0)

            lines.append(f"## {i}. {title}")
            if source:
                lines.append(f"_Source: {source} (relevance: {score:.2f})_")
            lines.append("")
            lines.append(content)
            lines.append("")

        lines.extend([
            "---",
            "",
            "Use these observations to understand past context, but verify any",
            "facts or patterns before relying on them in the current session.",
            "",
        ])

        return "\n".join(lines)

    def write_context_file(
        self,
        observations: list[dict],
        directory: Path | None = None,
    ) -> Path | None:
        """Write formatted observations to a temp file for --read flag.

        Args:
            observations: List of observation dicts
            directory: Temp directory (default: system temp)

        Returns:
            Path to temp file, or None if observations empty or write failed.
        """
        content = self.format_context(observations)
        if not content:
            return None

        try:
            tmp_dir = directory or Path(tempfile.gettempdir())
            tmp_file = tmp_dir / "pxx-memory-context.md"
            tmp_file.write_text(content, encoding="utf-8")
            return tmp_file
        except OSError:
            return None

    def inject_into_aider_args(
        self,
        aider_args: list[str],
        repo_root: str | None = None,
        cwd: str | None = None,
        tmp_dir: Path | None = None,
    ) -> list[str]:
        """Retrieve observations and inject into aider args via --read flag.

        Queries memory, formats context, writes temp file, and adds
        --read <path> to the aider command line.

        Args:
            aider_args: Current aider command line args
            repo_root: Git repository root for filtering
            cwd: Current working directory for filtering
            tmp_dir: Optional temp directory for context file

        Returns:
            Modified aider_args with --read flag added, or original args if
            memory unavailable.
        """
        obs_result = self.retrieve(repo_root=repo_root, cwd=cwd)
        observations = obs_result.get("observations", [])

        if not observations:
            return aider_args

        tmp_path = self.write_context_file(observations, tmp_dir)
        if not tmp_path:
            return aider_args

        # Insert --read before other args (after aider binary)
        # Preserve the order: binary, then --read, then other args
        if aider_args:
            return [aider_args[0], "--read", str(tmp_path), *aider_args[1:]]
        return ["--read", str(tmp_path), *aider_args]
