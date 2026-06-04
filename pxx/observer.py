"""Observer pattern for aider output monitoring and agentmemory integration.

Watches aider subprocess stdout/stderr, parses tool calls and results,
and sends hook events to agentmemory for persistent memory capture.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from datetime import datetime
from subprocess import Popen
from threading import Thread

import requests


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
                    if "tool_name" in obj:
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
    """Watch aider subprocess and pipe events to agentmemory."""

    def __init__(
        self,
        aider_proc: Popen[bytes],
        memory_api_base: str = "http://127.0.0.1:3111",
    ):
        self.aider = aider_proc
        self.memory_api = memory_api_base
        self.thread: Thread | None = None

    def start(self) -> None:
        """Start observer thread."""
        self.thread = Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        """Main observer loop: read aider output, parse, send to memory."""
        parser = AiderOutputParser()

        if self.aider.stdout is None:
            return

        for line in iter(self.aider.stdout.readline, b""):
            line_str = line.decode("utf-8", errors="replace").rstrip("\n")

            for event_type, payload in parser.parse_stream([line_str]):
                if event_type == "tool_call":
                    self._send_to_memory(
                        {
                            "hook_type": "pre_tool_use",
                            "data": payload,
                            "timestamp": datetime.now().isoformat(),
                        }
                    )

                elif event_type == "tool_result":
                    self._send_to_memory(
                        {
                            "hook_type": "post_tool_use",
                            "data": {
                                "tool_output": payload["output"],
                                "success": payload["success"],
                            },
                            "timestamp": datetime.now().isoformat(),
                        }
                    )

                elif event_type == "error":
                    self._send_to_memory(
                        {
                            "hook_type": "error",
                            "data": payload,
                            "timestamp": datetime.now().isoformat(),
                        }
                    )

    def _send_to_memory(self, hook_payload: dict) -> None:
        """POST hook event to agentmemory.

        Logs but doesn't block aider on memory failures.

        Args:
            hook_payload: Hook event dict to send.
        """
        try:
            resp = requests.post(
                f"{self.memory_api}/mem/observe",
                json=hook_payload,
                timeout=2,
            )
            if resp.status_code != 200:
                # Log but don't block
                print(
                    f"pxx: memory observe failed: {resp.status_code}",
                    file=sys.stderr,
                )
        except requests.RequestException as e:
            # Log but don't block
            print(f"pxx: memory connection error: {e}", file=sys.stderr)
