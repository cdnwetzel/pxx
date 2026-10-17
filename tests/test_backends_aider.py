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


def stub_delta(backend, monkeypatch, paths, lines):
    """Stub the worktree seams: the run changed ``paths`` (``lines`` total)."""

    async def snapshot(cwd):
        return {}

    async def delta(cwd, start):
        return list(paths), lines

    monkeypatch.setattr(backend, "_snapshot", snapshot)
    monkeypatch.setattr(backend, "_delta", delta)


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
        return None

    monkeypatch.setattr(backend, "_git", fake_git)
    stub_delta(backend, monkeypatch, ["pxx/foo.py"], 5)
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
        return None

    monkeypatch.setattr(backend, "_git", fake_git)
    stub_delta(backend, monkeypatch, ["converter.py"], 2)
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
        return None  # the edit never landed

    monkeypatch.setattr(backend, "_git", fake_git)
    stub_delta(backend, monkeypatch, [], 0)  # measured: nothing changed
    ctx = make_ctx(tmp_path, scope=ScopeGate(tmp_path, ("other_dir",)))
    outcome = asyncio.run(backend.run("Add a comment to converter.py", ctx))
    assert outcome.code is TerminalCode.OUT_OF_SCOPE
    assert "converter.py" in outcome.summary
    assert outcome.diff_lines == 0
    assert any(e.kind == "gate_decision" and e.data["allowed"] is False for e in ctx.bus.history)


def test_provider_down_exit_zero_maps_to_model_unavailable(tmp_path):
    """Truthfulness: aider often exits 0 with a provider-down message and no
    edit — that must read as MODEL_UNAVAILABLE, never COMPLETED (portable-box
    degrade receipt, 2026-08-02)."""
    path = fake_aider(tmp_path, "echo 'The API providers servers are down or overloaded.'\nexit 0")
    ctx = make_ctx(tmp_path)  # no git repo -> no diff, no reported edit
    outcome = asyncio.run(AiderBackend(aider_path=path).run("do it", ctx))
    assert outcome.code is TerminalCode.MODEL_UNAVAILABLE
    assert "unavailable" in outcome.summary.lower()


def test_litellm_api_error_no_edit_maps_to_model_unavailable(tmp_path):
    path = fake_aider(tmp_path, "echo 'litellm.APIConnectionError: Connection refused'\nexit 0")
    ctx = make_ctx(tmp_path)
    outcome = asyncio.run(AiderBackend(aider_path=path).run("do it", ctx))
    assert outcome.code is TerminalCode.MODEL_UNAVAILABLE


def test_error_phrase_with_real_edit_stays_completed(tmp_path, monkeypatch):
    """Guard: an error word in the transcript does NOT downgrade a run that
    actually landed an in-scope edit — only no-edit runs are reclassified."""
    path = fake_aider(
        tmp_path,
        "echo 'Applied edit to converter.py'\necho 'note: earlier connection error, retried ok'",
    )
    backend = AiderBackend(aider_path=path)

    async def fake_git(cwd, *args):
        if args[:2] == ("rev-parse", "HEAD"):
            return "abc123"
        return None

    monkeypatch.setattr(backend, "_git", fake_git)
    stub_delta(backend, monkeypatch, ["converter.py"], 2)
    ctx = make_ctx(tmp_path, scope=ScopeGate(tmp_path, (".",)))
    outcome = asyncio.run(backend.run("t", ctx))
    assert outcome.code is TerminalCode.COMPLETED


def test_clean_completion_without_error_stays_completed(tmp_path):
    """Guard: a no-edit run with no error signature is still COMPLETED."""
    path = fake_aider(tmp_path, "echo 'nothing to change; all good'")
    outcome = asyncio.run(AiderBackend(aider_path=path).run("t", make_ctx(tmp_path)))
    assert outcome.code is TerminalCode.COMPLETED


