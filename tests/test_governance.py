"""Tests for pxx.governance — pre-push governance gate."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pxx.governance import (
    check_review_verdict,
    check_version_sync,
    scan_staged_secrets,
)


class TestScanStagedSecrets:
    def test_detects_api_key_literal(self, tmp_path, monkeypatch):
        # Set up a git repo with staged file containing secret
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".git").mkdir()

        # Create a file with an API key
        secret_file = tmp_path / "config.py"
        secret_file.write_text('API_KEY = "sk1234567890abcdefghijklmnopqrstuvwxyz"')

        # Mock git diff to return the file as staged
        def mock_run(*args, **kwargs):
            result = MagicMock()
            result.stdout = "config.py"
            result.returncode = 0
            return result

        monkeypatch.setattr("subprocess.run", mock_run)

        violations = scan_staged_secrets(tmp_path)
        assert len(violations) > 0
        assert any("api-key" in v.detail.lower() or "secret" in v.detail.lower() for v in violations)

    def test_detects_github_token(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".git").mkdir()

        secret_file = tmp_path / "secrets.txt"
        secret_file.write_text("ghp_1234567890abcdefghijklmnopqrstuvwxyz1234")

        def mock_run(*args, **kwargs):
            result = MagicMock()
            result.stdout = "secrets.txt"
            result.returncode = 0
            return result

        monkeypatch.setattr("subprocess.run", mock_run)

        violations = scan_staged_secrets(tmp_path)
        assert len(violations) > 0
        assert any("github" in v.detail.lower() for v in violations)

    def test_returns_empty_when_no_secrets(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".git").mkdir()

        safe_file = tmp_path / "safe.py"
        safe_file.write_text("print('hello world')")

        def mock_run(*args, **kwargs):
            result = MagicMock()
            result.stdout = "safe.py"
            result.returncode = 0
            return result

        monkeypatch.setattr("subprocess.run", mock_run)

        violations = scan_staged_secrets(tmp_path)
        # Should be empty or only have non-secret violations
        assert all(v.check != "secrets" for v in violations)

    def test_handles_git_failure_gracefully(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        def mock_run(*args, **kwargs):
            raise FileNotFoundError("git not found")

        monkeypatch.setattr("subprocess.run", mock_run)

        violations = scan_staged_secrets(tmp_path)
        assert violations == []


class TestCheckVersionSync:
    def test_detects_version_mismatch(self, tmp_path):
        (tmp_path / "VERSION").write_text("1.2.3")
        (tmp_path / "package.json").write_text('{"version": "1.2.4"}')

        config = {
            "version_files": [
                {"path": "VERSION", "parser": "plaintext"},
                {"path": "package.json", "parser": "json:version"},
            ]
        }

        violations = check_version_sync(tmp_path, config)
        assert len(violations) > 0
        assert any("mismatch" in v.detail.lower() for v in violations)

    def test_accepts_matching_versions(self, tmp_path):
        (tmp_path / "VERSION").write_text("1.2.3")
        (tmp_path / "package.json").write_text('{"version": "1.2.3"}')

        config = {
            "version_files": [
                {"path": "VERSION", "parser": "plaintext"},
                {"path": "package.json", "parser": "json:version"},
            ]
        }

        violations = check_version_sync(tmp_path, config)
        # Should not have error-level violations for version mismatch
        errors = [v for v in violations if v.severity == "error" and "mismatch" in v.detail.lower()]
        assert len(errors) == 0

    def test_handles_missing_file(self, tmp_path):
        config = {
            "version_files": [
                {"path": "MISSING.txt", "parser": "plaintext"},
            ]
        }

        violations = check_version_sync(tmp_path, config)
        assert len(violations) > 0
        assert any("not found" in v.detail.lower() for v in violations)

    def test_handles_invalid_json(self, tmp_path):
        (tmp_path / "package.json").write_text("{ invalid json }")

        config = {
            "version_files": [
                {"path": "package.json", "parser": "json:version"},
            ]
        }

        violations = check_version_sync(tmp_path, config)
        assert len(violations) > 0
        assert any("invalid json" in v.detail.lower() for v in violations)

    def test_parses_changelog_header(self, tmp_path):
        (tmp_path / "CHANGELOG.md").write_text("## [2.0.0]\nSome changes")
        (tmp_path / "VERSION").write_text("2.0.0")

        config = {
            "version_files": [
                {"path": "CHANGELOG.md", "parser": "changelog-header"},
                {"path": "VERSION", "parser": "plaintext"},
            ]
        }

        violations = check_version_sync(tmp_path, config)
        errors = [v for v in violations if v.severity == "error" and "mismatch" in v.detail.lower()]
        assert len(errors) == 0

    def test_parses_python_assignment(self, tmp_path):
        (tmp_path / "config.py").write_text('VERSION = "1.5.0"')
        (tmp_path / "VERSION").write_text("1.5.0")

        config = {
            "version_files": [
                {"path": "config.py", "parser": "py-assign:VERSION"},
                {"path": "VERSION", "parser": "plaintext"},
            ]
        }

        violations = check_version_sync(tmp_path, config)
        errors = [v for v in violations if v.severity == "error" and "mismatch" in v.detail.lower()]
        assert len(errors) == 0


class TestCheckReviewVerdict:
    def test_warns_on_review_pending(self, tmp_path):
        from pxx.workflow import WorkflowState, save_state

        state = WorkflowState(phase="review_pending", review_verdict="(none yet)")
        save_state(state, tmp_path)

        violations = check_review_verdict(tmp_path)
        assert len(violations) > 0
        assert any("pending" in v.detail.lower() for v in violations)

    def test_errors_on_rejected(self, tmp_path):
        from pxx.workflow import WorkflowState, save_state

        state = WorkflowState(phase="rejected")
        save_state(state, tmp_path)

        violations = check_review_verdict(tmp_path)
        assert len(violations) > 0
        errors = [v for v in violations if v.severity == "error"]
        assert len(errors) > 0
        assert any("rejected" in v.detail.lower() for v in errors)

    def test_returns_empty_on_idle(self, tmp_path):
        from pxx.workflow import WorkflowState, save_state

        state = WorkflowState(phase="idle")
        save_state(state, tmp_path)

        violations = check_review_verdict(tmp_path)
        assert len(violations) == 0

    def test_returns_empty_when_no_state(self, tmp_path):
        violations = check_review_verdict(tmp_path)
        assert violations == []
