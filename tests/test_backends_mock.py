"""Tests for pxx.backends.mock — deterministic scripted backend."""

from __future__ import annotations

import asyncio

import pytest

from pxx.backends.base import SessionContext
from pxx.backends.mock import MockBackend, make_tool_context
from pxx.config import Settings
from pxx.errors import BackendError
from pxx.events import EventBus
from pxx.outcome import TerminalCode
from pxx.safety import BudgetGuard, HookRunner, ScopeGate


class FakeRegistry:
    """Duck-typed ToolRegistry stand-in (pxx.tools built in parallel)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.seen_ctx = None

    def specs(self) -> list[dict]:
        return [{"type": "function", "function": {"name": "fake_tool"}}]

    async def call(self, name: str, args: dict, ctx) -> str:
        self.calls.append((name, args))
        self.seen_ctx = ctx
        return f"ran {name}"


def make_ctx(tmp_path, tools=None, *, settings=None) -> SessionContext:
    return SessionContext(
        settings=settings or Settings(),
        bus=EventBus(),
        scope=ScopeGate(tmp_path),
        hooks=HookRunner(),
        budgets=BudgetGuard(Settings().budgets),
        tools=tools or FakeRegistry(),
        memory=None,
        session_id="test",
        project=tmp_path.name,
        cwd=tmp_path,
        cancel_event=asyncio.Event(),
    )


def test_say_then_done_completes(tmp_path):
    backend = MockBackend([{"say": "hello"}, {"done": "all done"}])
    ctx = make_ctx(tmp_path)
    outcome = asyncio.run(backend.run("task", ctx))
    assert outcome.code is TerminalCode.COMPLETED
    assert outcome.summary == "all done"
    kinds = [e.kind for e in ctx.bus.history]
    assert "model_response" in kinds


def test_tool_step_calls_registry_with_tool_context(tmp_path):
    tools = FakeRegistry()
    backend = MockBackend(
        [{"tool": "write_file", "args": {"path": "a.py", "content": "x"}}, {"done": "ok"}]
    )
    ctx = make_ctx(tmp_path, tools)
    outcome = asyncio.run(backend.run("task", ctx))
    assert outcome.code is TerminalCode.COMPLETED
    assert outcome.rounds == 1
    assert tools.calls == [("write_file", {"path": "a.py", "content": "x"})]
    # ctx-built ToolContext carries the gates
    assert tools.seen_ctx is not None
    assert tools.seen_ctx.scope is ctx.scope
    assert tools.seen_ctx.cwd == tmp_path
    assert tools.seen_ctx.permission is ctx.settings.permission


def test_tool_result_becomes_summary_when_script_exhausts(tmp_path):
    backend = MockBackend([{"tool": "read_file", "args": {"path": "a.py"}}])
    outcome = asyncio.run(backend.run("task", make_ctx(tmp_path)))
    assert outcome.code is TerminalCode.COMPLETED
    assert outcome.summary == "ran read_file"


def test_cancel_before_run_interrupts(tmp_path):
    backend = MockBackend([{"say": "hi"}, {"done": "done"}])

    async def main():
        await backend.cancel()
        return await backend.run("task", make_ctx(tmp_path))

    outcome = asyncio.run(main())
    assert outcome.code is TerminalCode.INTERRUPTED


def test_cancel_event_interrupts(tmp_path):
    backend = MockBackend([{"say": "hi"}, {"done": "done"}])
    ctx = make_ctx(tmp_path)
    ctx.cancel_event.set()
    outcome = asyncio.run(backend.run("task", ctx))
    assert outcome.code is TerminalCode.INTERRUPTED


def test_unknown_step_raises_backend_error(tmp_path):
    backend = MockBackend([{"bogus": 1}])
    with pytest.raises(BackendError):
        asyncio.run(backend.run("task", make_ctx(tmp_path)))


def test_gate_errors_propagate(tmp_path):
    class DenyRegistry(FakeRegistry):
        async def call(self, name, args, ctx):
            from pxx.errors import ScopeViolation

            raise ScopeViolation("nope")

    backend = MockBackend([{"tool": "write_file", "args": {}}])
    with pytest.raises(Exception, match="nope"):
        asyncio.run(backend.run("task", make_ctx(tmp_path, DenyRegistry())))


def test_make_tool_context_fallback_shape(tmp_path):
    ctx = make_ctx(tmp_path)
    tool_ctx = make_tool_context(ctx)
    assert tool_ctx.bus is ctx.bus
    assert tool_ctx.memory is None
