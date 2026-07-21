"""Tests for pxx.backends.aider — fake aider shell scripts, no real aider/git."""

from __future__ import annotations

import asyncio
import shutil

import pytest

from pxx.backends.aider import AiderBackend
from pxx.backends.base import SessionContext
from pxx.config import ModelRef, Settings
from pxx.errors import BackendError, BackendUnavailable
from pxx.events import EventBus
from pxx.outcome import TerminalCode
from pxx.safety import BudgetGuard, HookRunner, PermissionMode, ScopeGate


class FakeRegistry:
    def specs(self) -> list[dict]:
        return []

    async def call(self, name: str, args: dict, ctx) -> str:  # pragma: no cover
        raise AssertionError("aider backend never calls pxx tools")


def make_ctx(tmp_path, *, settings=None, scope: ScopeGate | None = None) -> SessionContext:
    return SessionContext(
        settings=settings or Settings(),
        bus=EventBus(),
        scope=scope or ScopeGate(tmp_path),
        hooks=HookRunner(),
        budgets=BudgetGuard(Settings().budgets),
        tools=FakeRegistry(),
        memory=None,
        session_id="test",
        project=tmp_path.name,
        cwd=tmp_path,
        cancel_event=asyncio.Event(),
    )


def fake_aider(tmp_path, body: str) -> str:
    script = tmp_path / "aider-fake"
    script.write_text(f"#!/bin/sh\n{body}\n")
    script.chmod(0o755)
    return str(script)


def test_missing_binary_raises_backend_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda cmd: None)
    with pytest.raises(BackendUnavailable, match=r"pip install pxx-orchestrator\[aider\]"):
        AiderBackend()


def test_successful_run_streams_lines(tmp_path):
    path = fake_aider(tmp_path, "echo 'line one'\necho 'line two'")
    ctx = make_ctx(tmp_path)
    outcome = asyncio.run(AiderBackend(aider_path=path).run("do it", ctx))
    assert outcome.code is TerminalCode.COMPLETED
    assert "line two" in outcome.summary
    lines = [e.data["line"] for e in ctx.bus.history if e.kind == "model_response"]
    assert lines == ["line one", "line two"]


def test_argv_permission_and_no_git(tmp_path):
    path = fake_aider(tmp_path, 'echo "ARGS: $@"')
    settings = Settings(permission=PermissionMode.ASK)
    ctx = make_ctx(tmp_path, settings=settings)
    outcome = asyncio.run(AiderBackend(aider_path=path).run("my task", ctx))
    args = outcome.summary
    assert "--message my task" in args
    assert "--yes-always" in args and "--no-stream" in args and "--no-pretty" in args
    assert "--chat-mode ask" in args  # ASK permission maps to ask mode
    assert "--no-git" in args  # tmp_path is not a git repo
    assert "--read" in args  # scope/memory context file


def test_edit_permission_uses_default_code_mode(tmp_path):
    path = fake_aider(tmp_path, 'echo "ARGS: $@"')
    settings = Settings(permission=PermissionMode.EDIT)
    ctx = make_ctx(tmp_path, settings=settings)
    outcome = asyncio.run(AiderBackend(aider_path=path).run("t", ctx))
    assert "--chat-mode" not in outcome.summary


def test_ollama_env_and_model_string(tmp_path):
    path = fake_aider(tmp_path, 'echo "BASE=$OLLAMA_API_BASE MODEL=$7 $8"')
    settings = Settings(model=ModelRef(provider="ollama", model="qwen2.5-coder:7b"))
    ctx = make_ctx(tmp_path, settings=settings)
    outcome = asyncio.run(AiderBackend(aider_path=path).run("t", ctx))
    assert "BASE=http://localhost:11434" in outcome.summary
    lines = [e.data["line"] for e in ctx.bus.history if e.kind == "model_response"]
    assert any("ollama_chat/qwen2.5-coder:7b" in line for line in lines)


def test_openai_compatible_env_and_model_string(tmp_path):
    path = fake_aider(tmp_path, 'echo "BASE=$OPENAI_API_BASE KEY=$OPENAI_API_KEY"')
    settings = Settings(
        model=ModelRef(provider="vllm", model="devstral", base_url="http://gpu:8000")
    )
    ctx = make_ctx(tmp_path, settings=settings)
    outcome = asyncio.run(AiderBackend(aider_path=path).run("t", ctx))
    assert "BASE=http://gpu:8000/v1" in outcome.summary
    assert "KEY=dummy" in outcome.summary
    assert AiderBackend._model_string(settings.model) == "openai/devstral"


def test_nonzero_exit_raises_backend_error_with_stderr_tail(tmp_path):
    path = fake_aider(tmp_path, "echo 'fatal: exploded' >&2\nexit 3")
    with pytest.raises(BackendError, match=r"exited with code 3.*exploded"):
        asyncio.run(AiderBackend(aider_path=path).run("t", make_ctx(tmp_path)))