def test_answer_prose_mentioning_errors_stays_completed(tmp_path):
    """neo advisory 2026-08-02: an ask-mode answer is a zero-edit exit-0 run;
    prose that merely discusses rate limits / connection errors must NOT
    reclassify to MODEL_UNAVAILABLE (the signature needs a distinctive error
    token, not the topic words)."""
    path = fake_aider(
        tmp_path,
        "echo 'To add a rate limiter, catch connection errors and retry with backoff.'",
    )
    outcome = asyncio.run(
        AiderBackend(aider_path=path).run("how do I add a rate limiter?", make_ctx(tmp_path))
    )
    assert outcome.code is TerminalCode.COMPLETED


def test_in_scope_edit_stays_completed(tmp_path, monkeypatch):
    """Control: the same reported edit under a covering scope is unaffected."""
    path = fake_aider(tmp_path, "echo 'Applied edit to converter.py'")
    backend = AiderBackend(aider_path=path)

    async def fake_git(cwd, *args):
        if args[:2] == ("rev-parse", "HEAD"):
            return "abc123"
        return None

    monkeypatch.setattr(backend, "_git", fake_git)
    stub_delta(backend, monkeypatch, ["converter.py"], 2)
    ctx = make_ctx(tmp_path, scope=ScopeGate(tmp_path, (".",)))
    outcome = asyncio.run(backend.run("t", ctx))
    assert outcome.code is TerminalCode.COMPLETED
    assert not [e for e in ctx.bus.history if e.kind == "gate_decision"]


def test_deep_paths_reach_the_scope_check_intact(tmp_path, monkeypatch):
    """K2-R1: a truncated path (as `git diff --stat` prints deep ones) false-fires
    the block. Paths now come from the worktree delta, full and exact."""
    path = fake_aider(tmp_path, "echo done")
    backend = AiderBackend(aider_path=path)

    async def fake_git(cwd, *args):
        if args[:2] == ("rev-parse", "HEAD"):
            return "abc123"
        return None

    monkeypatch.setattr(backend, "_git", fake_git)
    stub_delta(backend, monkeypatch, ["src/deep/module.py"], 3)
    ctx = make_ctx(tmp_path, scope=ScopeGate(tmp_path, ("src",)))
    outcome = asyncio.run(backend.run("t", ctx))
    assert outcome.code is TerminalCode.COMPLETED  # the truncated form is out of scope
    assert outcome.diff_lines == 3
    changed = [e for e in ctx.bus.history if e.kind == "file_changed"]
    assert [e.data["path"] for e in changed] == ["src/deep/module.py"]


def _blocking_fake_git(calls, statuses):
    """fake_git for K2b: stateful `status` answers, records `reset` calls."""

    async def fake_git(cwd, *args):
        if args[:2] == ("rev-parse", "HEAD"):
            return "abc123def456"
        if args[:2] == ("status", "--porcelain=v1"):
            calls["status"] += 1
            return statuses[min(calls["status"] - 1, len(statuses) - 1)]
        if args and args[0] == "reset":
            calls["reset"].append(args)
            return ""
        return None

    return fake_git


