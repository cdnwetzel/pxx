"""Session integration tests: full wiring with MockBackend, no network."""

from __future__ import annotations

import asyncio
import time

from pxx.backends.mock import MockBackend
from pxx.config import ModelRef, Settings
from pxx.events import AuditLog
from pxx.memory.store import MemoryStore
from pxx.outcome import TerminalCode
from pxx.safety import PermissionMode
from pxx.session import Session


def run(coro):
    return asyncio.run(coro)


def _settings(tmp_path, permission=PermissionMode.AUTO, **overrides) -> Settings:
    base = Settings(
        model=ModelRef(provider="ollama", model="test-model"),
        permission=permission,
        memory_dir=tmp_path / "mem",
        state_dir=tmp_path / "state",
    )
    if overrides:
        from dataclasses import replace

        base = replace(base, **overrides)
    return base


def test_session_completes_and_audits(tmp_path):
    backend = MockBackend([{"say": "hello"}, {"done": "greeted"}])
    session = Session(_settings(tmp_path), backend, cwd=tmp_path)
    outcome = run(session.run("say hi"))
    assert outcome.code is TerminalCode.COMPLETED
    assert outcome.summary == "greeted"

    day = time.strftime("%Y-%m-%d")
    audit_path = tmp_path / "state" / "audit" / f"{day}.jsonl"
    assert audit_path.is_file()
    assert AuditLog.verify(audit_path)
    kinds = [e.kind for e in session.bus.history]
    assert kinds[0] == "run_created"  # B10.3 vocabulary: RunCreated first
    assert kinds[1] == "session_start"
    assert kinds[-1] == "session_end"


def test_session_tool_run_writes_file_and_captures_memory(tmp_path):
    backend = MockBackend(
        [
            {"tool": "write_file", "args": {"path": "out.txt", "content": "hi"}},
            {"done": "wrote file"},
        ]
    )
    session = Session(_settings(tmp_path), backend, cwd=tmp_path)
    outcome = run(session.run("create out.txt"))
    assert outcome.code is TerminalCode.COMPLETED
    assert (tmp_path / "out.txt").read_text() == "hi"

    # Phase 20.5: a COMPLETED session is NOT auto-converted into knowledge —
    # the store stays empty; only explicit remember calls enter it.
    store = MemoryStore(tmp_path / "mem" / "memory.db")
    try:
        assert store.list(tmp_path.name) == []
    finally:
        store.close()


def test_session_failed_run_captures_episodic_observations(tmp_path):
    from pxx.errors import BackendError

    class WriteThenFailBackend:
        name = "write-fail"
        capabilities = None

        async def run(self, task, ctx):
            from pxx.backends.mock import make_tool_context

            await ctx.tools.call(
                "write_file",
                {"path": "partial.txt", "content": "x"},
                make_tool_context(ctx),
            )
            raise BackendError("backend died mid-run")

        async def cancel(self):
            pass

    session = Session(_settings(tmp_path), WriteThenFailBackend(), cwd=tmp_path)
    outcome = run(session.run("try the thing"))
    assert outcome.code is TerminalCode.MODEL_UNAVAILABLE

    store = MemoryStore(tmp_path / "mem" / "memory.db")
    try:
        obs = store.list(tmp_path.name)
        assert obs, "a failed session should capture episodic evidence"
        assert all(o.layer == "episodic" for o in obs)
        assert all(o.contamination_risk == 0.5 for o in obs)
        assert all(o.provenance == "failed_run_inference" for o in obs)
    finally:
        store.close()


def test_session_maps_scope_violation(tmp_path):
    backend = MockBackend(
        [{"tool": "write_file", "args": {"path": "/etc/pxx-evil", "content": "x"}}]
    )
    session = Session(_settings(tmp_path), backend, cwd=tmp_path)
    outcome = run(session.run("escape"))
    assert outcome.code is TerminalCode.OUT_OF_SCOPE


