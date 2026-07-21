"""ReplayBackend: deterministic replay of a recorded run (Phase 13/10.8).

Reads a run directory (``state_dir/runs/<run_id>/``) and re-executes the
recorded tool calls — in order — through the SAME registry + action broker
as a live run. No live model, no network: the trajectory comes from
``events.jsonl``; the terminal code comes from ``outcome.json``. Two replays
of the same record are byte-identical. This is the substrate B9
(pause/resume/checkpoint) builds on.

Limit: the audit stream is metadata-only — tool args previews are truncated
at ~200 chars, so calls whose recorded args were truncated cannot be
replayed faithfully; they fail closed (``BackendError``), never guessed.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..errors import BackendError
from ..outcome import RunOutcome, TerminalCode
from .base import BackendCapabilities, SessionContext

log = logging.getLogger("pxx.backends.replay")

_TRUNCATED_MARKER = "…[truncated]"


class ReplayBackend:
    """Replay the tool-call trajectory recorded in a run directory."""

    name = "replay"
    capabilities = BackendCapabilities(
        streaming=False, tools=True, interactive=False, headless=True
    )

    def __init__(self, run_dir: str | Path) -> None:
        self.run_dir = Path(run_dir)
        events_path = self.run_dir / "events.jsonl"
        outcome_path = self.run_dir / "outcome.json"
        if not events_path.is_file():
            raise BackendError(f"replay: no events.jsonl in {self.run_dir}")
        self._events = [
            json.loads(line) for line in events_path.read_text().splitlines() if line.strip()
        ]
        self._outcome: dict[str, Any] = {}
        if outcome_path.is_file():
            try:
                self._outcome = json.loads(outcome_path.read_text())
            except json.JSONDecodeError:
                log.warning("replay: unreadable outcome.json in %s", self.run_dir)

    def _recorded_code(self) -> TerminalCode:
        raw = self._outcome.get("code") or "MODEL_UNAVAILABLE"
        try:
            return TerminalCode(raw)
        except ValueError:
            return TerminalCode.MODEL_UNAVAILABLE

    async def run(self, task: str, ctx: SessionContext) -> RunOutcome:
        from .mock import make_tool_context

        tool_ctx = make_tool_context(ctx)
        calls = 0
        for event in self._events:
            if event.get("kind") != "tool_call":
                continue
            data = event.get("data", {})
            name = data.get("tool")
            args = data.get("args") or {}
            if not isinstance(name, str) or not isinstance(args, dict):
                raise BackendError(f"replay: malformed tool_call event: {event!r}")
            truncated = any(isinstance(v, str) and _TRUNCATED_MARKER in v for v in args.values())
            if truncated:
                raise BackendError(
                    f"replay: recorded args for {name!r} were truncated in the "
                    "audit stream (metadata-only) — cannot replay faithfully "
                    "(fail-closed)"
                )
            await ctx.tools.call(name, args, tool_ctx)  # same broker/gates
            calls += 1
        return RunOutcome(
            code=self._recorded_code(),
            summary=f"replay of {self.run_dir.name} ({calls} tool calls)",
            rounds=int(self._outcome.get("rounds") or 0),
            tokens=0,  # replay is deterministic; no new tokens are spent
            session_id=ctx.session_id,
        )

    async def cancel(self) -> None:
        return None


__all__ = ["ReplayBackend"]