def test_block_reverts_out_of_scope_commits(tmp_path, monkeypatch):
    """K2b: the blocked run's commit must VANISH — reset --hard to pre_head."""
    path = fake_aider(tmp_path, "echo done")
    backend = AiderBackend(aider_path=path)
    calls = {"status": 0, "reset": []}
    monkeypatch.setattr(backend, "_git", _blocking_fake_git(calls, ["", ""]))
    stub_delta(backend, monkeypatch, ["converter.py"], 2)
    ctx = make_ctx(tmp_path, scope=ScopeGate(tmp_path, ("other_dir",)))
    outcome = asyncio.run(backend.run("add a comment", ctx))
    assert outcome.code is TerminalCode.OUT_OF_SCOPE
    assert calls["reset"] == [("reset", "--hard", "abc123def456")]
    assert "out-of-scope edits reverted to abc123d" in outcome.summary
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
        ),
    )
    stub_delta(backend, monkeypatch, [], 0)
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
    stub_delta(backend, monkeypatch, ["converter.py"], 2)
    ctx = make_ctx(tmp_path, scope=ScopeGate(tmp_path, ("other_dir",)))
    outcome = asyncio.run(backend.run("add a comment", ctx))
    assert outcome.code is TerminalCode.OUT_OF_SCOPE
    assert calls["reset"] == []  # pre-existing dirt is untouchable
    assert "edits not reverted (pre-existing local changes)" in outcome.summary
    assert "git reset --hard abc123d" in outcome.summary
    gates = [e for e in ctx.bus.history if e.kind == "gate_decision"]
    assert gates[0].data["reverted_to"] == ""
    assert "not reverted" in gates[0].data["not_reverted"]


# --- pxx owns committing: aider never commits (2.9.1) -------------------------


def test_aider_is_told_not_to_commit_in_a_repo(tmp_path, monkeypatch):
    path = fake_aider(tmp_path, 'echo "ARGS: $@"')
    backend = AiderBackend(aider_path=path)

    async def fake_git(cwd, *args):
        return "abc123" if args[:2] == ("rev-parse", "HEAD") else None

    monkeypatch.setattr(backend, "_git", fake_git)
    stub_delta(backend, monkeypatch, [], 0)
    ctx = make_ctx(tmp_path, settings=Settings(permission=PermissionMode.EDIT))
    args = asyncio.run(backend.run("t", ctx)).summary
    assert "--no-auto-commits" in args and "--no-dirty-commits" in args
    assert "--no-git" not in args


def test_no_commit_flags_outside_a_repo(tmp_path):
    path = fake_aider(tmp_path, 'echo "ARGS: $@"')
    args = asyncio.run(AiderBackend(aider_path=path).run("t", make_ctx(tmp_path))).summary
    assert "--no-git" in args and "--no-auto-commits" not in args


def test_an_aider_that_commits_anyway_fails_loudly_and_rewrites_nothing(tmp_path, monkeypatch):
    path = fake_aider(tmp_path, "echo done")
    backend = AiderBackend(aider_path=path)
    calls = {"rev_parse": 0, "reset": []}

    async def fake_git(cwd, *args):
        if args[:2] == ("rev-parse", "HEAD"):
            calls["rev_parse"] += 1
            return "pre0000000000" if calls["rev_parse"] == 1 else "post111111111"
        if args and args[0] == "reset":
            calls["reset"].append(args)
        return ""

    monkeypatch.setattr(backend, "_git", fake_git)
    stub_delta(backend, monkeypatch, [], 0)
    ctx = make_ctx(tmp_path, settings=Settings(permission=PermissionMode.EDIT))
    with pytest.raises(BackendError, match=r"despite --no-auto-commits.*git reset --soft pre0000"):
        asyncio.run(backend.run("t", ctx))
    assert calls["reset"] == []  # pxx does not rewrite history behind the operator


_NEEDS_GIT = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")


def _git_repo(repo):
    import subprocess

    def git(*args):
        return subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout

    git("init", "-q")
    (repo / "converter.py").write_text("x = 1\n")
    git("add", "-A")
    git("commit", "-q", "-m", "start")
    return git


