"""Tests for pxx.governance — pre-push governance gate."""

from __future__ import annotations

from unittest.mock import MagicMock

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
        secret_content = 'API_KEY = "sk1234567890abcdefghijklmnopqrstuvwxyz"'
        secret_file.write_text(secret_content)

        # Mock git diff and git show
        def mock_run(cmd, *args, **kwargs):
            result = MagicMock()
            if cmd[1] == "diff":
                # git diff --cached --name-only
                result.stdout = "config.py"
            elif cmd[1] == "show":
                # git show :config.py
                result.stdout = secret_content
            result.returncode = 0
            return result

        monkeypatch.setattr("pxx.governance.subprocess.run", mock_run)

        violations = scan_staged_secrets(tmp_path)
        assert len(violations) > 0
        has_secret = any(
            "api-key" in v.detail.lower() or "secret" in v.detail.lower()
            for v in violations
        )
        assert has_secret

    def test_detects_github_token(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".git").mkdir()

        secret_content = "ghp_1234567890abcdefghijklmnopqrstuvwxyz1234"
        secret_file = tmp_path / "secrets.txt"
        secret_file.write_text(secret_content)

        def mock_run(cmd, *args, **kwargs):
            result = MagicMock()
            if cmd[1] == "diff":
                result.stdout = "secrets.txt"
            elif cmd[1] == "show":
                result.stdout = secret_content
            result.returncode = 0
            return result

        monkeypatch.setattr("pxx.governance.subprocess.run", mock_run)

        violations = scan_staged_secrets(tmp_path)
        assert len(violations) > 0
        assert any("github" in v.detail.lower() for v in violations)

    def test_returns_empty_when_no_secrets(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".git").mkdir()

        safe_content = "print('hello world')"
        safe_file = tmp_path / "safe.py"
        safe_file.write_text(safe_content)

        def mock_run(cmd, *args, **kwargs):
            result = MagicMock()
            if cmd[1] == "diff":
                result.stdout = "safe.py"
            elif cmd[1] == "show":
                result.stdout = safe_content
            result.returncode = 0
            return result

        monkeypatch.setattr("pxx.governance.subprocess.run", mock_run)

        violations = scan_staged_secrets(tmp_path)
        # Should be empty or only have non-secret violations
        assert all(v.check != "secrets" for v in violations)

    def test_handles_git_failure_gracefully(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        def mock_run(*args, **kwargs):
            raise FileNotFoundError("git not found")

        monkeypatch.setattr("pxx.governance.subprocess.run", mock_run)

        violations = scan_staged_secrets(tmp_path)
        assert violations == []

    def test_index_worktree_boundary_catches_staged_secret_modified_after(
        self, tmp_path, monkeypatch
    ):
        """Verify scan catches secrets in the index even if worktree file is modified."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".git").mkdir()

        # Simulate: stage a secret, then modify file to remove it
        secret_content = 'API_KEY = "sk1234567890abcdefghijklmnopqrstuvwxyz"'
        safe_content = "# Key was here but removed"

        def mock_run(cmd, *args, **kwargs):
            result = MagicMock()
            if cmd[1] == "diff":
                # git diff --cached --name-only
                result.stdout = "config.py"
            elif cmd[1] == "show":
                # git show :config.py — returns STAGED content (with secret)
                result.stdout = secret_content
            result.returncode = 0
            return result

        monkeypatch.setattr("pxx.governance.subprocess.run", mock_run)

        # Worktree file is modified to remove secret (but git index still has it)
        secret_file = tmp_path / "config.py"
        secret_file.write_text(safe_content)

        violations = scan_staged_secrets(tmp_path)
        # Should catch the secret in the INDEX, not the modified worktree
        assert len(violations) > 0
        assert any("api-key" in v.detail.lower() for v in violations)


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
        errors = [
            v
            for v in violations
            if v.severity == "error" and "mismatch" in v.detail.lower()
        ]
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
        errors = [
            v
            for v in violations
            if v.severity == "error" and "mismatch" in v.detail.lower()
        ]
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
        errors = [
            v
            for v in violations
            if v.severity == "error" and "mismatch" in v.detail.lower()
        ]
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


class TestSecretPatternBreadth:
    """Every shipped secret pattern must actually match its target shape."""

    import pytest as _pytest

    SAMPLES = [
        ("api-key-literal", 'api_key = "abcdefgh12345678"'),
        ("openai-key", "sk-" + "a1b2c3d4" * 5),
        ("anthropic-key", "sk-ant-" + "a1b2c3d4" * 5),
        ("huggingface-token", "hf_" + "a" * 24),
        ("aws-key", "AKIA" + "ABCDEFGHIJKLMNOP"),
        ("github-token", "ghp_" + "a" * 36),
        ("bearer-token", "Authorization: Bearer " + "abcdefghij0123456789"),
        ("private-key-pem", "-----BEGIN RSA PRIVATE KEY-----"),
        ("generic-password", 'password = "hunter22"'),
    ]

    @_pytest.mark.parametrize("name,sample", SAMPLES)
    def test_pattern_matches_its_sample(self, name, sample):
        from pxx.governance import SECRET_PATTERNS

        pattern = dict(SECRET_PATTERNS)[name]
        assert pattern.search(sample), f"{name} failed to match its own sample"

    def test_all_nine_patterns_are_exercised(self):
        from pxx.governance import SECRET_PATTERNS

        assert {n for n, _ in SECRET_PATTERNS} == {n for n, _ in self.SAMPLES}


def _init_repo(tmp_path):
    import subprocess as sp

    sp.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    sp.run(["git", "config", "user.email", "x@x"], cwd=tmp_path, check=True)
    sp.run(["git", "config", "user.name", "x"], cwd=tmp_path, check=True)
    return tmp_path


class TestStagedBinaryFiles:
    def test_staged_binary_does_not_crash_the_gate(self, tmp_path):
        """A staged non-UTF-8 blob must not take down scan_staged_secrets.

        Regression: `git show :<path>` with text=True strict decoding raised
        UnicodeDecodeError (not in the caught tuple) on any staged binary.
        """
        import subprocess as sp

        repo = _init_repo(tmp_path)
        (repo / "blob.bin").write_bytes(b"\x80\x81\xfe\xff" * 8)
        (repo / "leak.py").write_text('password = "hunter22"\n')
        sp.run(["git", "add", "."], cwd=repo, check=True)

        violations = scan_staged_secrets(repo)
        # No crash, and the real secret alongside the binary is still found.
        assert any(v.check == "secrets" for v in violations)


class TestRunGovernanceCheck:
    """Aggregator-level allow/deny — the gate the loop will call."""

    def test_clean_repo_returns_0(self, tmp_path, monkeypatch):
        from pxx.governance import run_governance_check

        repo = _init_repo(tmp_path)
        monkeypatch.delenv("PXX_GOVERNANCE_SKIP", raising=False)
        assert run_governance_check(repo) == 0

    def test_staged_secret_returns_1(self, tmp_path, monkeypatch):
        import subprocess as sp

        from pxx.governance import run_governance_check

        repo = _init_repo(tmp_path)
        (repo / "config.py").write_text('api_key = "abcdefgh12345678"\n')
        sp.run(["git", "add", "."], cwd=repo, check=True)
        monkeypatch.delenv("PXX_GOVERNANCE_SKIP", raising=False)
        assert run_governance_check(repo) == 1

    def test_skip_env_inside_pytest_returns_0(self, tmp_path, monkeypatch):
        from pxx.governance import run_governance_check

        monkeypatch.setenv("PXX_GOVERNANCE_SKIP", "1")
        assert run_governance_check(tmp_path) == 0

    def test_skip_env_outside_pytest_raises(self, tmp_path, monkeypatch):
        import pytest as pt

        from pxx.governance import run_governance_check

        monkeypatch.setenv("PXX_GOVERNANCE_SKIP", "1")
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        with pt.raises(RuntimeError):
            run_governance_check(tmp_path)

    def test_invalid_governance_json_warns_but_does_not_crash(
        self, tmp_path, monkeypatch, capsys
    ):
        from pxx.governance import run_governance_check

        repo = _init_repo(tmp_path)
        (repo / ".pxx").mkdir()
        (repo / ".pxx" / "governance.json").write_text("{not json")
        monkeypatch.delenv("PXX_GOVERNANCE_SKIP", raising=False)

        rc = run_governance_check(repo)
        assert rc == 0  # warning, not error
        assert "invalid JSON" in capsys.readouterr().err
