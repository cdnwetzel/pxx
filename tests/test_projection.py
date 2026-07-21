"""Tests for pxx.projection: RunOutcome projected from the event stream (B10.4)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from pxx.backends.mock import MockBackend
from pxx.config import ModelRef, Settings
from pxx.outcome import TerminalCode
from pxx.projection import project_outcome
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


def test_recorded_outcome_equals_stream_projection(tmp_path):
    """The persisted outcome.json IS a projection of the run's event stream —
    the record cannot disagree with what happened."""
    work = tmp_path / "work"
    work.mkdir()
    session = Session(
        _settings(tmp_path),
        MockBackend(
            [
                {"tool": "write_file", "args": {"path": "a.py", "content": "x"}},
                {"done": "ok"},
            ]
        ),
        cwd=work,
    )
    outcome = run(session.run("write a.py"))
    assert outcome.code is TerminalCode.COMPLETED

    run_dir = sorted((tmp_path / "state" / "runs").iterdir())[-1]
    recorded = json.loads((run_dir / "outcome.json").read_text())
    stream = [
        json.loads(line)
        for line in (run_dir / "events.jsonl").read_text().splitlines()
        if line.strip()
    ]
    projected = project_outcome(stream, session.session_id)
    assert recorded["code"] == str(projected.code)
    assert recorded["rounds"] == projected.rounds
    assert recorded["tokens"] == projected.tokens
    assert recorded["files_changed"] == projected.files_changed
    # and the projection matches the constructed outcome on the same fields
    assert projected.code is outcome.code


def test_projection_neutral_on_empty_stream():
    projected = project_outcome([])
    assert projected.code is TerminalCode.MODEL_UNAVAILABLE
    assert projected.rounds == 0
