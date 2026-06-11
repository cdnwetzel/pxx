"""Tests for pxx.workflow — workflow state management."""

from __future__ import annotations

import json

from pxx.workflow import (
    WorkflowState,
    load_state,
    resume_state,
    save_state,
    state_path,
    transition,
)


class TestWorkflowState:
    def test_default_phase_is_idle(self):
        state = WorkflowState()
        assert state.phase == "idle"

    def test_roundtrip_save_load(self, tmp_path):
        original = WorkflowState(
            phase="generating",
            session_id="sess-123",
            session_start_sha="abc123def456",
            scope=["src/", "tests/"],
            edit_mode=True,
        )
        save_state(original, tmp_path)
        loaded = load_state(tmp_path)
        assert loaded is not None
        assert loaded.phase == original.phase
        assert loaded.session_id == original.session_id
        assert loaded.session_start_sha == original.session_start_sha
        assert loaded.scope == original.scope
        assert loaded.edit_mode == original.edit_mode

    def test_save_is_atomic_tmp_cleaned_up(self, tmp_path):
        state = WorkflowState(phase="generating")
        save_state(state, tmp_path)
        # Verify no tmp file left behind
        tmp_file = state_path(tmp_path).with_suffix(".json.tmp")
        assert not tmp_file.exists()

    def test_load_returns_none_if_absent(self, tmp_path):
        result = load_state(tmp_path)
        assert result is None

    def test_load_returns_none_on_corrupt_json(self, tmp_path):
        (tmp_path / ".pxx").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".pxx" / "workflow_state.json").write_text("{ invalid json")
        result = load_state(tmp_path)
        assert result is None

    def test_load_returns_none_on_missing_file(self, tmp_path):
        result = load_state(tmp_path)
        assert result is None

    def test_load_ignores_unknown_fields(self, tmp_path):
        (tmp_path / ".pxx").mkdir(parents=True, exist_ok=True)
        path = tmp_path / ".pxx" / "workflow_state.json"
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "phase": "generating",
                    "unknown_field": "should be ignored",
                }
            )
        )
        result = load_state(tmp_path)
        assert result is not None
        assert result.phase == "generating"
        assert not hasattr(result, "unknown_field")


class TestTransition:
    def test_changes_phase(self):
        state = WorkflowState(phase="idle")
        new_state = transition(state, "generating")
        assert new_state.phase == "generating"

    def test_sets_ts_phase_changed(self):
        state = WorkflowState(phase="idle", ts_phase_changed="")
        new_state = transition(state, "generating")
        assert new_state.ts_phase_changed != ""

    def test_preserves_all_other_fields(self):
        state = WorkflowState(
            phase="idle",
            session_id="sess-123",
            scope=["src/"],
            autonomous=True,
        )
        new_state = transition(state, "generating")
        assert new_state.session_id == state.session_id
        assert new_state.scope == state.scope
        assert new_state.autonomous == state.autonomous

    def test_kwargs_override_fields(self):
        state = WorkflowState(phase="idle", healing_attempts=0)
        new_state = transition(state, "rejected", healing_attempts=2)
        assert new_state.phase == "rejected"
        assert new_state.healing_attempts == 2


class TestResumeState:
    def test_no_state_file_prints_nothing(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = resume_state(tmp_path)
        assert result == 0
        captured = capsys.readouterr()
        assert "nothing to resume" in captured.err

    def test_idle_prints_nothing(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        state = WorkflowState(phase="idle")
        save_state(state, tmp_path)
        result = resume_state(tmp_path)
        assert result == 0
        captured = capsys.readouterr()
        assert "nothing to resume" in captured.err

    def test_generating_with_commits_transitions(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        # Monkeypatch _commits_since to return non-empty list
        monkeypatch.setattr(
            "pxx.workflow._commits_since",
            lambda repo, sha: ["abc123 commit message"],
        )
        monkeypatch.setattr(
            "pxx.workflow._head_sha",
            lambda repo: "def456",
        )
        state = WorkflowState(phase="generating", session_start_sha="abc000")
        save_state(state, tmp_path)
        result = resume_state(tmp_path)
        assert result == 0
        # Verify state transitioned
        loaded = load_state(tmp_path)
        assert loaded is not None
        assert loaded.phase == "review_pending"

    def test_generating_no_commits_clears(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "pxx.workflow._commits_since",
            lambda repo, sha: [],
        )
        state = WorkflowState(phase="generating", session_start_sha="abc000")
        save_state(state, tmp_path)
        result = resume_state(tmp_path)
        assert result == 0
        # Verify state cleared
        loaded = load_state(tmp_path)
        assert loaded is not None
        assert loaded.phase == "idle"

    def test_review_pending_prints_verdict(self, tmp_path, capsys):
        state = WorkflowState(phase="review_pending", review_verdict="APPROVE")
        save_state(state, tmp_path)
        result = resume_state(tmp_path)
        assert result == 0
        captured = capsys.readouterr()
        assert "review pending" in captured.err

    def test_approved_prints_range_and_clears(self, tmp_path, capsys):
        state = WorkflowState(
            phase="approved",
            session_start_sha="abc1234567",
            session_end_sha="def4567890",
        )
        save_state(state, tmp_path)
        result = resume_state(tmp_path)
        assert result == 0
        captured = capsys.readouterr()
        assert "approved" in captured.err
        # Verify state cleared
        loaded = load_state(tmp_path)
        assert loaded is not None
        assert loaded.phase == "idle"

    def test_rejected_returns_exit_code_1(self, tmp_path, capsys):
        state = WorkflowState(phase="rejected", healing_attempts=1)
        save_state(state, tmp_path)
        result = resume_state(tmp_path)
        assert result == 1
        captured = capsys.readouterr()
        assert "rejected" in captured.err
