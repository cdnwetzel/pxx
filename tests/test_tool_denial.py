"""hook_denial = "refuse_tool": a PreToolUse denial is data for the model, not the end.

Measured 2026-09-22 across eight governed runs of one task: every run ended on
a refusal of a command the model did not need (mkdir before write_file, cd to
its own cwd, ls) AFTER the work was written, and the safety net reset the tree
each time. Nothing here widens what is allowed: the denied call still never
runs. What changes is whether one refusal ends the whole run.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pxx.config import ConfigError, Settings, load_settings
from pxx.errors import HookDenied
from pxx.events import EventBus
from pxx.safety import Hook, HookRunner, PermissionMode, ScopeGate
from pxx.tools import ToolContext, default_registry


def run(coro):
    return asyncio.run(coro)


def _denying_hook(tmp_path: Path) -> Hook:
    script = tmp_path / "deny.sh"
    script.write_text(
        "#!/bin/sh\ncat >/dev/null\necho 'policy DENY: no policy matched' >&2\nexit 2\n"
    )
    script.chmod(0o755)
    return Hook(event="PreToolUse", command=str(script))


def _ctx(tmp_path: Path, refuse: bool, bus: EventBus) -> ToolContext:
    root = tmp_path / "proj"
    root.mkdir(exist_ok=True)
    return ToolContext(
        scope=ScopeGate(root),
        hooks=HookRunner((_denying_hook(tmp_path),)),
        permission=PermissionMode.EDIT,
        bus=bus,
        cwd=root,
        session_id="t",
        refuse_denied_tools=refuse,
    )


def test_default_is_unchanged_a_denial_ends_the_run(tmp_path):
    ctx = _ctx(tmp_path, refuse=False, bus=EventBus())
    with pytest.raises(HookDenied):
        run(default_registry().call("write_file", {"path": "a.txt", "content": "x"}, ctx))
    assert not (tmp_path / "proj" / "a.txt").exists()


def test_refuse_tool_returns_the_denial_to_the_model_and_does_not_execute(tmp_path):
    events = []
    bus = EventBus()

    async def _record(e):
        events.append(e)

    bus.subscribe(_record)
    ctx = _ctx(tmp_path, refuse=True, bus=bus)

    result = run(default_registry().call("write_file", {"path": "a.txt", "content": "x"}, ctx))

    assert result.startswith("error: refused by policy:")
    assert "no policy matched" in result
    assert not (tmp_path / "proj" / "a.txt").exists(), "a refused call must not run"
    kinds = [e.kind for e in events]
    assert "tool_denied" in kinds
    denied = next(e for e in events if e.kind == "tool_denied")
    assert denied.data["tool"] == "write_file"
    assert denied.data["reason"] == "hook_denied"
    # metadata only: names and sizes, never the values
    assert "args" not in denied.data
    assert denied.data["arg_names"] == sorted(denied.data["arg_sizes"])
    assert "no policy matched" not in str(denied.data)
    # and it is a tool_result the model sees as an error, not a session end
    res = next(e for e in events if e.kind == "tool_result")
    assert res.data["error"] is True and res.data.get("denied") is True
    assert "tool_call" not in kinds, "a refused call is never announced as a call"


def test_the_setting_parses_strictly(tmp_path, monkeypatch):
    """hook_denial is an exec surface: honoured from the USER config, never
    from a repo-local pxx.toml (that file lives inside the tree the hook
    guards)."""
    user_cfg = tmp_path / "home" / "config.toml"
    user_cfg.parent.mkdir()
    monkeypatch.setattr("pxx.config._USER_CONFIG", user_cfg)
    project = tmp_path / "proj"
    project.mkdir()
    user_cfg.write_text('hook_denial = "refuse_tool"\n')
    s = load_settings(project)
    assert s.hook_denial == "refuse_tool"
    assert Settings().hook_denial == "abort"
    user_cfg.write_text('hook_denial = "ignore"\n')
    with pytest.raises(ConfigError, match="hook_denial"):
        load_settings(project)
    # repo-local: ignored, and the default stands
    user_cfg.write_text("")
    (project / "pxx.toml").write_text('hook_denial = "refuse_tool"\n')
    assert load_settings(project).hook_denial == "abort"


def test_the_refusal_names_no_hook_command(tmp_path: Path) -> None:
    """The model-facing error and the audit event must not carry the hook's
    command line: it is an arbitrary shell string and may hold credentials."""
    hook = _denying_hook(tmp_path)
    reg = default_registry()
    ctx = _ctx(tmp_path, refuse=True, bus=EventBus())
    result = run(reg.call("read_file", {"path": "a.txt"}, ctx))
    assert hook.command not in result
    assert "no policy matched" in result  # the hook's own stderr is the feedback
