"""Tests for pxx.drift — cross-machine sync check (#006)."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock

from pxx import drift


class TestDrift:
    def test_synced_heads_return_synced_result(self, monkeypatch):
        monkeypatch.setattr("pxx.drift._get_pxx_local_head", lambda: "abc1234")
        monkeypatch.setattr("pxx.drift._get_pxx_local_branch", lambda: "main")

        def mock_remote(*a, **kw):
            return "abc1234", "main", None

        monkeypatch.setattr("pxx.drift._get_remote_state", mock_remote)

        result = drift.check_sync(ssh_target="u@h", remote_path="/p")
        assert result.is_synced is True
        assert result.local_sha == "abc1234"
        assert result.remote_sha == "abc1234"
        assert result.error is None

    def test_diverged_heads_return_drift_result(self, monkeypatch):
        monkeypatch.setattr("pxx.drift._get_pxx_local_head", lambda: "local_sha")
        monkeypatch.setattr("pxx.drift._get_pxx_local_branch", lambda: "main")

        def mock_remote(*a, **kw):
            return "remote_sha", "main", None

        monkeypatch.setattr("pxx.drift._get_remote_state", mock_remote)

        result = drift.check_sync(ssh_target="u@h", remote_path="/p")
        assert result.is_synced is False
        assert result.local_sha == "local_sha"
        assert result.remote_sha == "remote_sha"
        assert result.error is None

    def test_remote_state_probes_over_ssh(self, monkeypatch):
        mock_run = MagicMock()
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "remote_sha\nremote_branch\n"
        monkeypatch.setattr(subprocess, "run", mock_run)

        sha, branch, error = drift._get_remote_state(
            "user@host", "/path/to/repo", timeout=5.0
        )

        assert sha == "remote_sha"
        assert branch == "remote_branch"
        assert error is None

        # Verify the command construction
        args, kwargs = mock_run.call_args
        cmd_list = args[0]
        assert cmd_list[0] == "ssh"
        assert cmd_list[1] == "user@host"
        assert "git -C /path/to/repo rev-parse HEAD --abbrev-ref HEAD" in cmd_list[2]
        assert kwargs["timeout"] == 5.0

    def test_remote_state_handles_ssh_timeout(self, monkeypatch):
        def mock_timeout(*a, **kw):
            raise subprocess.TimeoutExpired(cmd="ssh", timeout=1.0)

        monkeypatch.setattr(subprocess, "run", mock_timeout)

        sha, branch, error = drift._get_remote_state("h", "p", 1.0)
        assert sha is None
        assert "timeout" in error.lower()

    def test_remote_state_handles_non_repo_remote(self, monkeypatch):
        mock_run = MagicMock()
        mock_run.return_value.returncode = 128
        mock_run.return_value.stderr = "fatal: not a git repository"
        monkeypatch.setattr(subprocess, "run", mock_run)

        sha, branch, error = drift._get_remote_state("h", "p", 1.0)
        assert sha is None
        assert "not a git repository" in error

    def test_print_report_synced(self, capsys):
        res = drift.DriftResult(
            local_sha="abcdef12345",
            remote_sha="abcdef12345",
            local_branch="main",
            remote_branch="main",
        )
        drift.print_report(res)
        out, err = capsys.readouterr()
        assert "✓ local and remote in sync at abcdef1 (main)" in err

    def test_print_report_drift(self, capsys):
        res = drift.DriftResult(
            local_sha="aaaaaa",
            remote_sha="bbbbbb",
            local_branch="feat",
            remote_branch="main",
        )
        drift.print_report(res)
        out, err = capsys.readouterr()
        assert "✗ drift detected" in err
        assert "local:  aaaaaa feat" in err
        assert "remote: bbbbbb main" in err
        assert "Sync the two checkouts" in err

    def test_print_report_timeout(self, capsys):
        res = drift.DriftResult(
            local_sha="sha",
            remote_sha=None,
            local_branch="b",
            remote_branch=None,
            error="timeout after 5s",
        )
        drift.print_report(res)
        out, err = capsys.readouterr()
        assert "? timeout after 5s; skipping drift check" in err