def test_cancel_terminates_process(tmp_path):
    path = fake_aider(tmp_path, "sleep 30")
    backend = AiderBackend(aider_path=path)

    async def main():
        task = asyncio.ensure_future(backend.run("t", make_ctx(tmp_path)))
        await asyncio.sleep(0.3)
        await backend.cancel()
        return await task

    outcome = asyncio.run(main())
    assert outcome.code is TerminalCode.INTERRUPTED


def test_git_diff_reporting(tmp_path, monkeypatch):
    path = fake_aider(tmp_path, "echo done")
    backend = AiderBackend(aider_path=path)

    async def fake_git(cwd, *args):
        if args[:2] == ("rev-parse", "HEAD"):
            return "abc123"
        if args[:2] == ("diff", "--stat"):
            return " pxx/foo.py | 5 +++--\n 1 file changed, 3 insertions(+), 2 deletions(-)"
        if args[:2] == ("diff", "--name-only"):
            return "pxx/foo.py"
        return None

    monkeypatch.setattr(backend, "_git", fake_git)
    ctx = make_ctx(tmp_path)
    outcome = asyncio.run(backend.run("t", ctx))
    assert outcome.code is TerminalCode.COMPLETED
    assert outcome.diff_lines == 5
    changed = [e for e in ctx.bus.history if e.kind == "file_changed"]
    assert [e.data["path"] for e in changed] == ["pxx/foo.py"]
    assert ctx.budgets.diff_lines == 5


def test_out_of_scope_capture_projects_blocked(tmp_path, monkeypatch):
    """F4: post-capture shows only out-of-scope changes -> blocked, not COMPLETED."""
    path = fake_aider(tmp_path, "echo done")
    backend = AiderBackend(aider_path=path)

    async def fake_git(cwd, *args):
        if args[:2] == ("rev-parse", "HEAD"):
            return "abc123"
        if args[:2] == ("diff", "--stat"):
            return " converter.py | 2 +-\n 1 file changed, 1 insertion(+), 1 deletion(-)"
        if args[:2] == ("diff", "--name-only"):
            return "converter.py"
        return None

    monkeypatch.setattr(backend, "_git", fake_git)
    ctx = make_ctx(tmp_path, scope=ScopeGate(tmp_path, ("other_dir",)))
    outcome = asyncio.run(backend.run("add a comment", ctx))
    assert outcome.code is TerminalCode.OUT_OF_SCOPE
    assert "converter.py" in outcome.summary
    assert "other_dir" in outcome.summary
    gates = [e for e in ctx.bus.history if e.kind == "gate_decision"]
    assert len(gates) == 1
    assert gates[0].data["gate"] == "scope" and gates[0].data["allowed"] is False
    assert gates[0].data["paths"] == ["converter.py"]


def test_claimed_but_suppressed_edit_projects_blocked(tmp_path, monkeypatch):
    """F4 live repro: aider says "Applied edit", the pre/post capture is empty."""
    path = fake_aider(
        tmp_path,
        "echo '```diff'\n"
        "echo 'Tokens: 1.4k sent, 263 received.'\n"
        "echo 'Applied edit to converter.py'",
    )
    backend = AiderBackend(aider_path=path)

    async def fake_git(cwd, *args):
        if args[:2] == ("rev-parse", "HEAD"):
            return "abc123"
        return None  # the edit never landed — nothing committed

    monkeypatch.setattr(backend, "_git", fake_git)
    ctx = make_ctx(tmp_path, scope=ScopeGate(tmp_path, ("other_dir",)))
    outcome = asyncio.run(backend.run("Add a comment to converter.py", ctx))
    assert outcome.code is TerminalCode.OUT_OF_SCOPE
    assert "converter.py" in outcome.summary
    assert outcome.diff_lines == 0
    assert any(e.kind == "gate_decision" and e.data["allowed"] is False for e in ctx.bus.history)


def test_in_scope_edit_stays_completed(tmp_path, monkeypatch):
    """Control: the same reported edit under a covering scope is unaffected."""
    path = fake_aider(tmp_path, "echo 'Applied edit to converter.py'")
    backend = AiderBackend(aider_path=path)

    async def fake_git(cwd, *args):
        if args[:2] == ("rev-parse", "HEAD"):
            return "abc123"
        if args[:2] == ("diff", "--stat"):
            return " converter.py | 2 +-\n 1 file changed, 1 insertion(+), 1 deletion(-)"
        if args[:2] == ("diff", "--name-only"):
            return "converter.py"
        return None

    monkeypatch.setattr(backend, "_git", fake_git)
    ctx = make_ctx(tmp_path, scope=ScopeGate(tmp_path, (".",)))
    outcome = asyncio.run(backend.run("t", ctx))
    assert outcome.code is TerminalCode.COMPLETED
    assert not [e for e in ctx.bus.history if e.kind == "gate_decision"]


