"""Safety control-plane tests: permission modes, scope, hooks, budgets."""

from __future__ import annotations

import asyncio
import sys
import time

import pytest

from pxx.errors import BudgetExceeded, HookDenied, ScopeViolation
from pxx.safety import (
    BudgetGuard,
    Budgets,
    Hook,
    HookRunner,
    PermissionMode,
    ScopeGate,
    canonicalize,
)


def run(coro):
    return asyncio.run(coro)


# --- PermissionMode ---------------------------------------------------------


def test_permission_write_and_shell_matrix():
    assert not PermissionMode.ASK.can_write
    assert not PermissionMode.PLAN.can_write
    assert PermissionMode.EDIT.can_write
    assert PermissionMode.AUTO.can_write
    assert PermissionMode.AUTO.can_shell
    assert not PermissionMode.EDIT.can_shell
    assert not PermissionMode.ASK.can_shell


# --- ScopeGate --------------------------------------------------------------


def test_scope_allows_inside_root(tmp_path):
    gate = ScopeGate(tmp_path)
    target = tmp_path / "src" / "main.py"
    assert gate.check(target) == target


def test_scope_denies_traversal(tmp_path):
    gate = ScopeGate(tmp_path / "sub")
    with pytest.raises(ScopeViolation):
        gate.check(tmp_path / "other" / "x.py")


def test_scope_restricts_to_prefixes(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "docs").mkdir()
    gate = ScopeGate(tmp_path, scope=("src",))
    assert gate.in_scope(tmp_path / "src" / "a.py")
    assert not gate.in_scope(tmp_path / "docs" / "a.md")
    assert not gate.in_scope(tmp_path / "a.py")


def test_scope_resolves_symlink_escapes(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("nope")
    root = tmp_path / "root"
    root.mkdir()
    link = root / "link"
    link.symlink_to(outside)
    gate = ScopeGate(root)
    with pytest.raises(ScopeViolation):
        gate.check(link / "secret.txt")


def test_scope_dotdot_escape_denied(tmp_path):
    gate = ScopeGate(tmp_path / "proj")
    with pytest.raises(ScopeViolation):
        gate.check("../../etc/passwd")


def test_trusted_paths_extend_scope(tmp_path):
    extra = tmp_path / "trusted"
    extra.mkdir()
    gate = ScopeGate(tmp_path / "proj", trusted=(str(extra),))
    assert gate.in_scope(extra / "x.py")


def test_check_write_requires_write_permission(tmp_path):
    gate = ScopeGate(tmp_path)
    with pytest.raises(ScopeViolation, match="permission"):
        gate.check_write(tmp_path / "a.py", PermissionMode.ASK)
    with pytest.raises(ScopeViolation, match="permission"):
        gate.check_write(tmp_path / "a.py", PermissionMode.PLAN)
    assert gate.check_write(tmp_path / "a.py", PermissionMode.EDIT)


def test_canonicalize_relative_to_cwd(tmp_path):
    assert canonicalize("a/b.py", cwd=tmp_path) == tmp_path / "a" / "b.py"


# --- HookRunner -------------------------------------------------------------


def _hook_script(tmp_path, code: int) -> str:
    script = tmp_path / f"hook{code}.sh"
    script.write_text(f"#!/bin/sh\ncat >/dev/null\nexit {code}\n")
    script.chmod(0o755)
    return str(script)


def test_pre_hook_allow(tmp_path):
    hooks = HookRunner((Hook(event="PreToolUse", command=_hook_script(tmp_path, 0)),))
    run(hooks.run_pre("write_file", {"path": "x"}))


def test_pre_hook_deny_raises(tmp_path):
    hooks = HookRunner((Hook(event="PreToolUse", command=_hook_script(tmp_path, 2)),))
    with pytest.raises(HookDenied, match=r"docs/CONFIG\.md"):  # K9: actionable pointer
        run(hooks.run_pre("write_file", {"path": "x"}))


def test_hook_error_exit_is_fail_closed(tmp_path):
    hooks = HookRunner((Hook(event="PreToolUse", command=_hook_script(tmp_path, 1)),))
    with pytest.raises(HookDenied):
        run(hooks.run_pre("write_file", {}))


def test_hook_matcher_filters_tools(tmp_path):
    hooks = HookRunner(
        (Hook(event="PreToolUse", command=_hook_script(tmp_path, 2), matcher="shell"),)
    )
    run(hooks.run_pre("read_file", {}))  # not matched: allowed
    with pytest.raises(HookDenied):
        run(hooks.run_pre("run_shell", {}))


def test_hook_timeout_denies(tmp_path):
    hooks = HookRunner(
        (
            Hook(
                event="PreToolUse",
                command=f"{sys.executable} -c 'import time;time.sleep(5)'",
                timeout=0.2,
            ),
        )
    )
    with pytest.raises(HookDenied, match="timed out"):
        run(hooks.run_pre("write_file", {}))


def test_post_hook_receives_result_preview(tmp_path):
    capture = tmp_path / "payload.json"
    script = tmp_path / "capture.sh"
    script.write_text(f"#!/bin/sh\ncat > {capture}\nexit 0\n")
    script.chmod(0o755)
    hooks = HookRunner((Hook(event="PostToolUse", command=str(script)),))
    run(hooks.run_post("read_file", {"path": "x"}, "R" * 5000))
    import json as _json

    payload = _json.loads(capture.read_text())
    assert payload["tool"] == "read_file"
    assert len(payload["result_preview"]) == 1000


# --- BudgetGuard ------------------------------------------------------------


def test_budget_rounds_cap():
    guard = BudgetGuard(Budgets(max_rounds=2))
    guard.consume(rounds=1)
    guard.consume(rounds=1)
    with pytest.raises(BudgetExceeded, match="max_rounds"):
        guard.consume(rounds=1)


def test_budget_tokens_cap():
    guard = BudgetGuard(Budgets(max_tokens=100))
    guard.consume(tokens=60)
    with pytest.raises(BudgetExceeded, match="max_tokens"):
        guard.consume(tokens=60)


def test_budget_cost_and_diff_caps():
    guard = BudgetGuard(Budgets(max_cost_usd=1.0, max_diff_lines=10))
    with pytest.raises(BudgetExceeded, match="max_cost_usd"):
        guard.consume(cost=1.01)
    guard2 = BudgetGuard(Budgets(max_diff_lines=10))
    with pytest.raises(BudgetExceeded, match="max_diff_lines"):
        guard2.consume(diff_lines=11)


def test_budget_wall_clock():
    guard = BudgetGuard(Budgets(max_wall_seconds=0.05))
    time.sleep(0.06)
    with pytest.raises(BudgetExceeded, match="max_wall_seconds"):
        guard.check_clock()


def test_budget_snapshot():
    guard = BudgetGuard(Budgets())
    guard.consume(rounds=1, tokens=5)
    snap = guard.snapshot()
    assert snap["rounds"] == 1 and snap["tokens"] == 5
    assert snap["remaining_seconds"] > 0