@_NEEDS_GIT
def test_real_git_edits_and_new_files_stay_uncommitted_and_are_reported(tmp_path):
    """End to end with real git and the real worktree helpers: a fake aider that
    edits a file and creates one (and, like a well-behaved aider given
    --no-auto-commits, commits nothing). HEAD stays put, both paths are
    reported, and the edits are left exactly as the native backend leaves them."""
    repo = tmp_path / "repo"  # the fake aider script lives outside the repo
    repo.mkdir()
    git = _git_repo(repo)
    start = git("rev-parse", "HEAD").strip()
    path = fake_aider(tmp_path, "echo 'x = 2' > converter.py\necho 'y = 1' > new.py")
    ctx = make_ctx(repo, settings=Settings(permission=PermissionMode.EDIT))
    outcome = asyncio.run(AiderBackend(aider_path=path).run("edit", ctx))
    assert outcome.code is TerminalCode.COMPLETED
    assert git("rev-parse", "HEAD").strip() == start
    changed = sorted(e.data["path"] for e in ctx.bus.history if e.kind == "file_changed")
    assert changed == ["converter.py", "new.py"]
    assert outcome.diff_lines == 3  # 1 insertion + 1 deletion + 1 new line
    assert git("status", "--porcelain") == " M converter.py\n?? new.py\n"