def test_deep_paths_come_from_name_only_not_stat(tmp_path, monkeypatch):
    """K2-R1: --stat truncates deep paths; the scope check must see the full
    --name-only path, else a legit deep in-scope edit false-fires the block."""
    path = fake_aider(tmp_path, "echo done")
    backend = AiderBackend(aider_path=path)

    async def fake_git(cwd, *args):
        if args[:2] == ("rev-parse", "HEAD"):
            return "abc123"
        if args[:2] == ("diff", "--stat"):
            return " .../deep/module.py | 3 ++-\n 1 file changed, 2 insertions(+), 1 deletion(-)"
        if args[:2] == ("diff", "--name-only"):
            return "src/deep/module.py"
        return None

    monkeypatch.setattr(backend, "_git", fake_git)
    ctx = make_ctx(tmp_path, scope=ScopeGate(tmp_path, ("src",)))
    outcome = asyncio.run(backend.run("t", ctx))
    assert outcome.code is TerminalCode.COMPLETED  # the truncated form is out of scope
    assert outcome.diff_lines == 3
    changed = [e for e in ctx.bus.history if e.kind == "file_changed"]
    assert [e.data["path"] for e in changed] == ["src/deep/module.py"]


def _blocking_fake_git(
    calls,
    statuses,
    *,
    diff=(" converter.py | 2 +-\n 1 file changed, 1 insertion(+), 1 deletion(-)", "converter.py"),
):
    """fake_git for K2b: stateful `status` answers, records `reset` calls."""
    stat, names = diff

    async def fake_git(cwd, *args):
        if args[:2] == ("rev-parse", "HEAD"):
            return "abc123def456"
        if args[:2] == ("status", "--porcelain=v1"):
            calls["status"] += 1
            return statuses[min(calls["status"] - 1, len(statuses) - 1)]
        if args and args[0] == "reset":
            calls["reset"].append(args)
            return ""
        if args[:2] == ("diff", "--stat"):
            return stat
        if args[:2] == ("diff", "--name-only"):
            return names
        return None

    return fake_git


def test_block_reverts_out_of_scope_commits(tmp_path, monkeypatch):
    """K2b: the blocked run's commit must VANISH — reset --hard to pre_head."""
    path = fake_aider(tmp_path, "echo done")
    backend = AiderBackend(aider_path=path)
    calls = {"status": 0, "reset": []}
    monkeypatch.setattr(backend, "_git", _blocking_fake_git(calls, ["", ""]))
    ctx = make_ctx(tmp_path, scope=ScopeGate(tmp_path, ("other_dir",)))
    outcome = asyncio.run(backend.run("add a comment", ctx))
    assert outcome.code is TerminalCode.OUT_OF_SCOPE
    assert calls["reset"] == [("reset", "--hard", "abc123def456")]
    assert "out-of-scope commits reverted to abc123d" in outcome.summary
    gates = [e for e in ctx.bus.history if e.kind == "gate_decision"]
    assert gates[0].data["reverted_to"] == "abc123d"


def test_block_removes_only_session_created_droppings(tmp_path, monkeypatch):
    """K2b: untracked droppings from the blocked run are cleaned ONLY when
    session-created; pre-existing untracked files are untouchable."""
    (tmp_path / "pre_existing.txt").write_text("was here before\n")
    path = fake_aider(tmp_path, "echo 'Applied edit to converter.py'\ntouch converter_notes.txt")
    backend = AiderBackend(aider_path=path)
    calls = {"status": 0, "reset": []}
    monkeypatch.setattr(
        backend,
        "_git",
        _blocking_fake_git(
            calls,
            ["?? pre_existing.txt", "?? pre_existing.txt\n?? converter_notes.txt"],
            diff=(None, None),
        ),
    )
    ctx = make_ctx(tmp_path, scope=ScopeGate(tmp_path, ("other_dir",)))
    outcome = asyncio.run(backend.run("add a comment", ctx))
    assert outcome.code is TerminalCode.OUT_OF_SCOPE
    assert not (tmp_path / "converter_notes.txt").exists()  # session dropping: gone
    assert (tmp_path / "pre_existing.txt").read_text() == "was here before\n"  # untouched
    assert "session droppings removed: converter_notes.txt" in outcome.summary
    gates = [e for e in ctx.bus.history if e.kind == "gate_decision"]
    assert gates[0].data["dropped_untracked"] == ["converter_notes.txt"]


def test_block_skips_revert_with_preexisting_dirt(tmp_path, monkeypatch):
    """K2b: never reset --hard over pre-existing user dirt (net off corner) —
    the revert is skipped and the summary says how to do it manually."""
    path = fake_aider(tmp_path, "echo done")
    backend = AiderBackend(aider_path=path)
    calls = {"status": 0, "reset": []}
    monkeypatch.setattr(
        backend, "_git", _blocking_fake_git(calls, [" M dirty.txt", " M dirty.txt"])
    )
    ctx = make_ctx(tmp_path, scope=ScopeGate(tmp_path, ("other_dir",)))
    outcome = asyncio.run(backend.run("add a comment", ctx))
    assert outcome.code is TerminalCode.OUT_OF_SCOPE
    assert calls["reset"] == []  # pre-existing dirt is untouchable
    assert "commits not reverted (pre-existing local changes)" in outcome.summary
    assert "git reset --hard abc123d" in outcome.summary
    gates = [e for e in ctx.bus.history if e.kind == "gate_decision"]
    assert gates[0].data["reverted_to"] == ""
    assert "not reverted" in gates[0].data["not_reverted"]