def test_session_denies_write_in_ask_mode(tmp_path):
    backend = MockBackend([{"tool": "write_file", "args": {"path": "x.txt", "content": "x"}}])
    session = Session(_settings(tmp_path, PermissionMode.ASK), backend, cwd=tmp_path)
    outcome = run(session.run("write"))
    assert outcome.code is TerminalCode.OUT_OF_SCOPE
    assert not (tmp_path / "x.txt").exists()


def test_session_maps_backend_error(tmp_path):
    backend = MockBackend([{"bogus": "step"}])
    session = Session(_settings(tmp_path), backend, cwd=tmp_path)
    outcome = run(session.run("boom"))
    assert outcome.code is TerminalCode.MODEL_UNAVAILABLE


# --- K8: native runs must report mutations that actually landed -------------


def _git_repo(path):
    """A git repo with one committed file (mirrors test_identity_threading)."""
    import subprocess

    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True, capture_output=True)
    (path / "a.txt").write_text("hello\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@e.c", "commit", "-q", "-m", "i"],
        cwd=path,
        check=True,
        capture_output=True,
    )


def test_completed_native_run_reports_worktree_diff_lines(tmp_path):
    """K8: a COMPLETED native run with uncommitted edits reports real diff
    accounting (native commits nothing — measure the worktree, not commits)."""
    repo = tmp_path / "repo"
    _git_repo(repo)
    backend = MockBackend(
        [
            {
                "tool": "edit_file",
                "args": {"path": "a.txt", "old_string": "hello", "new_string": "goodbye\nworld"},
            },
            {"tool": "write_file", "args": {"path": "new.txt", "content": "line one\nline two\n"}},
            {"done": "made changes"},
        ]
    )
    session = Session(_settings(tmp_path), backend, cwd=repo)
    outcome = run(session.run("change things"))
    assert outcome.code is TerminalCode.COMPLETED
    # a.txt: +2/-1 against HEAD; new.txt: 2 untracked lines
    assert outcome.diff_lines == 5


def test_hook_denied_after_writes_reports_landed_mutation(tmp_path):
    """K8 spec repro: writes land, then the shell call is denied — the abort
    must say writes landed and account for them (not all zeros)."""
    from pxx.safety import Hook

    repo = tmp_path / "repo"
    _git_repo(repo)
    settings = _settings(
        tmp_path,
        hooks=(Hook(event="PreToolUse", command="sh -c 'exit 2'", matcher="run_shell"),),
    )
    backend = MockBackend(
        [
            {"tool": "write_file", "args": {"path": "new.txt", "content": "line one\nline two\n"}},
            {"tool": "run_shell", "args": {"command": "true"}},
            {"done": "unreachable"},
        ]
    )
    session = Session(settings, backend, cwd=repo)
    outcome = run(session.run("write then shell"))
    assert outcome.code is TerminalCode.HOOK_DENIED
    assert (repo / "new.txt").exists()  # the writes really landed
    assert outcome.diff_lines == 2
    assert "1 file already modified: new.txt" in outcome.summary


def test_worktree_report_catches_writes_outside_tools(tmp_path):
    """K8: writes that bypass the tool surface (e.g. shell redirects) still
    end up on the event stream and in diff accounting."""

    class SideWriteBackend:
        name = "side-write"
        capabilities = None

        async def run(self, task, ctx):
            from pxx.outcome import RunOutcome
            from pxx.outcome import TerminalCode as TC

            (ctx.cwd / "sneaky.txt").write_text("written off the books\n")
            return RunOutcome(code=TC.COMPLETED, summary="done", session_id=ctx.session_id)

        async def cancel(self):
            pass

    repo = tmp_path / "repo"
    _git_repo(repo)
    session = Session(_settings(tmp_path), SideWriteBackend(), cwd=repo)
    outcome = run(session.run("write sideways"))
    assert outcome.code is TerminalCode.COMPLETED
    assert outcome.diff_lines == 1
    changed = [e for e in session.bus.history if e.kind == "file_changed"]
    assert any(
        e.data.get("path") == "sneaky.txt" and e.data.get("source") == "worktree-diff"
        for e in changed
    )


