"""scope_violation = "refuse_tool": an out-of-scope path is data for the model, not the end.

Observed on a governed run: after one correct edit the model named the same
file with a leading slash it invented. The scope gate refused it, correctly,
and the session ended OUT_OF_SCOPE with the work discarded. Same class as the
hook denials ``hook_denial = "refuse_tool"`` (2.6.0) addressed: the refused
call never runs; what changes is whether one refusal ends the run. Default
unchanged.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pxx.config import ConfigError, Settings, load_settings
from pxx.errors import ScopeViolation
from pxx.events import EventBus
from pxx.safety import HookRunner, PermissionMode, ScopeGate
from pxx.tools import ToolContext, default_registry


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _ctx(tmp_path: Path, refuse: bool, bus: EventBus) -> ToolContext:
    root = tmp_path / "proj"
    root.mkdir(exist_ok=True)
    return ToolContext(
        scope=ScopeGate(root),
        hooks=HookRunner(()),
        permission=PermissionMode.EDIT,
        bus=bus,
        cwd=root,
        session_id="t",
        refuse_scope_violations=refuse,
    )


def test_default_is_unchanged_a_violation_ends_the_run(tmp_path):
    ctx = _ctx(tmp_path, refuse=False, bus=EventBus())
    with pytest.raises(ScopeViolation):
        run(default_registry().call("write_file", {"path": "/src/a.txt", "content": "x"}, ctx))
    assert not Path("/src/a.txt").exists()


def test_refuse_tool_returns_the_violation_to_the_model_and_does_not_execute(tmp_path):
    events = []
    bus = EventBus()

    async def _record(e):
        events.append(e)

    bus.subscribe(_record)
    ctx = _ctx(tmp_path, refuse=True, bus=bus)
    result = run(default_registry().call("write_file", {"path": "/src/a.txt", "content": "x"}, ctx))
    assert result.startswith("error: refused by policy:")
    assert "outside scope" in result
    assert not Path("/src/a.txt").exists()
    assert not (tmp_path / "proj" / "src" / "a.txt").exists()
    kinds = [e.kind for e in events]
    assert "tool_denied" in kinds
    assert "tool_call" not in kinds  # never dispatched
    denied = [e for e in events if e.kind == "tool_result"]
    assert denied and denied[-1].data.get("denied") is True


def test_an_in_scope_call_still_runs_after_a_refusal(tmp_path):
    ctx = _ctx(tmp_path, refuse=True, bus=EventBus())
    run(default_registry().call("write_file", {"path": "/src/a.txt", "content": "x"}, ctx))
    out = run(default_registry().call("write_file", {"path": "a.txt", "content": "ok"}, ctx))
    assert not out.startswith("error")
    assert (tmp_path / "proj" / "a.txt").read_text() == "ok"


def test_hook_denial_setting_does_not_govern_scope(tmp_path):
    # the two settings are independent: refuse_denied_tools alone leaves a
    # scope violation terminal
    root = tmp_path / "proj"
    root.mkdir(exist_ok=True)
    ctx = ToolContext(
        scope=ScopeGate(root),
        hooks=HookRunner(()),
        permission=PermissionMode.EDIT,
        bus=EventBus(),
        cwd=root,
        session_id="t",
        refuse_denied_tools=True,
    )
    with pytest.raises(ScopeViolation):
        run(default_registry().call("write_file", {"path": "/src/a.txt", "content": "x"}, ctx))


def test_the_setting_parses_strictly_and_only_from_trusted_config(tmp_path, monkeypatch):
    """scope_violation is part of the gate: honoured from the USER config,
    never from a repo-local pxx.toml (that file lives inside the tree the
    gate guards) -- the hook_denial rule, applied to the scope gate."""
    user_cfg = tmp_path / "home" / "config.toml"
    user_cfg.parent.mkdir()
    monkeypatch.setattr("pxx.config._USER_CONFIG", user_cfg)
    project = tmp_path / "proj"
    project.mkdir()
    user_cfg.write_text('scope_violation = "refuse_tool"\n')
    assert load_settings(project).scope_violation == "refuse_tool"
    assert Settings().scope_violation == "abort"
    user_cfg.write_text('scope_violation = "continue"\n')
    with pytest.raises(ConfigError, match="scope_violation"):
        load_settings(project)
    # repo-local: ignored with a warning, the default stands
    user_cfg.unlink()
    (project / "pxx.toml").write_text('scope_violation = "refuse_tool"\n')
    assert load_settings(project).scope_violation == "abort"
