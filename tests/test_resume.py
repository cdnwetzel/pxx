"""Tests for pxx.resume: checkpoint + resume-from-checkpoint (B9.3)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pxx.backends.mock import MockBackend
from pxx.config import ModelRef, Settings
from pxx.errors import BackendError
from pxx.outcome import TerminalCode
from pxx.resume import resume_run, write_checkpoint
from pxx.safety import PermissionMode
from pxx.session import Session


def run(coro):
    return asyncio.run(coro)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        model=ModelRef(provider="ollama", model="test-model"),
        permission=PermissionMode.AUTO,
        memory_enabled=False,
        memory_dir=tmp_path / "mem",
        state_dir=tmp_path / "state",
    )


def test_pause_checkpoint_resume_same_outcome(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    settings = _settings(tmp_path)
    session = Session(
        settings,
        MockBackend(
            [
                {"tool": "write_file", "args": {"path": "out.txt", "content": "hi"}},
                {"done": "done"},
            ]
        ),
        cwd=work,
    )
    original = run(session.run("do it"))
    assert original.code is TerminalCode.COMPLETED
    run_dir = sorted((tmp_path / "state" / "runs").iterdir())[-1]

    # pause: checkpoint the trajectory
    checkpoint = write_checkpoint(tmp_path / "state", run_dir.name)
    assert checkpoint.events_count > 0

    # rewind the tree, then resume: the trajectory replays to the same state
    (work / "out.txt").unlink()
    resumed = run(resume_run(tmp_path / "state", run_dir.name, settings, cwd=work))
    assert resumed.code is original.code
    assert (work / "out.txt").read_text() == "hi"


def test_resume_without_checkpoint_fails_closed(tmp_path):
    run_dir = tmp_path / "state" / "runs" / "run-x"
    run_dir.mkdir(parents=True)
    with pytest.raises(BackendError, match="no checkpoint"):
        run(resume_run(tmp_path / "state", "run-x", _settings(tmp_path)))


def test_checkpoint_without_events_fails_closed(tmp_path):
    run_dir = tmp_path / "state" / "runs" / "run-x"
    run_dir.mkdir(parents=True)
    with pytest.raises(BackendError, match="no recorded events"):
        write_checkpoint(tmp_path / "state", "run-x")
