"""Tests for pxx.backends.replay: deterministic replay through the same gates."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from pxx.backends.base import SessionContext
from pxx.backends.mock import MockBackend
from pxx.backends.replay import ReplayBackend
from pxx.config import ModelRef, Settings
from pxx.errors import BackendError, ScopeViolation
from pxx.outcome import TerminalCode
from pxx.safety import BudgetGuard, HookRunner, PermissionMode, ScopeGate
from pxx.session import Session


def run(coro):
    return asyncio.run(coro)


def _settings(tmp_path: Path, **overrides) -> Settings:
    from dataclasses import replace

    base = Settings(
        model=ModelRef(provider="ollama", model="test-model"),
        permission=PermissionMode.AUTO,
        memory_enabled=False,
        memory_dir=tmp_path / "mem",
        state_dir=tmp_path / "state",
    )
    return replace(base, **overrides) if overrides else base


def _record_run(tmp_path: Path, work: Path, steps: list[dict]) -> Path:
    """Run a real session to produce a run dir; return the run dir path."""
    settings = _settings(tmp_path)
    session = Session(settings, MockBackend(steps), cwd=work)
    run(session.run("record this"))
    run_dirs = sorted((tmp_path / "state" / "runs").iterdir())
    assert run_dirs, "no run dir written"
    return run_dirs[-1]


def _replay_ctx(tmp_path: Path, work: Path) -> SessionContext:
    from pxx.events import EventBus
    from pxx.tools import default_registry

    settings = _settings(tmp_path)
    return SessionContext(
        settings=settings,
        bus=EventBus(),
        scope=ScopeGate(work),
        hooks=HookRunner(()),
        budgets=BudgetGuard(settings.budgets),
        tools=default_registry(),
        memory=None,
        session_id="replay-session",
        project=work.name,
        cwd=work,
        cancel_event=asyncio.Event(),
    )


def test_replay_reproduces_files_and_terminal_code(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    run_dir = _record_run(
        tmp_path,
        work,
        [
            {"tool": "write_file", "args": {"path": "out.txt", "content": "hello"}},
            {"done": "wrote"},
        ],
    )
    (work / "out.txt").unlink()  # rewind the tree

    outcome = run(ReplayBackend(run_dir).run("record this", _replay_ctx(tmp_path, work)))
    assert outcome.code is TerminalCode.COMPLETED
    assert (work / "out.txt").read_text() == "hello"

    # second replay is byte-identical
    (work / "out.txt").unlink()
    outcome2 = run(ReplayBackend(run_dir).run("record this", _replay_ctx(tmp_path, work)))
    assert outcome2.code is outcome.code
    assert (work / "out.txt").read_text() == "hello"


def test_replay_honors_scope_gate(tmp_path: Path) -> None:
    """A recorded call that escapes the CURRENT scope must be denied by the
    same broker a live run would use."""
    work = tmp_path / "work"
    work.mkdir()
    events = [
        {
            "kind": "tool_call",
            "data": {"tool": "write_file", "args": {"path": "evil/out.txt", "content": "x"}},
            "session_id": "s",
        }
    ]
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
    (run_dir / "outcome.json").write_text(json.dumps({"code": "COMPLETED"}))

    settings = _settings(tmp_path, scope=("src",))
    (work / "src").mkdir()
    from pxx.events import EventBus
    from pxx.tools import default_registry

    ctx = SessionContext(
        settings=settings,
        bus=EventBus(),
        scope=ScopeGate(work, ("src",)),
        hooks=HookRunner(()),
        budgets=BudgetGuard(settings.budgets),
        tools=default_registry(),
        memory=None,
        session_id="replay-session",
        project=work.name,
        cwd=work,
        cancel_event=asyncio.Event(),
    )
    with pytest.raises(ScopeViolation):
        run(ReplayBackend(run_dir).run("task", ctx))
    assert not (work / "evil").exists()


def test_replay_missing_events_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(BackendError, match=r"no events\.jsonl"):
        ReplayBackend(tmp_path / "nonexistent-run")


def test_replay_truncated_args_fail_closed(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_text(
        json.dumps(
            {
                "kind": "tool_call",
                "data": {
                    "tool": "write_file",
                    "args": {"path": "big.txt", "content": "x…[truncated]"},
                },
                "session_id": "s",
            }
        )
        + "\n"
    )
    (run_dir / "outcome.json").write_text(json.dumps({"code": "COMPLETED"}))
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(BackendError, match="truncated"):
        run(ReplayBackend(run_dir).run("task", _replay_ctx(tmp_path, work)))
