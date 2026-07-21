"""Tests for pxx.clarify: the ambiguity gate (B1.2)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from pxx.backends.mock import MockBackend
from pxx.clarify import ReadyState, ready_to_act
from pxx.config import ModelRef, Settings
from pxx.outcome import TerminalCode
from pxx.safety import PermissionMode
from pxx.session import Session


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


# --- pure heuristics -----------------------------------------------------------


def test_clear_task_is_ready(tmp_path: Path) -> None:
    decision = ready_to_act("add a docstring to the widget", cwd=tmp_path, test_command=None)
    assert decision.state is ReadyState.READY_TO_EXECUTE


def test_empty_task_asks(tmp_path: Path) -> None:
    decision = ready_to_act("   ", cwd=tmp_path, test_command=None)
    assert decision.state is ReadyState.QUESTION_REQUIRED
    assert decision.question


def test_test_intent_without_test_command_asks(tmp_path: Path) -> None:
    decision = ready_to_act("make the tests pass", cwd=tmp_path, test_command=None)
    assert decision.state is ReadyState.QUESTION_REQUIRED
    assert "test command" in decision.question


def test_test_intent_with_test_command_proceeds(tmp_path: Path) -> None:
    decision = ready_to_act("make the tests pass", cwd=tmp_path, test_command="pytest")
    assert decision.state is ReadyState.READY_TO_EXECUTE


def test_edit_verb_with_missing_file_asks(tmp_path: Path) -> None:
    decision = ready_to_act("fix the bug in src/nope.py", cwd=tmp_path, test_command=None)
    assert decision.state is ReadyState.INSUFFICIENT_CONTEXT
    assert "src/nope.py" in decision.question


def test_edit_verb_with_existing_file_proceeds(tmp_path: Path) -> None:
    (tmp_path / "real.py").write_text("x = 1\n")
    decision = ready_to_act("fix the bug in real.py", cwd=tmp_path, test_command=None)
    assert decision.state is ReadyState.READY_TO_EXECUTE


def test_create_verb_with_missing_file_proceeds(tmp_path: Path) -> None:
    decision = ready_to_act("create a new module src/fresh.py", cwd=tmp_path, test_command=None)
    assert decision.state is ReadyState.READY_TO_EXECUTE


# --- session wiring: ambiguous tasks stop WITHOUT editing ------------------------


def test_ambiguous_task_stops_without_editing(tmp_path: Path) -> None:
    backend = MockBackend(
        [{"tool": "write_file", "args": {"path": "out.txt", "content": "x"}}, {"done": "wrote"}]
    )
    session = Session(_settings(tmp_path), backend, cwd=tmp_path)
    outcome = asyncio.run(session.run("fix the bug in src/missing.py"))
    assert outcome.code is TerminalCode.CLARIFICATION_REQUIRED
    assert "src/missing.py" in outcome.summary  # the question is surfaced
    assert not (tmp_path / "out.txt").exists()  # nothing was edited
    kinds = [e.kind for e in session.bus.history]
    assert "gate_decision" in kinds
    gate = next(e for e in session.bus.history if e.kind == "gate_decision")
    assert gate.data["gate"] == "clarification" and gate.data["allowed"] is False


def test_clear_task_runs_backend(tmp_path: Path) -> None:
    backend = MockBackend([{"done": "ok"}])
    session = Session(_settings(tmp_path), backend, cwd=tmp_path)
    outcome = asyncio.run(session.run("say hello"))
    assert outcome.code is TerminalCode.COMPLETED


def test_healing_round_skips_clarity_check(tmp_path: Path) -> None:
    """The loop re-gates only round 1; healing prompts are never gated."""
    backend = MockBackend([{"done": "ok"}])
    session = Session(_settings(tmp_path), backend, cwd=tmp_path)
    outcome = asyncio.run(session.run("fix the bug in src/missing.py", check_clarity=False))
    assert outcome.code is TerminalCode.COMPLETED
