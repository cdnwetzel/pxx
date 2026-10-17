"""shell_unguarded = "refuse_tool": a run_shell with no safeguard is refused, not the end.

Observed 2026-10-17 on the tutorial: capable models (a 27B, Qwen3-Coder) made
the correct edit, then reached for ``pytest`` to verify it. With no shell
safeguard configured the session ended HOOKS_MISSING before ``--commit``, so
only models that never verify could finish. The command still never runs;
what changes is whether one attempt ends the run. Same treatment as
``hook_denial`` / ``scope_violation``; unlike them, refusal is the default.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pxx.config import ConfigError, Settings, load_settings
from pxx.errors import HooksMissing, ShellUnguarded
from pxx.events import EventBus
from pxx.safety import HookRunner, PermissionMode, ScopeGate
from pxx.tools import ToolContext, default_registry


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _ctx(
    tmp_path: Path,
    *,
    refuse: bool,
    bus: EventBus | None = None,
    permission: PermissionMode = PermissionMode.EDIT,
    sandbox_shell: bool = False,
    refuse_scope_violations: bool = False,
) -> ToolContext:
    root = tmp_path / "proj"
    root.mkdir(exist_ok=True)
    return ToolContext(
        scope=ScopeGate(root),
        hooks=HookRunner(()),
        permission=permission,
        bus=bus or EventBus(),
        cwd=root,
        session_id="t",
        sandbox_shell=sandbox_shell,
        refuse_unguarded_shell=refuse,
        refuse_scope_violations=refuse_scope_violations,
    )


def _touch_cmd(tmp_path: Path) -> dict[str, str]:
    """A command whose only effect is visible on disk, so 'not executed' is checkable."""
    return {"command": f"touch {tmp_path / 'proj' / 'ran.txt'}"}


@pytest.mark.parametrize("mode", [PermissionMode.EDIT, PermissionMode.AUTO])
def test_abort_ends_the_run_as_before(tmp_path, mode):
    ctx = _ctx(tmp_path, refuse=False, permission=mode)
    with pytest.raises(ShellUnguarded):
        run(default_registry().call("run_shell", _touch_cmd(tmp_path), ctx))
    assert not (tmp_path / "proj" / "ran.txt").exists()


def test_shell_unguarded_is_still_a_hooks_missing():
    # existing `except HooksMissing` handling (the HOOKS_MISSING terminal)
    # keeps working under shell_unguarded = "abort"
    assert issubclass(ShellUnguarded, HooksMissing)


@pytest.mark.parametrize("mode", [PermissionMode.EDIT, PermissionMode.AUTO])
def test_refuse_tool_returns_the_refusal_and_does_not_execute(tmp_path, mode):
    events = []
    bus = EventBus()

    async def _record(e):
        events.append(e)

    bus.subscribe(_record)
    ctx = _ctx(tmp_path, refuse=True, bus=bus, permission=mode)
    result = run(default_registry().call("run_shell", _touch_cmd(tmp_path), ctx))
    assert result.startswith("error: refused by policy:")
    assert "shell safeguard" in result
    assert not (tmp_path / "proj" / "ran.txt").exists()
    denied_ev = [e for e in events if e.kind == "tool_denied"][-1]
    assert denied_ev.data["reason"] == "shell_unguarded"
    # metadata only: the argument name, never the command text
    assert denied_ev.data["arg_names"] == ["command"] and "touch" not in str(denied_ev.data)
    results = [e for e in events if e.kind == "tool_result"]
    assert results and results[-1].data.get("denied") is True


def test_other_tools_still_run_after_a_refusal(tmp_path):
    ctx = _ctx(tmp_path, refuse=True)
    run(default_registry().call("run_shell", _touch_cmd(tmp_path), ctx))
    out = run(default_registry().call("write_file", {"path": "a.txt", "content": "ok"}, ctx))
    assert not out.startswith("error")
    assert (tmp_path / "proj" / "a.txt").read_text() == "ok"


def test_a_broken_safeguard_still_aborts(tmp_path, monkeypatch):
    # sandbox_shell=true with no sandboxer is setup the operator must fix --
    # never refused back to the model, even with refusal on
    monkeypatch.setattr("pxx.tools.shell._sandbox_available", lambda: False)
    ctx = _ctx(tmp_path, refuse=True, sandbox_shell=True)
    with pytest.raises(HooksMissing, match="no sandbox binary") as info:
        run(default_registry().call("run_shell", _touch_cmd(tmp_path), ctx))
    assert not isinstance(info.value, ShellUnguarded)


def test_scope_violation_setting_does_not_govern_the_shell(tmp_path):
    # the settings are independent
    ctx = _ctx(tmp_path, refuse=False, refuse_scope_violations=True)
    with pytest.raises(ShellUnguarded):
        run(default_registry().call("run_shell", _touch_cmd(tmp_path), ctx))


def test_session_default_refuses(tmp_path):
    # Settings defaults to refusal; the bare ToolContext stays strict
    assert Settings().shell_unguarded == "refuse_tool"
    root = tmp_path / "proj"
    root.mkdir()
    assert (
        ToolContext(
            scope=ScopeGate(root),
            hooks=HookRunner(()),
            permission=PermissionMode.EDIT,
            bus=EventBus(),
            cwd=root,
        ).refuse_unguarded_shell
        is False
    )


def test_the_setting_parses_strictly_and_only_from_trusted_config(tmp_path, monkeypatch):
    user_cfg = tmp_path / "home" / "config.toml"
    user_cfg.parent.mkdir()
    monkeypatch.setattr("pxx.config._USER_CONFIG", user_cfg)
    project = tmp_path / "proj"
    project.mkdir()
    user_cfg.write_text('shell_unguarded = "abort"\n')
    assert load_settings(project).shell_unguarded == "abort"
    user_cfg.write_text('shell_unguarded = "allow"\n')
    with pytest.raises(ConfigError, match="shell_unguarded"):
        load_settings(project)
    # repo-local: ignored with a warning, the default stands
    user_cfg.unlink()
    (project / "pxx.toml").write_text('shell_unguarded = "abort"\n')
    assert load_settings(project).shell_unguarded == "refuse_tool"


def test_a_refused_command_never_appears_in_the_event_stream(tmp_path):
    # Codex review, 2cd11af: the tool_call event (args preview) used to be
    # emitted before the safeguard check, so a refused command's text -- and
    # any secret in it -- reached events/audit. The preflight now runs first.
    events = []
    bus = EventBus()

    async def _record(e):
        events.append(e)

    bus.subscribe(_record)
    ctx = _ctx(tmp_path, refuse=True, bus=bus)
    sentinel = "REVIEW_SENTINEL_7f3a"
    run(default_registry().call("run_shell", {"command": f"TOKEN={sentinel} pytest"}, ctx))
    assert "tool_call" not in [e.kind for e in events]
    assert sentinel not in repr([(e.kind, e.data) for e in events])
