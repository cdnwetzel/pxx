"""Scripted backend for tests — deterministic, no I/O beyond tools.

``MockBackend(script)`` executes a list of steps in order:

- ``{"tool": name, "args": {...}}`` — call ``ctx.tools.call`` with a
  :class:`~pxx.tools.ToolContext` built from the session context.
- ``{"say": text}`` — emit a ``model_response`` event with the text.
- ``{"done": summary}`` — terminate the run with ``COMPLETED``.

Gate errors raised by tools propagate (fail-closed); the session layer maps
them to terminal codes.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from ..errors import BackendError
from ..outcome import RunOutcome, TerminalCode
from .base import BackendCapabilities, SessionContext

log = logging.getLogger("pxx.backends.mock")

Step = dict[str, Any]


def make_tool_context(ctx: SessionContext) -> Any:
    """Build a ``tools.ToolContext`` from a :class:`SessionContext`."""
    from ..tools import ToolContext

    return ToolContext(
        scope=ctx.scope,
        hooks=ctx.hooks,
        permission=ctx.settings.permission,
        bus=ctx.bus,
        memory=ctx.memory,
        cwd=ctx.cwd,
        session_id=ctx.session_id,
        sandbox_shell=ctx.settings.sandbox_shell,
        profile=ctx.profile,
    )


class MockBackend:
    """Deterministic scripted backend. No I/O beyond the tool registry."""

    name: ClassVar[str] = "mock"
    capabilities: ClassVar[BackendCapabilities] = BackendCapabilities(
        streaming=False, tools=True, interactive=False, headless=True
    )

    def __init__(self, script: list[Step] | None = None) -> None:
        self.script = list(script or [])
        self._cancelled = False

    async def cancel(self) -> None:
        self._cancelled = True

    async def run(self, task: str, ctx: SessionContext) -> RunOutcome:
        tool_ctx = make_tool_context(ctx)
        rounds = 0
        last_text = ""
        for step in self.script:
            if self._cancelled or ctx.cancel_event.is_set():
                return RunOutcome(
                    code=TerminalCode.INTERRUPTED,
                    summary="cancelled",
                    rounds=rounds,
                    session_id=ctx.session_id,
                )
            if "tool" in step:
                name = str(step["tool"])
                args = dict(step.get("args") or {})
                await ctx.bus.emit(
                    "model_response",
                    {"backend": "mock", "tool_call": name},
                    session_id=ctx.session_id,
                )
                result = await ctx.tools.call(name, args, tool_ctx)
                rounds += 1
                ctx.budgets.consume(rounds=1)
                last_text = str(result)
            elif "say" in step:
                text = str(step["say"])
                await ctx.bus.emit(
                    "model_response",
                    {"backend": "mock", "text": text[:300]},
                    session_id=ctx.session_id,
                )
                last_text = text
            elif "done" in step:
                return RunOutcome(
                    code=TerminalCode.COMPLETED,
                    summary=str(step["done"]),
                    rounds=rounds,
                    session_id=ctx.session_id,
                )
            else:
                raise BackendError(f"mock step needs 'tool', 'say' or 'done': {step!r}")
        return RunOutcome(
            code=TerminalCode.COMPLETED,
            summary=last_text or "script exhausted",
            rounds=rounds,
            session_id=ctx.session_id,
        )
