"""Tests for ToolRegistry gating/dispatch and the tool schema surface."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from pxx.errors import BudgetExceeded, HookDenied, ScopeViolation
from pxx.events import EventBus
from pxx.safety import Hook, HookRunner, PermissionMode, ScopeGate
from pxx.tools import (
    EVENT_PREVIEW_CHARS,
    ToolContext,
    ToolRegistry,
    ToolSpec,
    default_registry,
)


@dataclass
class FakeTool:
    spec: ToolSpec
    result: str = "ok"
    exc: Exception | None = None
    seen_args: dict[str, Any] | None = None

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        self.seen_args = args
        if self.exc is not None:
            raise self.exc
        return self.result


def make_ctx(
    root: Path,
    *,
    permission: PermissionMode = PermissionMode.AUTO,
    hooks: tuple[Hook, ...] = (),
    bus: EventBus | None = None,
) -> ToolContext:
    return ToolContext(
        scope=ScopeGate(root),
        hooks=HookRunner(hooks),
        permission=permission,
        bus=bus or EventBus(),
        cwd=root,
        session_id="test-session",
    )


def fake_tool(name: str = "fake", *, mutating: bool = False, **kwargs: Any) -> FakeTool:
    return FakeTool(
        spec=ToolSpec(
            name=name,
            description=f"{name} tool",
            parameters={"type": "object", "properties": {}},
            mutating=mutating,
        ),
        **kwargs,
    )


def test_register_and_specs_schema() -> None:
    reg = ToolRegistry()
    reg.register(fake_tool("alpha"))
    reg.register(fake_tool("beta", mutating=True))
    specs = reg.specs()
    assert [s["type"] for s in specs] == ["function", "function"]
    assert specs[0]["function"] == {
        "name": "alpha",
        "description": "alpha tool",
        "parameters": {"type": "object", "properties": {}},
    }
    assert {s["function"]["name"] for s in specs} == {"alpha", "beta"}


def test_duplicate_registration_raises() -> None:
    reg = ToolRegistry()
    reg.register(fake_tool("alpha"))
    with pytest.raises(ValueError, match="duplicate"):
        reg.register(fake_tool("alpha"))


def test_default_registry_has_builtins() -> None:
    reg = default_registry()  # no args
    names = {s["function"]["name"] for s in reg.specs()}
    assert names == {
        "read_file",
        "write_file",
        "edit_file",
        "list_files",
        "search_files",
        "run_shell",
        "recall_memory",
        "remember",
    }
    assert len(reg) == 8


def test_call_unknown_tool_returns_error_string(tmp_path: Path) -> None:
    async def main() -> str:
        return await ToolRegistry().call("nope", {}, make_ctx(tmp_path))

    result = asyncio.run(main())
    assert result.startswith("error: unknown tool")


def test_call_success_emits_events(tmp_path: Path) -> None:
    bus = EventBus()
    reg = ToolRegistry()
    reg.register(fake_tool("alpha", result="done"))

    async def main() -> str:
        return await reg.call("alpha", {"x": 1}, make_ctx(tmp_path, bus=bus))

    assert asyncio.run(main()) == "done"
    kinds = [e.kind for e in bus.history]
    # the broker authorizes first, then the tool executes
    assert kinds == ["tool_action_proposed", "policy_decision", "tool_call", "tool_result"]
    call_ev, result_ev = (e for e in bus.history if e.kind in ("tool_call", "tool_result"))
    assert call_ev.data["tool"] == "alpha"
    assert call_ev.data["args"] == {"x": 1}
    assert call_ev.session_id == "test-session"
    assert result_ev.data == {"tool": "alpha", "result_preview": "done", "error": False}


def test_result_preview_truncated_in_event(tmp_path: Path) -> None:
    bus = EventBus()
    reg = ToolRegistry()
    reg.register(fake_tool(result="x" * (EVENT_PREVIEW_CHARS * 3)))

    async def main() -> str:
        return await reg.call("fake", {}, make_ctx(tmp_path, bus=bus))

    result = asyncio.run(main())
    assert len(result) == EVENT_PREVIEW_CHARS * 3  # full result returned to model
    preview = bus.history[-1].data["result_preview"]
    assert len(preview) == EVENT_PREVIEW_CHARS


def test_long_arg_values_truncated_in_tool_call_event(tmp_path: Path) -> None:
    bus = EventBus()
    reg = ToolRegistry()
    reg.register(fake_tool())

    async def main() -> str:
        return await reg.call("fake", {"content": "y" * 1000}, make_ctx(tmp_path, bus=bus))

    asyncio.run(main())
    tool_call = next(e for e in bus.history if e.kind == "tool_call")
    arg_value = tool_call.data["args"]["content"]
    assert len(arg_value) < 1000
    assert arg_value.endswith("[truncated]")


def test_mutating_tool_denied_in_read_only_modes(tmp_path: Path) -> None:
    reg = ToolRegistry()
    reg.register(fake_tool(mutating=True))
    for mode in (PermissionMode.ASK, PermissionMode.PLAN):
        with pytest.raises(ScopeViolation, match="not permitted"):
            asyncio.run(reg.call("fake", {}, make_ctx(tmp_path, permission=mode)))


def test_mutating_tool_allowed_in_edit_and_auto(tmp_path: Path) -> None:
    reg = ToolRegistry()
    reg.register(fake_tool(mutating=True, result="wrote"))
    for mode in (PermissionMode.EDIT, PermissionMode.AUTO):

        async def main(mode: PermissionMode = mode) -> str:
            return await reg.call("fake", {}, make_ctx(tmp_path, permission=mode))

        assert asyncio.run(main()) == "wrote"


def test_tool_exception_returned_as_error_string(tmp_path: Path) -> None:
    bus = EventBus()
    reg = ToolRegistry()
    reg.register(fake_tool(exc=RuntimeError("boom")))

    async def main() -> str:
        return await reg.call("fake", {}, make_ctx(tmp_path, bus=bus))

    result = asyncio.run(main())
    assert result == "error: RuntimeError: boom"
    assert bus.history[-1].data["error"] is True


def test_gate_errors_propagate(tmp_path: Path) -> None:
    for exc in (
        ScopeViolation("out of scope"),
        HookDenied("hook says no"),
        BudgetExceeded("max_rounds", "25"),
    ):
        reg = ToolRegistry()
        reg.register(fake_tool(exc=exc))
        with pytest.raises(type(exc)):
            asyncio.run(reg.call("fake", {}, make_ctx(tmp_path)))


def test_pre_hook_denial_propagates(tmp_path: Path) -> None:
    reg = ToolRegistry()
    tool = fake_tool()
    reg.register(tool)
    ctx = make_ctx(tmp_path, hooks=(Hook(event="PreToolUse", command="false"),))
    with pytest.raises(HookDenied):
        asyncio.run(reg.call("fake", {}, ctx))
    assert tool.seen_args is None  # tool never ran


def test_pre_hook_allow_lets_tool_run(tmp_path: Path) -> None:
    reg = ToolRegistry()
    tool = fake_tool(result="ran")
    reg.register(tool)
    ctx = make_ctx(tmp_path, hooks=(Hook(event="PreToolUse", command="true"),))

    async def main() -> str:
        return await reg.call("fake", {"a": 1}, ctx)

    assert asyncio.run(main()) == "ran"
    assert tool.seen_args == {"a": 1}


def test_post_hook_denial_propagates(tmp_path: Path) -> None:
    reg = ToolRegistry()
    reg.register(fake_tool())
    ctx = make_ctx(
        tmp_path,
        hooks=(Hook(event="PostToolUse", command="false"),),
    )
    with pytest.raises(HookDenied):
        asyncio.run(reg.call("fake", {}, ctx))


def test_hook_matcher_skips_non_matching_tools(tmp_path: Path) -> None:
    reg = ToolRegistry()
    reg.register(fake_tool("other"))
    ctx = make_ctx(
        tmp_path,
        hooks=(Hook(event="PreToolUse", command="false", matcher="run_shell"),),
    )

    async def main() -> str:
        return await reg.call("other", {}, ctx)

    assert asyncio.run(main()) == "ok"
