"""Tests for pxx.safety guards — Task D added the previously-uncovered
True branch of _has_unmerged_autonomous_commits (the #CF-017 concurrency guard)."""

from __future__ import annotations

from unittest.mock import MagicMock

from pxx import safety


class TestHasUnmergedAutonomousCommits:
    def _patch_log(self, monkeypatch, *, returncode=0, stdout=""):
        def fake_run(cmd, *a, **k):
            r = MagicMock()
            r.returncode = returncode
            r.stdout = stdout
            return r

        monkeypatch.setattr("pxx.safety.subprocess.run", fake_run)

    def test_true_when_autonomous_commit_ahead_of_upstream(self, monkeypatch):
        self._patch_log(monkeypatch, stdout="abc123 [autonomous] seeded fix\n")
        assert safety._has_unmerged_autonomous_commits() is True

    def test_false_when_no_autonomous_marker(self, monkeypatch):
        self._patch_log(monkeypatch, stdout="abc123 ordinary commit\n")
        assert safety._has_unmerged_autonomous_commits() is False

    def test_false_on_git_error(self, monkeypatch):
        def boom(cmd, *a, **k):
            raise FileNotFoundError("git")

        monkeypatch.setattr("pxx.safety.subprocess.run", boom)
        assert safety._has_unmerged_autonomous_commits() is False
