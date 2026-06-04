"""Memory-aware slash commands for aider sessions (/recall, /remember, /forget).

⚠️ STATUS: Implemented but NOT WIRED. The handler parses and executes commands,
but there is no integration point in the supervisor loop that connects:
1. User typing /recall in aider's stdin prompt
2. This handler intercepting and executing the command
3. The result being returned to the user in the aider session

The handler was designed to parse commands from stdout, but user-typed commands
go to stdin, not stdout. The supervisor's observer thread (which would need to
read/write bidirectionally with aider) is currently disabled due to TTY issues.

NEXT STEPS (post-Phase-5):
- Implement slash commands via aider's stdin hook (if aider supports custom hooks)
- Or: execute slash commands post-session via .aider.chat.history.md analysis
- Or: wire stdin/stdout bidirectionally via PTY support in the supervisor

For now, this module is infrastructure-ready but not connected.
"""

from __future__ import annotations

import re
from typing import TypedDict

import requests


class SlashCommandResult(TypedDict):
    """Result of a slash command execution."""

    success: bool
    command: str
    response: str


class SlashCommandHandler:
    """Execute memory-aware slash commands (/recall, /remember, /forget)."""

    def __init__(self, memory_api_base: str = "http://127.0.0.1:3111"):
        self.memory_api = memory_api_base

    def parse_command(self, line: str) -> tuple[str, str] | None:
        """Parse a slash command from aider output.

        Args:
            line: Single line of aider output

        Returns:
            (command, args) tuple, or None if not a recognized command.
        """
        line = line.strip()
        match = re.match(r"^/(\w+)\s*(.*)", line)
        if not match:
            return None

        cmd = match.group(1)
        args = match.group(2).strip()

        if cmd in ("recall", "remember", "forget"):
            return (cmd, args)

        return None

    def execute(
        self,
        command: str,
        args: str,
        repo_root: str | None = None,
        cwd: str | None = None,
    ) -> SlashCommandResult:
        """Execute a slash command.

        Args:
            command: Command name (recall, remember, forget)
            args: Command arguments (query string or key=value pairs)
            repo_root: Git repository root (optional context)
            cwd: Current working directory (optional context)

        Returns:
            SlashCommandResult with success flag and response.
        """
        try:
            if command == "recall":
                return self._recall(args, repo_root=repo_root, cwd=cwd)
            elif command == "remember":
                return self._remember(args, repo_root=repo_root, cwd=cwd)
            elif command == "forget":
                return self._forget(args)
            else:
                return {
                    "success": False,
                    "command": command,
                    "response": f"Unknown command: /{command}",
                }
        except Exception as e:
            return {
                "success": False,
                "command": command,
                "response": f"Command error: {e}",
            }

    def _recall(
        self,
        query: str,
        repo_root: str | None = None,
        cwd: str | None = None,
    ) -> SlashCommandResult:
        """Execute /recall <query> to search memory.

        Args:
            query: Search term for memory retrieval
            repo_root: Repository root for filtering
            cwd: Current directory for filtering

        Returns:
            SlashCommandResult with search results.
        """
        if not query:
            return {
                "success": False,
                "command": "recall",
                "response": "Usage: /recall <query>",
            }

        try:
            payload = {
                "query": query,
                "limit": 5,
                "filters": {},
            }
            if repo_root:
                payload["filters"]["repo_root"] = repo_root
            if cwd:
                payload["filters"]["cwd"] = cwd

            resp = requests.post(
                f"{self.memory_api}/search",
                json=payload,
                timeout=2.0,
            )

            if resp.status_code != 200:
                return {
                    "success": False,
                    "command": "recall",
                    "response": f"Memory query failed: HTTP {resp.status_code}",
                }

            result = resp.json()
            observations = result.get("observations", [])

            if not observations:
                return {
                    "success": True,
                    "command": "recall",
                    "response": f"No observations found for: {query}",
                }

            lines = [f"### Recall Results for '{query}':\n"]
            for i, obs in enumerate(observations, 1):
                title = obs.get("title", f"Observation {i}")
                content = obs.get("content", "")
                score = obs.get("score", 0)
                lines.append(f"**{i}. {title}** (relevance: {score:.2f})")
                lines.append(content)
                lines.append("")

            return {
                "success": True,
                "command": "recall",
                "response": "\n".join(lines),
            }

        except requests.RequestException as e:
            return {
                "success": False,
                "command": "recall",
                "response": f"Memory connection error: {e}",
            }

    def _remember(
        self,
        args: str,
        repo_root: str | None = None,
        cwd: str | None = None,
    ) -> SlashCommandResult:
        """Execute /remember <title> <content> to save observation.

        Args:
            args: "title" "content" or title:content format
            repo_root: Repository root for context
            cwd: Current directory for context

        Returns:
            SlashCommandResult with save status.
        """
        if not args:
            return {
                "success": False,
                "command": "remember",
                "response": 'Usage: /remember "title" "content"',
            }

        # Parse: /remember "title" "content" or /remember title:content
        parts = args.split(":", 1)
        if len(parts) == 2:
            title, content = parts[0].strip(), parts[1].strip()
        else:
            # Try quoted format
            match = re.match(r'"([^"]+)"\s+"([^"]+)"', args)
            if match:
                title, content = match.group(1), match.group(2)
            else:
                return {
                    "success": False,
                    "command": "remember",
                    "response": 'Usage: /remember "title" "content"',
                }

        if not title or not content:
            return {
                "success": False,
                "command": "remember",
                "response": 'Title and content cannot be empty',
            }

        try:
            observation = {
                "title": title,
                "content": content,
                "source": "user-remember-command",
                "metadata": {
                    "repo_root": repo_root,
                    "cwd": cwd,
                    "command": "remember",
                },
            }

            resp = requests.post(
                f"{self.memory_api}/inject",
                json={"observations": [observation]},
                timeout=2.0,
            )

            if resp.status_code == 200:
                return {
                    "success": True,
                    "command": "remember",
                    "response": f"Saved: {title}",
                }
            else:
                return {
                    "success": False,
                    "command": "remember",
                    "response": f"Save failed: HTTP {resp.status_code}",
                }

        except requests.RequestException as e:
            return {
                "success": False,
                "command": "remember",
                "response": f"Save error: {e}",
            }

    def _forget(self, args: str) -> SlashCommandResult:
        """Execute /forget <key> to mark observation deprecated.

        Args:
            args: Observation key or ID to forget

        Returns:
            SlashCommandResult with forget status.
        """
        if not args:
            return {
                "success": False,
                "command": "forget",
                "response": "Usage: /forget <observation_id>",
            }

        # TODO: agentmemory does not yet have a /forget endpoint.
        # For now, return a "not yet implemented" message.
        return {
            "success": False,
            "command": "forget",
            "response": "/forget not yet implemented (agentmemory endpoint missing)",
        }

    def is_command_line(self, line: str) -> bool:
        """Check if line is a slash command.

        Args:
            line: Line to check

        Returns:
            True if line starts with / and is a recognized command.
        """
        cmd_result = self.parse_command(line)
        return cmd_result is not None