def test_session_runs_without_memory(tmp_path):
    backend = MockBackend([{"done": "ok"}])
    session = Session(_settings(tmp_path, memory_enabled=False), backend, cwd=tmp_path)
    outcome = run(session.run("hi"))
    assert outcome.code is TerminalCode.COMPLETED
    assert not (tmp_path / "mem").exists()


def test_session_memory_injected_into_context(tmp_path):
    """Seeded memory must reach the backend via ctx.memory_context."""
    seen = {}

    class ProbeBackend(MockBackend):
        async def run(self, task, ctx):
            seen["memory_context"] = ctx.memory_context
            return await super().run(task, ctx)

    async def scenario():
        store = MemoryStore(tmp_path / "mem" / "memory.db")
        await store.add(tmp_path.name, "note", "project uses ruff for linting", tags=("pinned",))
        store.close()
        backend = ProbeBackend([{"done": "ok"}])
        session = Session(_settings(tmp_path), backend, cwd=tmp_path)
        await session.run("how do I lint?")

    run(scenario())
    assert "ruff" in seen["memory_context"]


# --- M0 regression: L1 (MCP clients closed) + L2 (SIGINT handler removed) -------


def test_mcp_clients_closed_and_sigint_removed_after_run(tmp_path, monkeypatch):
    """A run must not leak MCP subprocesses/reader tasks or the SIGINT handler."""
    from dataclasses import replace

    from pxx.config import McpServerSpec

    closed: list[str] = []

    class FakeClient:
        async def close(self) -> None:
            closed.append("closed")

    async def fake_connect(name, command):
        await asyncio.sleep(0)
        return FakeClient()

    async def fake_register(client, tools) -> None:
        await asyncio.sleep(0)

    monkeypatch.setattr("pxx.mcp.client.StdioMcpClient.connect", fake_connect)
    monkeypatch.setattr("pxx.mcp.client.register_mcp_tools", fake_register)

    settings = replace(
        _settings(tmp_path),
        mcp_servers=(McpServerSpec(name="fake", command=("true",)),),
    )
    session = Session(settings, MockBackend([{"done": "ok"}]), cwd=tmp_path)
    outcome = run(session.run("hi"))
    assert outcome.code is TerminalCode.COMPLETED
    assert closed == ["closed"]  # L1: client closed even on the success path
    assert session._sigint_loop is None  # L2: signal handler removed


# --- B2.1 session-level code splits + B2.4 identity threading --------------------


def test_backend_edit_failed_code_hint(tmp_path):
    """A backend that names its cause (BackendError code=EDIT_FAILED) gets
    THAT terminal code, not a generic model error."""
    from pxx.errors import BackendError

    class FailingBackend:
        name = "failing"
        capabilities = None

        async def run(self, task, ctx):
            from pxx.outcome import TerminalCode as TC

            raise BackendError("aider exited 1", code=TC.EDIT_FAILED)

        async def cancel(self):
            pass

    session = Session(_settings(tmp_path), FailingBackend(), cwd=tmp_path)
    outcome = run(session.run("edit something"))
    assert outcome.code is TerminalCode.EDIT_FAILED


def test_plain_backend_error_is_model_unavailable(tmp_path):
    from pxx.errors import BackendError

    class FailingBackend:
        name = "failing"
        capabilities = None

        async def run(self, task, ctx):
            raise BackendError("all endpoints unreachable")

        async def cancel(self):
            pass

    session = Session(_settings(tmp_path), FailingBackend(), cwd=tmp_path)
    outcome = run(session.run("do it"))
    assert outcome.code is TerminalCode.MODEL_UNAVAILABLE


def test_malformed_workflow_is_configuration_invalid(tmp_path):
    """A broken WORKFLOW.md fails closed as CONFIGURATION_INVALID."""
    (tmp_path / "WORKFLOW.md").write_text("# broken, no toml fence\n")
    session = Session(_settings(tmp_path), MockBackend([{"done": "ok"}]), cwd=tmp_path)
    outcome = run(session.run("do it"))
    assert outcome.code is TerminalCode.CONFIGURATION_INVALID