@_NEEDS_GIT
def test_real_git_preexisting_staged_work_is_untouched(tmp_path):
    """Nothing is reset, so the index the operator had before the run survives
    exactly -- staged and unstaged stay distinct (raw porcelain, no strip)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git = _git_repo(repo)
    (repo / "staged.py").write_text("s = 1\n")
    git("add", "staged.py")
    path = fake_aider(tmp_path, "echo 'x = 2' > converter.py")
    ctx = make_ctx(repo, settings=Settings(permission=PermissionMode.EDIT))
    outcome = asyncio.run(AiderBackend(aider_path=path).run("edit", ctx))
    assert outcome.code is TerminalCode.COMPLETED
    assert git("status", "--porcelain") == " M converter.py\nA  staged.py\n"
    changed = [e.data["path"] for e in ctx.bus.history if e.kind == "file_changed"]
    assert changed == ["converter.py"]  # pre-existing staged work is not the run's


@_NEEDS_GIT
def test_real_git_with_commit_pxx_commits_through_its_own_gate(tmp_path):
    """agy review (P0): under --commit, aider's own commits used to land
    unscanned. Now aider commits nothing and the session's commit path runs:
    clean work is committed BY PXX (net trailer), a secret is never committed."""
    from pxx.config import ModelRef
    from pxx.session import Session

    def session(repo, body):
        settings = Settings(
            model=ModelRef(provider="ollama", model="m"),
            permission=PermissionMode.EDIT,
            auto_commit=True,
            memory_dir=tmp_path / "mem",
            state_dir=tmp_path / "state",
        )
        backend = AiderBackend(aider_path=fake_aider(tmp_path, body))
        return Session(settings, backend, cwd=repo)

    clean = tmp_path / "clean"
    clean.mkdir()
    git = _git_repo(clean)
    outcome = asyncio.run(session(clean, "echo 'x = 2' > converter.py").run("set x to 2"))
    assert outcome.code is TerminalCode.COMPLETED
    assert "[committed " in outcome.summary
    assert "[net: pxx-pre/" in git("log", "-1", "--format=%B")  # pxx's commit, not aider's
    assert git("status", "--porcelain") == ""

    dirty = tmp_path / "dirty"
    dirty.mkdir()
    git = _git_repo(dirty)
    head = git("rev-parse", "HEAD")
    leak = "echo \"api_key = 'sk-live-0123456789abcdef0123456789abcdef'\" > settings.py"
    outcome = asyncio.run(session(dirty, leak).run("add settings"))
    assert git("rev-parse", "HEAD") == head  # the secrets gate refused the commit
    assert "[committed " not in outcome.summary


def _moved_head_git(calls):
    async def fake_git(cwd, *args):
        if args[:2] == ("rev-parse", "HEAD"):
            calls["rev_parse"] += 1
            return "pre0000000000" if calls["rev_parse"] == 1 else "post111111111"
        if args and args[0] == "reset":
            calls["reset"].append(args)
        return ""

    return fake_git


def test_head_moved_is_reported_when_aider_fails(tmp_path, monkeypatch):
    # Codex review (2.9.1 r2): the HEAD check ran only on success.
    backend = AiderBackend(aider_path=fake_aider(tmp_path, "echo boom >&2; exit 3"))
    calls = {"rev_parse": 0, "reset": []}
    monkeypatch.setattr(backend, "_git", _moved_head_git(calls))
    stub_delta(backend, monkeypatch, [], 0)
    ctx = make_ctx(tmp_path, settings=Settings(permission=PermissionMode.EDIT))
    with pytest.raises(BackendError, match=r"(?s)exited with code 3.*despite --no-auto-commits"):
        asyncio.run(backend.run("t", ctx))
    assert calls["reset"] == []


def test_head_moved_is_reported_when_cancelled(tmp_path, monkeypatch):
    # `exec`: no shell child left holding the pipes after cancel; cancel only
    # once aider reported ready; bounded, so a hang fails instead of blocking
    # (Codex review, 2.9.1 r3).
    backend = AiderBackend(aider_path=fake_aider(tmp_path, "echo ready\nexec sleep 30"))
    calls = {"rev_parse": 0, "reset": []}
    monkeypatch.setattr(backend, "_git", _moved_head_git(calls))
    stub_delta(backend, monkeypatch, [], 0)
    ctx = make_ctx(tmp_path, settings=Settings(permission=PermissionMode.EDIT))

    async def main():
        task = asyncio.ensure_future(backend.run("t", ctx))
        for _ in range(500):
            if any(e.kind == "model_response" for e in ctx.bus.history):
                break
            await asyncio.sleep(0.01)
        await backend.cancel()
        return await asyncio.wait_for(task, timeout=10)

    outcome = asyncio.run(main())
    assert outcome.code is TerminalCode.INTERRUPTED
    assert "despite --no-auto-commits" in outcome.summary
    assert calls["reset"] == []


def test_a_failed_k2b_revert_is_reported_not_claimed(tmp_path, monkeypatch):
    # Codex review (2.9.1 r2): a failed `reset --hard` was reported as a revert.
    backend = AiderBackend(aider_path=fake_aider(tmp_path, "echo done"))
    calls = {"status": 0, "reset": []}
    blocking = _blocking_fake_git(calls, ["", ""])

    async def failing_reset(cwd, *args):
        if args and args[0] == "reset":
            calls["reset"].append(args)
            return None  # git failed
        return await blocking(cwd, *args)

    monkeypatch.setattr(backend, "_git", failing_reset)
    stub_delta(backend, monkeypatch, ["converter.py"], 2)
    ctx = make_ctx(tmp_path, scope=ScopeGate(tmp_path, ("other_dir",)))
    outcome = asyncio.run(backend.run("add a comment", ctx))
    assert outcome.code is TerminalCode.OUT_OF_SCOPE
    assert "revert FAILED" in outcome.summary and "reverted to" not in outcome.summary
    gates = [e for e in ctx.bus.history if e.kind == "gate_decision"]
    assert gates[0].data["reverted_to"] == "" and "FAILED" in gates[0].data["not_reverted"]


def _session_for(tmp_path, repo, body):
    from pxx.config import ModelRef
    from pxx.session import Session

    settings = Settings(
        model=ModelRef(provider="ollama", model="m"),
        permission=PermissionMode.EDIT,
        memory_dir=tmp_path / "mem",
        state_dir=tmp_path / "state",
    )
    return Session(settings, AiderBackend(aider_path=fake_aider(tmp_path, body)), cwd=repo)


@_NEEDS_GIT
def test_session_never_resets_over_a_rogue_aider_commit(tmp_path):
    """agy review (2.9.1 r2, P0): on EDIT_FAILED the session's safety net ran
    `reset --hard <net tag>`, orphaning the very commit the error tells the
    operator to inspect. The net now refuses when HEAD moved."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git = _git_repo(repo)
    rogue = "echo 'x = 99' > converter.py\ngit -c user.name=a -c user.email=a@e.c commit -qam rogue"
    outcome = asyncio.run(_session_for(tmp_path, repo, rogue).run("set x"))
    assert outcome.code is TerminalCode.EDIT_FAILED
    assert git("log", "-1", "--format=%s").strip() == "rogue"  # the commit survives
    assert (repo / "converter.py").read_text() == "x = 99\n"  # and its edit
    assert "net not restored" in outcome.summary


