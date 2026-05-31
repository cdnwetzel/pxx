"""Tests for pxx.safety — pre-session safety foundation (#002)."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock

from pxx import safety


class TestHasUnmergedAutonomousCommits:
    def test_returns_true_when_autonomous_commits_exist(self, monkeypatch):
        mock_run = MagicMock()
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "abc1234 [autonomous] fix: something\ndef5678 feat: other\n"
        monkeypatch.setattr(subprocess, "run", mock_run)

        result = safety._has_unmerged_autonomous_commits()
        assert result is True

    def test_returns_false_when_no_autonomous_commits(self, monkeypatch):
        mock_run = MagicMock()
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "abc1234 feat: something\ndef5678 fix: other\n"
        monkeypatch.setattr(subprocess, "run", mock_run)

        result = safety._has_unmerged_autonomous_commits()
        assert result is False

    def test_returns_false_on_git_error(self, monkeypatch):
        mock_run = MagicMock()
        mock_run.return_value.returncode = 128
        monkeypatch.setattr(subprocess, "run", mock_run)

        result = safety._has_unmerged_autonomous_commits()
        assert result is False

    def test_returns_false_on_timeout(self, monkeypatch):
        def mock_timeout(*a, **kw):
            raise subprocess.TimeoutExpired(cmd="git", timeout=2.0)

        monkeypatch.setattr(subprocess, "run", mock_timeout)

        result = safety._has_unmerged_autonomous_commits()
        assert result is False


class TestCreateTag:
    def test_skips_stash_when_autonomous_commits_exist(self, monkeypatch):
        monkeypatch.setattr("pxx.safety._git.is_in_repo", lambda: True)
        monkeypatch.setattr(
            "pxx.safety._has_unmerged_autonomous_commits", lambda: True
        )

        result = safety.create_tag()
        assert result is None

    def test_skips_stash_during_pytest(self, monkeypatch):
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/test_safety.py::TestCreateTag")
        monkeypatch.setattr("pxx.safety._git.is_in_repo", lambda: True)

        result = safety.create_tag()
        assert result is None

    def test_returns_none_if_not_in_repo(self, monkeypatch):
        monkeypatch.setattr("pxx.safety._git.is_in_repo", lambda: False)

        result = safety.create_tag()
        assert result is None
