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

ROLE: this handler is a thin client. The agentmemory server owns the command
logic (services/agentmemory CommandHandler). Each command is forwarded to the
server's `POST /command` endpoint as `{project, command, args}` and the JSON
result is formatted for display. The server scopes observations by `project`,
which pxx maps to the session's repo root.
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
            repo_root: Git repository root, used as the memory `project` scope
            cwd: Current working directory (reserved; server scopes by project)

        Returns:
            SlashCommandResult with success flag and response.
        """
        try:
            if command == "recall":
                return self._recall(args, repo_root=repo_root)
            elif command == "remember":
                return self._remember(args, repo_root=repo_root)
            elif command == "forget":
                return self._forget(args, repo_root=repo_root)
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

    def _post_command(
        self, command: str, args: dict, repo_root: str | None
    ) -> requests.Response:
        """Forward a slash command to the agentmemory server's /command endpoint."""
        payload = {
            "project": repo_root or "default",
            "command": command,
            "args": args,
        }
        return requests.post(
            f"{self.memory_api}/command",
            json=payload,
            timeout=2.0,
        )

    def _recall(self, query: str, repo_root: str | None = None) -> SlashCommandResult:
        """Execute /recall <query> — search saved observations via /command."""
        if not query:
            return {
                "success": False,
                "command": "recall",
                "response": "Usage: /recall <query>",
            }

        try:
            resp = self._post_command("recall", {"query": query, "limit": 5}, repo_root)

            if resp.status_code != 200:
                return {
                    "success": False,
                    "command": "recall",
                    "response": f"Memory query failed: HTTP {resp.status_code}",
                }

            results = resp.json().get("results", [])
            if not results:
                return {
                    "success": True,
                    "command": "recall",
                    "response": f"No observations found for: {query}",
                }

            lines = [f"### Recall Results for '{query}':\n"]
            for i, obs in enumerate(results, 1):
                content = obs.get("content", "")
                score = obs.get("score", 0)
                lines.append(f"**{i}.** (relevance: {score:.2f})")
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

    def _remember(self, args: str, repo_root: str | None = None) -> SlashCommandResult:
        """Execute /remember "title" "content" — save an observation via /command."""
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
                "response": "Title and content cannot be empty",
            }

        try:
            resp = self._post_command(
                "remember", {"title": title, "content": content}, repo_root
            )

            if resp.status_code == 200:
                return {
                    "success": True,
                    "command": "remember",
                    "response": f"Saved: {title}",
                }
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

    def _forget(self, args: str, repo_root: str | None = None) -> SlashCommandResult:
        """Execute /forget <id> — delete an observation via /command."""
        obs_id = args.strip()
        if not obs_id:
            return {
                "success": False,
                "command": "forget",
                "response": "Usage: /forget <observation_id>",
            }

        try:
            resp = self._post_command("forget", {"id": obs_id}, repo_root)

            if resp.status_code == 200:
                return {
                    "success": True,
                    "command": "forget",
                    "response": f"Forgot: {obs_id}",
                }
            return {
                "success": False,
                "command": "forget",
                "response": f"Forget failed: HTTP {resp.status_code}",
            }

        except requests.RequestException as e:
            return {
                "success": False,
                "command": "forget",
                "response": f"Forget error: {e}",
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