@_NEEDS_GIT
def test_session_never_resets_a_branch_aider_switched_to(tmp_path):
    """agy review (2.9.1 r2, P0): a model that ran `git checkout other` got
    `other` reset to main's tip by the net. Now nothing moves."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git = _git_repo(repo)
    start_branch = git("symbolic-ref", "--short", "HEAD").strip()
    main = git("rev-parse", "HEAD").strip()
    git("checkout", "-q", "-b", "other")
    (repo / "o.txt").write_text("only on other\n")
    git("add", "-A")
    git("commit", "-q", "-m", "other work")
    other_tip = git("rev-parse", "HEAD").strip()
    git("checkout", "-q", "-")
    outcome = asyncio.run(_session_for(tmp_path, repo, "git checkout -q other").run("look"))
    assert outcome.code is TerminalCode.EDIT_FAILED
    assert "branch changed" in outcome.summary
    assert git("rev-parse", "other").strip() == other_tip  # other is untouched
    assert git("rev-parse", start_branch).strip() == main  # and so is the start branch


@_NEEDS_GIT
def test_session_branch_switch_at_the_same_commit_is_caught(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git = _git_repo(repo)
    git("branch", "twin")  # same commit, different branch
    outcome = asyncio.run(_session_for(tmp_path, repo, "git checkout -q twin").run("look"))
    assert outcome.code is TerminalCode.EDIT_FAILED
    assert "branch changed" in outcome.summary


def test_one_out_of_scope_edit_among_in_scope_ones_blocks_the_run(tmp_path, monkeypatch):
    # agy review (2.9.1 r2): the fence required EVERY edit to be out of
    # scope, so an extra in-scope edit carried an out-of-scope one through.
    backend = AiderBackend(aider_path=fake_aider(tmp_path, "echo done"))
    calls = {"status": 0, "reset": []}
    monkeypatch.setattr(backend, "_git", _blocking_fake_git(calls, ["", ""]))
    stub_delta(backend, monkeypatch, ["src/ok.py", ".secret"], 3)
    ctx = make_ctx(tmp_path, scope=ScopeGate(tmp_path, ("src",)))
    outcome = asyncio.run(backend.run("edit", ctx))
    assert outcome.code is TerminalCode.OUT_OF_SCOPE
    assert ".secret" in outcome.summary and "src/ok.py" not in outcome.summary
    gates = [e for e in ctx.bus.history if e.kind == "gate_decision"]
    assert gates[0].data["paths"] == [".secret"]


@_NEEDS_GIT
def test_session_commit_never_sweeps_in_pre_staged_work(tmp_path):
    """Grok review (2.9.1 r2): a bare `git commit` committed whatever was
    already staged alongside the session's delta."""
    from pxx.config import ModelRef
    from pxx.session import Session

    repo = tmp_path / "repo"
    repo.mkdir()
    git = _git_repo(repo)
    (repo / "staged_by_operator.py").write_text("mine = 1\n")
    git("add", "staged_by_operator.py")
    settings = Settings(
        model=ModelRef(provider="ollama", model="m"),
        permission=PermissionMode.EDIT,
        auto_commit=True,
        safety_net=False,  # keep the operator's index in place for this case
        memory_dir=tmp_path / "mem",
        state_dir=tmp_path / "state",
    )
    backend = AiderBackend(aider_path=fake_aider(tmp_path, "echo 'x = 2' > converter.py"))
    outcome = asyncio.run(Session(settings, backend, cwd=repo).run("set x to 2"))
    assert "[committed " in outcome.summary
    committed = git("show", "--name-only", "--format=", "HEAD").split()
    assert committed == ["converter.py"]  # only the session's own delta
    assert git("status", "--porcelain") == "A  staged_by_operator.py\n"  # still staged