def test_identity_threading_into_run_dir(tmp_path):
    """B2.4: same task + same repo state -> same task_id + fingerprint;
    a dirty tree changes the fingerprint; starting_commit is recorded."""
    import json
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    (repo / "a.txt").write_text("hello\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@e.c", "commit", "-q", "-m", "i"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    settings = _settings(tmp_path)
    first = Session(settings, MockBackend([{"done": "ok"}]), cwd=repo)
    run(first.run("same task"))
    second = Session(settings, MockBackend([{"done": "ok"}]), cwd=repo)
    run(second.run("same task"))

    runs_root = tmp_path / "state" / "runs"
    tasks = [json.loads((d / "task.json").read_text()) for d in runs_root.iterdir()]
    assert len(tasks) == 2
    assert tasks[0]["task_id"] == tasks[1]["task_id"]
    assert tasks[0]["repository_fingerprint"] == tasks[1]["repository_fingerprint"]
    assert tasks[0]["starting_commit"] == tasks[1]["starting_commit"]
    assert len(tasks[0]["starting_commit"]) == 40

    # dirty the tree -> the fingerprint must change
    (repo / "a.txt").write_text("modified\n")
    third = Session(settings, MockBackend([{"done": "ok"}]), cwd=repo)
    run(third.run("same task"))
    tasks = [json.loads((d / "task.json").read_text()) for d in runs_root.iterdir()]
    fingerprints = {t["repository_fingerprint"] for t in tasks}
    assert len(fingerprints) == 2


# --- K5: safety net on edit-capable session start ---------------------------


def test_safety_net_fires_on_edit_capable_start(tmp_path):
    import subprocess

    repo = tmp_path / "repo"
    _git_repo(repo)
    (repo / "a.txt").write_text("dirty\n")
    session = Session(_settings(tmp_path), MockBackend([{"done": "ok"}]), cwd=repo)
    outcome = run(session.run("do it"))
    assert outcome.code is TerminalCode.COMPLETED

    nets = [
        e
        for e in session.bus.history
        if e.kind == "gate_decision" and e.data.get("gate") == "safety_net"
    ]
    assert len(nets) == 1
    assert nets[0].data["tag"].startswith("pxx-pre/")
    assert "pxx safety net" in nets[0].data["stash"]
    assert "[net: " in outcome.summary and "+stash" in outcome.summary
    clean = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True
    )
    assert clean.stdout == ""  # the user's dirt is parked, not lost
    # K8 snapshot happens after the stash: pre-existing dirt is NOT
    # attributed to this run
    assert outcome.diff_lines == 0


def test_safety_net_skipped_in_ask_mode(tmp_path):
    import subprocess

    repo = tmp_path / "repo"
    _git_repo(repo)
    (repo / "a.txt").write_text("dirty\n")
    settings = _settings(tmp_path, PermissionMode.ASK)
    session = Session(settings, MockBackend([{"say": "hi"}, {"done": "answered"}]), cwd=repo)
    outcome = run(session.run("a question"))
    assert "[net:" not in outcome.summary
    tags = subprocess.run(
        ["git", "tag", "-l", "pxx-pre/*"], cwd=repo, capture_output=True, text=True
    )
    assert tags.stdout == ""
    assert (repo / "a.txt").read_text() == "dirty\n"  # untouched


def test_safety_net_disabled_by_config(tmp_path):
    import subprocess

    repo = tmp_path / "repo"
    _git_repo(repo)
    (repo / "a.txt").write_text("dirty\n")
    settings = _settings(tmp_path, safety_net=False)
    session = Session(settings, MockBackend([{"done": "ok"}]), cwd=repo)
    outcome = run(session.run("do it"))
    assert "[net:" not in outcome.summary
    tags = subprocess.run(
        ["git", "tag", "-l", "pxx-pre/*"], cwd=repo, capture_output=True, text=True
    )
    assert tags.stdout == ""
    assert (repo / "a.txt").read_text() == "dirty\n"  # untouched
