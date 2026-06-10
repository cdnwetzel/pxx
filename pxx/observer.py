"""Observer pattern for aider output monitoring and agentmemory integration.

⚠️ STATUS: Infrastructure only. Runtime observation is BLOCKED pending two issues:

1. TTY Problem: aider is a TUI that requires isatty()=true for terminal rendering.
   Capturing stdout via PIPE breaks the UI. Requires PTY support to solve.

2. Output Format Problem: AiderOutputParser looks for:
   - {"tool_name": ...} JSON — aider never serializes tool_calls to stdout
   - <tool_result>...</tool_result> tags — aider never writes these to stdout
   Tool calls are internal API objects, not human-readable output. Real aider
   output is rich-rendered text with no structured data.

NEXT STEPS (post-Phase-5):
- Investigate .aider.chat.history.md (aider's post-session output log) as an
  alternative observation mechanism. This captures structured conversation data
  without fighting the terminal emulation problem.
- If real-time observation is required, implement PTY support + agent API hooks
  instead of stdout scraping.

For now, the service lifecycle (start/stop) works correctly. Memory injection
(reading prior observations at session start) may work. Runtime capture does not.

ROLE: this observer is a thin producer for the agentmemory server. Captured
tool-call/result pairs are stored as observations via the server's
`POST /observations {project, content}` endpoint, scoped to the session's repo
root. The server owns storage, ranking, and retrieval.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from subprocess import Popen
from threading import Thread

import requests

from pxx.memory_analytics import MemoryAnalytics
from pxx.memory_commands import SlashCommandHandler


class AiderOutputParser:
    """Parse aider stdout to extract tool calls and results."""

    def parse_stream(self, stdout_iter: Iterator[str]) -> Iterator[tuple[str, dict]]:
        """Yield (event_type, payload) tuples from aider output.

        Event types: tool_call, tool_result, error, conversation_start

        Args:
            stdout_iter: Iterator of stdout lines from aider.

        Yields:
            (event_type, payload) tuples for each parsed event.
        """
        for line in stdout_iter:
            line = line.strip()
            if not line:
                continue

            # Aider formats tool calls as JSON blocks
            if line.startswith("{") and '"tool_name"' in line:
                try:
                    obj = json.loads(line)
                    # Handle both old and new observation formats
                    if isinstance(obj, dict) and "content" in obj:
                        yield ("tool_call", {"content": obj["content"]})
                    elif "tool_name" in obj:
                        yield ("tool_call", obj)
                except json.JSONDecodeError:
                    pass

            # Tool results marked with <tool_result> tags
            if "<tool_result>" in line:
                result_text = self._extract_tag("tool_result", line)
                if result_text:
                    yield ("tool_result", {"output": result_text, "success": True})

            # Conversation markers
            if "starting session" in line.lower():
                yield ("conversation_start", {})

            # Error patterns
            if any(
                marker in line.lower() for marker in ["error", "exception", "traceback"]
            ):
                yield ("error", {"message": line})

    def _extract_tag(self, tag: str, text: str) -> str | None:
        """Extract content between opening and closing XML tags.

        Args:
            tag: Tag name (e.g., 'tool_result').
            text: Text to search.

        Returns:
            Content between tags, or None if not found.
        """
        opening = f"<{tag}>"
        closing = f"</{tag}>"

        start_idx = text.find(opening)
        end_idx = text.find(closing)

        if start_idx == -1 or end_idx == -1:
            return None

        return text[start_idx + len(opening) : end_idx]


class AiderMemoryObserver:
    """Watch aider subprocess and store observations in agentmemory."""

    def __init__(
        self,
        aider_proc: Popen[bytes],
        memory_api_base: str = "http://127.0.0.1:3111",
        repo_root: str | None = None,
        cwd: str | None = None,
        analytics: MemoryAnalytics | None = None,
    ):
        self.aider = aider_proc
        self.memory_api = memory_api_base
        self.repo_root = repo_root
        self.cwd = cwd
        self.thread: Thread | None = None
        self.last_tool_call: dict | None = None
        self.slash_commands = SlashCommandHandler(memory_api_base)
        self.analytics = analytics

    def start(self) -> None:
        """Start observer thread."""
        self.thread = Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        """Main observer loop: read aider output, pair tool use, store observations."""
        parser = AiderOutputParser()

        if self.aider.stdout is None:
            return

        for line in iter(self.aider.stdout.readline, b""):
            line_str = line.decode("utf-8", errors="replace").rstrip("\n")

            # Slash commands take precedence over tool-call parsing.
            cmd_result = self.slash_commands.parse_command(line_str)
            if cmd_result:
                cmd_name, cmd_args = cmd_result
                exec_result = self.slash_commands.execute(
                    cmd_name,
                    cmd_args,
                    repo_root=self.repo_root,
                    cwd=self.cwd,
                )
                self._record_slash_command(cmd_name, exec_result)
                continue

            for event_type, payload in parser.parse_stream([line_str]):
                if event_type == "tool_call":
                    # Hold the call until its result arrives so we can pair them.
                    self.last_tool_call = payload

                elif event_type == "tool_result" and self.last_tool_call:
                    tool_name = self.last_tool_call.get("tool_name", "unknown")
                    tool_input = str(self.last_tool_call.get("arguments", {}))
                    tool_output = payload.get("output", "")
                    obs = self._format_observation(tool_name, tool_input, tool_output)
                    self._store_observation(obs)
                    self.last_tool_call = None

    def _format_observation(
        self, tool_name: str, tool_input: str, tool_output: str
    ) -> dict:
        """Format a tool use as an observation for storage.

        Args:
            tool_name: Name of the tool (e.g., 'execute_bash', 'read_file')
            tool_input: Input/arguments to the tool
            tool_output: Output from the tool

        Returns:
            Observation dict; its ``content`` field is what gets stored.
        """
        # Truncate long outputs to avoid bloating the observation store
        max_output_len = 500
        if len(tool_output) > max_output_len:
            tool_output = tool_output[:max_output_len] + "... (truncated)"

        title = f"Tool use: {tool_name}"
        content = f"**Tool:** {tool_name}\n"
        if tool_input:
            content += f"**Input:** {tool_input}\n"
        content += f"**Output:** {tool_output}"

        return {
            "title": title,
            "content": content,
            "source": f"aider-session:{tool_name}",
            "metadata": {
                "tool": tool_name,
                "repo_root": self.repo_root,
                "cwd": self.cwd,
            },
        }

    def _store_observation(self, observation: dict) -> None:
        """Store an observation via the server's POST /observations endpoint.

        Logs but doesn't block aider on memory failures.

        Args:
            observation: Observation dict; ``content`` is persisted, scoped to
                the session's repo root (the server's per-project key).
        """
        try:
            payload = {
                "project": self.repo_root or "default",
                "content": observation.get("content", ""),
            }
            resp = requests.post(
                f"{self.memory_api}/observations",
                json=payload,
                timeout=2,
            )
            if resp.status_code != 200:
                # Log but don't block
                print(
                    f"pxx: memory store failed: {resp.status_code}",
                    file=sys.stderr,
                )
        except requests.RequestException as e:
            # Log but don't block
            print(f"pxx: memory store error: {e}", file=sys.stderr)

    def _record_slash_command(self, cmd_name: str, result: dict) -> None:
        """Record a slash command execution in analytics and the observation store.

        Args:
            cmd_name: Slash command name (recall, remember, forget)
            result: SlashCommandResult from command execution
        """
        success = result.get("success", False)
        response = result.get("response", "")

        # Record in analytics if available
        if self.analytics:
            # For /recall, result_count is approximated from the response summary
            result_count = len(response.split("\n")) // 3 if success else 0
            self.analytics.record_command(cmd_name, success, result_count)

        content = f"Slash command /{cmd_name} (success={success})\n\n{response[:200]}"
        self._store_observation({"content": content})