@_NEEDS_GIT
def test_session_commit_includes_a_staged_rename_whole(tmp_path):
    """Codex review (2.9.1 r3): the path-limited commit dropped the source of
    a staged rename (`git add` cannot re-add it), committing half a rename."""
    from pxx.config import ModelRef
    from pxx.session import Session

    repo = tmp_path / "repo"
    repo.mkdir()
    git = _git_repo(repo)
    (repo / "unrelated.py").write_text("u = 1\n")
    git("add", "unrelated.py")  # the operator's, staged before the run
    settings = Settings(
        model=ModelRef(provider="ollama", model="m"),
        permission=PermissionMode.EDIT,
        auto_commit=True,
        safety_net=False,
        memory_dir=tmp_path / "mem",
        state_dir=tmp_path / "state",
    )
    backend = AiderBackend(aider_path=fake_aider(tmp_path, "git mv converter.py renamed.py"))
    outcome = asyncio.run(Session(settings, backend, cwd=repo).run("rename it"))
    assert "[committed " in outcome.summary
    status = git("show", "--name-status", "--format=", "-M", "HEAD").split()
    assert status[:3] == ["R100", "converter.py", "renamed.py"]  # the whole rename
    assert git("status", "--porcelain") == "A  unrelated.py\n"  # still only staged


