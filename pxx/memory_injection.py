"""Memory injection for aider system prompt (Phase 5 Tier 2).

Retrieves relevant observations from agentmemory and formats them as
context to inject into aider's system prompt via the --read flag.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import requests

from pxx.memory_analytics import MemoryAnalytics
from pxx.memory_tuning import MemoryTuner


class MemoryInjector:
    """Query agentmemory and format observations for system prompt injection."""

    def __init__(
        self,
        memory_api_base: str = "http://127.0.0.1:3111",
        tuner: MemoryTuner | None = None,
        analytics: MemoryAnalytics | None = None,
    ):
        self.memory_api = memory_api_base
        self.tuner = tuner
        self.analytics = analytics

    def retrieve(
        self,
        repo_root: str | None = None,
        cwd: str | None = None,
        query: str = "",
        limit: int = 5,
        timeout: float = 3.0,
    ) -> dict:
        """Retrieve observations from agentmemory for the session's project.

        Uses the server's structured search endpoint (POST /search) so the
        client-side tuner can rank the returned dicts before injection. The
        repo root is the server's per-project scope. The server's bundled
        POST /inject endpoint is an alternative that returns pre-formatted,
        char-capped strings, but it bypasses the local tuner.

        Args:
            repo_root: Git repository root path; the server's project scope
            cwd: Current working directory (reserved; server scopes by project)
            query: Optional ranking query; empty retrieves the project's set
            limit: Maximum observations to retrieve
            timeout: HTTP request timeout

        Returns:
            Dict with an 'observations' list (the server's ranked results).
            Returns an empty dict on error.
        """
        try:
            payload = {
                "project": repo_root or "default",
                "query": query,
                "limit": limit,
            }

            resp = requests.post(
                f"{self.memory_api}/search",
                json=payload,
                timeout=timeout,
            )
            if resp.status_code == 200:
                # Server returns ranked dicts under "results"; downstream code
                # (tuner, analytics, format_context) consumes "observations".
                return {"observations": resp.json().get("results", [])}
            return {}
        except (requests.RequestException, ValueError):
            return {}

    def format_context(self, observations: list[dict]) -> str:
        """Format observations as markdown context for aider prompt.

        Args:
            observations: List of observation dicts from retrieve() (/search)

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

        lines.extend(
            [
                "---",
                "",
                "Use these observations to understand past context, but verify any",
                "facts or patterns before relying on them in the current session.",
                "",
            ]
        )

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
        --read <path> to the aider command line. Applies tuning if available
        and records analytics.

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

        # Apply tuning if available
        if self.tuner:
            tuned, stats = self.tuner.tune_observations(observations)
            observations = tuned

        # Record retrieval event in analytics
        if self.analytics:
            self.analytics.record_retrieval(
                query="aider-startup",
                observations=observations,
            )

        if not observations:
            return aider_args

        tmp_path = self.write_context_file(observations, tmp_dir)
        if not tmp_path:
            return aider_args

        # Record injection event in analytics
        if self.analytics:
            context_size = len(self.format_context(observations))
            self.analytics.record_injection(observations, context_size=context_size)

        # Insert --read before other args (after aider binary)
        # Preserve the order: binary, then --read, then other args
        if aider_args:
            return [aider_args[0], "--read", str(tmp_path), *aider_args[1:]]
        return ["--read", str(tmp_path), *aider_args]