@_NEEDS_GIT
def test_real_git_an_out_of_scope_symlink_trips_the_fence(tmp_path):
    """Codex review (2.9.1 r3): a newly created dangling symlink vanished from
    the delta, so with no "Applied edit" line it slipped past the fence."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_repo(repo)
    (repo / "src").mkdir()
    path = fake_aider(tmp_path, "ln -s /nowhere/at/all outside_link")
    ctx = make_ctx(
        repo,
        settings=Settings(permission=PermissionMode.EDIT),
        scope=ScopeGate(repo, ("src",)),
    )
    outcome = asyncio.run(AiderBackend(aider_path=path).run("t", ctx))
    assert outcome.code is TerminalCode.OUT_OF_SCOPE
    assert "outside_link" in outcome.summary


def test_no_baseline_means_aider_is_not_run(tmp_path, monkeypatch):
    # Codex review (2.9.1 r4): an unavailable baseline became "zero changes".
    marker = tmp_path / "ran"
    backend = AiderBackend(aider_path=fake_aider(tmp_path, f"touch {marker}"))

    async def fake_git(cwd, *args):
        return "abc123" if args[:2] == ("rev-parse", "HEAD") else ""

    async def no_snapshot(cwd):
        return None

    monkeypatch.setattr(backend, "_git", fake_git)
    monkeypatch.setattr(backend, "_snapshot", no_snapshot)
    ctx = make_ctx(tmp_path, settings=Settings(permission=PermissionMode.EDIT))
    with pytest.raises(BackendError, match="could not snapshot"):
        asyncio.run(backend.run("t", ctx))
    assert not marker.exists()  # aider never started


def test_an_unmeasurable_run_is_not_a_clean_zero_change_success(tmp_path, monkeypatch):
    backend = AiderBackend(aider_path=fake_aider(tmp_path, "echo done"))

    async def fake_git(cwd, *args):
        return "abc123" if args[:2] == ("rev-parse", "HEAD") else ""

    async def snapshot(cwd):
        return {}

    async def no_delta(cwd, start):
        return None  # the final snapshot failed

    monkeypatch.setattr(backend, "_git", fake_git)
    monkeypatch.setattr(backend, "_snapshot", snapshot)
    monkeypatch.setattr(backend, "_delta", no_delta)
    ctx = make_ctx(tmp_path, settings=Settings(permission=PermissionMode.EDIT))
    with pytest.raises(BackendError, match="could not measure what aider changed"):
        asyncio.run(backend.run("t", ctx))


@_NEEDS_GIT
def test_session_commit_treats_a_wildcard_filename_literally(tmp_path):
    """Codex review (2.9.1 r4): pathspecs are patterns even after `--`, so a
    legal filename `*.py` staged and committed every .py file."""
    from pxx.config import ModelRef
    from pxx.session import Session

    repo = tmp_path / "repo"
    repo.mkdir()
    git = _git_repo(repo)
    (repo / "staged_by_operator.py").write_text("mine = 1\n")
    git("add", "staged_by_operator.py")
    (repo / "unrelated.py").write_text("u = 1\n")  # untracked, matches *.py
    settings = Settings(
        model=ModelRef(provider="ollama", model="m"),
        permission=PermissionMode.EDIT,
        auto_commit=True,
        safety_net=False,
        memory_dir=tmp_path / "mem",
        state_dir=tmp_path / "state",
    )
    backend = AiderBackend(aider_path=fake_aider(tmp_path, "echo 'w = 1' > '*.py'"))
    outcome = asyncio.run(Session(settings, backend, cwd=repo).run("add the file"))
    assert "[committed " in outcome.summary
    assert git("show", "--name-only", "--format=", "HEAD").split() == ["*.py"]
    status = git("status", "--porcelain")
    assert "A  staged_by_operator.py" in status and "?? unrelated.py" in status


def test_an_unverifiable_head_after_the_run_is_not_success(tmp_path, monkeypatch):
    # Grok review (2.9.1 r4): a failed `rev-parse` read as "HEAD did not move".
    backend = AiderBackend(aider_path=fake_aider(tmp_path, "echo done"))
    calls = {"rev_parse": 0}

    async def fake_git(cwd, *args):
        if args[:2] == ("rev-parse", "HEAD"):
            calls["rev_parse"] += 1
            return "pre0000000000" if calls["rev_parse"] == 1 else None
        return ""

    monkeypatch.setattr(backend, "_git", fake_git)
    stub_delta(backend, monkeypatch, [], 0)
    ctx = make_ctx(tmp_path, settings=Settings(permission=PermissionMode.EDIT))
    with pytest.raises(BackendError, match="could not verify HEAD"):
        asyncio.run(backend.run("t", ctx))


def test_task_cancellation_kills_aider_and_still_reports_a_moved_head(tmp_path, monkeypatch):
    # Codex review (2.9.1 r5): cancelling the TASK (the session's wait_for)
    # skipped the HEAD check and left the aider process running.
    import os as _os

    pidfile = tmp_path / "aider.pid"
    backend = AiderBackend(
        aider_path=fake_aider(tmp_path, f"echo $$ > {pidfile}\necho ready\nexec sleep 30")
    )
    calls = {"rev_parse": 0, "reset": []}
    monkeypatch.setattr(backend, "_git", _moved_head_git(calls))
    stub_delta(backend, monkeypatch, [], 0)
    ctx = make_ctx(tmp_path, settings=Settings(permission=PermissionMode.EDIT))

    async def main():
        task = asyncio.ensure_future(backend.run("t", ctx))
        for _ in range(500):
            if any(e.kind == "model_response" for e in ctx.bus.history):
                break
            await asyncio.sleep(0.01)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=15)

    import contextlib

    asyncio.run(main())
    pid = int(pidfile.read_text())
    with pytest.raises(ProcessLookupError):
        _os.kill(pid, 0)  # aider is gone, not orphaned
    obs = [e.data for e in ctx.bus.history if e.kind == "observation"]
    assert any(
        o.get("source") == "aider_head_moved" and "despite --no-auto-commits" in o.get("detail", "")
        for o in obs
    )
    assert calls["reset"] == []
