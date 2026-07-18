"""Tests for pxx.governance — pre-push governance gate."""

from __future__ import annotations

from unittest.mock import MagicMock

from pxx.governance import (
    CONTENT_ALLOW_PRAGMA,
    _scan_content_lines,
    check_review_verdict,
    check_version_sync,
    load_content_denylist,
    scan_public_content,
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

    def test_git_failure_fails_closed(self, tmp_path, monkeypatch):
        # "Couldn't run the scanner" must NOT read as "no secrets" — a gate
        # that can't scan blocks, it doesn't wave the commit through
        # (reviewer finding, 2026-07-17).
        monkeypatch.chdir(tmp_path)

        def mock_run(*args, **kwargs):
            raise FileNotFoundError("git not found")

        monkeypatch.setattr("pxx.governance.subprocess.run", mock_run)

        violations = scan_staged_secrets(tmp_path)
        assert len(violations) == 1
        assert violations[0].severity == "error"
        assert "could not run" in violations[0].detail

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


class TestScanContentLines:
    """Pattern logic for the public-content scanner (roadmap Phase 0.1)."""

    def _hits(self, content: str, denylist=()) -> list[str]:
        return [v.detail for v in _scan_content_lines("f.md", content, list(denylist))]

    def test_private_ipv4_ranges_flagged(self):
        import re as _re  # noqa: F401

        assert self._hits("host at 10.1.2.3")  # pxx-content: allow
        assert self._hits("host at 172.20.1.1")  # pxx-content: allow
        assert self._hits("host at 192.168.5.9")  # pxx-content: allow

    def test_non_private_addresses_pass(self):
        assert self._hits("loopback 127.0.0.1:8003") == []
        assert self._hits("public 8.8.8.8 and 172.32.1.1") == []
        assert self._hits("docs example 192.0.2.7") == []

    def test_internal_hostname_suffix_flagged(self):
        assert self._hits("reach box.local now")  # pxx-content: allow
        assert self._hits("via gpu.internal path")  # pxx-content: allow

    def test_localhost_is_not_an_internal_suffix(self):
        assert self._hits("http://localhost:11434") == []

    def test_home_path_real_username_flagged(self):
        assert self._hits("/Users/jsmith/ai/repo")  # pxx-content: allow
        assert self._hits("/home/jsmith/work")  # pxx-content: allow

    def test_home_path_placeholders_pass(self):
        assert self._hits("cd /Users/you/project") == []
        assert self._hits("cd /home/user/project") == []
        assert self._hits("cd /Users/example/project") == []

    def test_unprotected_service_statement_flagged(self):
        assert self._hits("the fleet has no auth at all")  # pxx-content: allow
        assert self._hits("runs without authentication")  # pxx-content: allow

    def test_auth_prose_passes(self):
        assert self._hits("requests require authentication") == []
        assert self._hits("auth is enforced at the proxy") == []

    def test_pragma_line_is_exempt(self):
        line = f"host at 10.1.2.3  # {CONTENT_ALLOW_PRAGMA}"  # pxx-content: allow
        assert self._hits(line) == []

    def test_one_report_per_pattern_per_file(self):
        content = "a 10.1.1.1\nb 10.2.2.2"  # pxx-content: allow
        hits = self._hits(content)
        assert len(hits) == 1

    def test_denylist_match_does_not_echo_term(self):
        import re

        deny = [re.compile(re.escape("hushpuppy-node"), re.IGNORECASE)]
        hits = self._hits("deploy to HUSHPUPPY-NODE tonight", deny)
        assert len(hits) == 1
        assert "hushpuppy-node" not in hits[0].lower()
        assert "denylist entry #1" in hits[0]


class TestContentDenylistLoading:
    def test_loads_repo_private_and_config_files(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        (repo / "private").mkdir(parents=True)
        (repo / "private" / "content-denylist.txt").write_text(
            "# comment\nalpha-host\n\nbeta-host\n"
        )
        cfg = tmp_path / "xdg" / "pxx"
        cfg.mkdir(parents=True)
        (cfg / "content-denylist").write_text("gamma-user\n")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        patterns = load_content_denylist(repo)
        assert len(patterns) == 3
        assert any(p.search("ALPHA-HOST") for p in patterns)

    def test_missing_files_mean_empty_denylist(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "nowhere"))
        assert load_content_denylist(tmp_path) == []


class TestScanPublicContent:
    def test_staged_mode_scans_index_content(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "nowhere"))
        staged = "server at 10.9.9.9 tonight"  # pxx-content: allow

        def mock_run(cmd, *args, **kwargs):
            result = MagicMock()
            result.returncode = 0
            if cmd[1] == "diff":
                result.stdout = "notes.md"
            elif cmd[1] == "show":
                result.stdout = staged
            return result

        monkeypatch.setattr("pxx.governance.subprocess.run", mock_run)
        violations = scan_public_content(tmp_path)
        assert len(violations) == 1
        assert violations[0].check == "public-content"
        assert "notes.md:1" in violations[0].detail

    def test_full_tree_mode_reads_tracked_worktree_files(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "nowhere"))
        (tmp_path / "doc.md").write_text(
            "connect to rack.lan today\n"  # pxx-content: allow
        )

        def mock_run(cmd, *args, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = "doc.md" if cmd[1] == "ls-files" else ""
            return result

        monkeypatch.setattr("pxx.governance.subprocess.run", mock_run)
        violations = scan_public_content(
            tmp_path, full_tree=True, allow_empty_denylist=True
        )
        structural = [v for v in violations if "coverage DISABLED" not in v.detail]
        assert len(structural) == 1
        assert "internal-hostname-suffix" in structural[0].detail

    def test_clean_content_passes_both_modes(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "nowhere"))
        (tmp_path / "doc.md").write_text("use <lan-vllm-host> on 127.0.0.1\n")

        def mock_run(cmd, *args, **kwargs):
            result = MagicMock()
            result.returncode = 0
            if cmd[1] == "ls-files":
                result.stdout = "doc.md"
            elif cmd[1] == "diff":
                result.stdout = ""
            return result

        monkeypatch.setattr("pxx.governance.subprocess.run", mock_run)
        assert scan_public_content(tmp_path) == []  # staged mode: no coverage gate
        fulltree = scan_public_content(
            tmp_path, full_tree=True, allow_empty_denylist=True
        )
        assert [v for v in fulltree if "coverage DISABLED" not in v.detail] == []


class TestContentScannerFalsePositives:
    """FP classes found by the first live audit (2026-07-16)."""

    def _hits(self, content: str) -> list[str]:
        return [v.detail for v in _scan_content_lines("f.py", content, [])]

    def test_path_home_calls_pass(self):
        assert self._hits('log = Path.home() / ".local" / "state"') == []

    def test_local_method_call_passes(self):
        assert self._hits("value = obj.local()") == []

    def test_users_foo_fixture_passes(self):
        assert self._hits('"/Users/foo/ai/repo/cli.py"') == []

    def test_lockfiles_are_skipped(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "nowhere"))
        quad = 'version = "10.4.0.35"\n'  # pxx-content: allow
        (tmp_path / "uv.lock").write_text(quad)

        def mock_run(cmd, *args, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = "uv.lock" if cmd[1] == "ls-files" else ""
            return result

        monkeypatch.setattr("pxx.governance.subprocess.run", mock_run)
        vs = scan_public_content(tmp_path, full_tree=True, allow_empty_denylist=True)
        assert [v for v in vs if "coverage DISABLED" not in v.detail] == []


class TestShippedContentScan:
    """Release gate scans only what reaches PyPI (2026-07-17)."""

    def test_shipped_scope_excludes_dev_trees(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "nowhere"))
        # A leak in review/ (dev-only) must NOT gate a release; the same leak
        # in pxx/ (shipped) MUST.
        (tmp_path / "review").mkdir()
        (tmp_path / "review" / "notes.md").write_text(
            "deploy to box.lan tonight\n"  # pxx-content: allow
        )
        (tmp_path / "pxx").mkdir()
        (tmp_path / "pxx" / "leak.py").write_text(
            "HOST = 'rack.internal'\n"  # pxx-content: allow
        )

        def mock_run(cmd, *args, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = (
                "review/notes.md\npxx/leak.py" if cmd[1] == "ls-files" else ""
            )
            return result

        monkeypatch.setattr("pxx.governance.subprocess.run", mock_run)
        hits = scan_public_content(tmp_path, shipped_only=True)
        files = {v.detail.split(":")[0] for v in hits}
        assert "pxx/leak.py" in files
        assert "review/notes.md" not in files

    def test_shipped_prefixes_are_the_packaged_set(self):
        from pxx.governance import _SHIPPED_PREFIXES

        # Guards against drift from the sdist manifest; if packaging changes,
        # this list and the check must move together.
        assert set(_SHIPPED_PREFIXES) == {
            "pxx/",
            "tests/",
            "README.md",
            "pyproject.toml",
        }


class TestContentScanFailsClosed:
    def test_git_failure_blocks_content_scan(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "nowhere"))

        def boom(*a, **k):
            raise FileNotFoundError("git gone")

        monkeypatch.setattr("pxx.governance.subprocess.run", boom)
        violations = scan_public_content(tmp_path, full_tree=True)
        assert len(violations) == 1
        assert violations[0].severity == "error"
        assert "could not run" in violations[0].detail


class TestP0HostnameCoverageArmed:
    """[P0] a --shipped/audit scan with 0 denylist patterns must not pass
    silently — bare hostnames are denylist-only, so 0 patterns == coverage OFF."""

    def _shipped_repo(self, tmp_path, monkeypatch):
        # Neutralize the user-config denylist path so only repo/private is
        # consulted (else a real ~/.config/pxx/content-denylist would arm it).
        import subprocess as sp

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        repo = _init_repo(tmp_path)
        (repo / "pxx").mkdir()
        # Placeholder, not a real fleet host — the privacy contract bans real
        # hostnames even in tracked test fixtures; use gpu-node.
        (repo / "pxx" / "mod.py").write_text("# eval host: gpu-node\n")
        sp.run(["git", "add", "."], cwd=repo, check=True)
        return repo

    def test_zero_denylist_shipped_emits_coverage_disabled_error(
        self, tmp_path, monkeypatch
    ):
        repo = self._shipped_repo(tmp_path, monkeypatch)
        cov = [
            v
            for v in scan_public_content(repo, shipped_only=True)
            if "coverage DISABLED" in v.detail
        ]
        assert cov and cov[0].severity == "error"

    def test_allow_empty_downgrades_to_warning(self, tmp_path, monkeypatch):
        repo = self._shipped_repo(tmp_path, monkeypatch)
        cov = [
            v
            for v in scan_public_content(
                repo, shipped_only=True, allow_empty_denylist=True
            )
            if "coverage DISABLED" in v.detail
        ]
        assert cov and cov[0].severity == "warning"

    def test_armed_denylist_flags_the_hostname_and_clears_coverage_signal(
        self, tmp_path, monkeypatch
    ):
        repo = self._shipped_repo(tmp_path, monkeypatch)
        (repo / "private").mkdir()
        (repo / "private" / "content-denylist.txt").write_text("gpu-node\n")
        vs = scan_public_content(repo, shipped_only=True)
        assert not any("coverage DISABLED" in v.detail for v in vs)
        assert any("private denylist entry" in v.detail for v in vs)

    def test_run_governance_check_soft_fails_unarmed_passes_with_opt_out(
        self, tmp_path, monkeypatch
    ):
        from pxx.governance import run_governance_check

        repo = self._shipped_repo(tmp_path, monkeypatch)
        monkeypatch.delenv("PXX_GOVERNANCE_SKIP", raising=False)
        assert run_governance_check(repo, shipped_content=True) == 1
        assert (
            run_governance_check(repo, shipped_content=True, allow_empty_denylist=True)
            == 0
        )


class TestReleaseGateArmsDenylist:
    """[P0] pin the workflow parity: release arms from a secret and runs the
    check WITHOUT the opt-out (so it can fail); CI opts out deliberately."""

    def _wf(self, name):
        from pathlib import Path

        return (
            Path(__file__).resolve().parent.parent / ".github" / "workflows" / name
        ).read_text()

    def test_release_materializes_denylist_from_secret_and_can_fail(self):
        text = self._wf("release.yml")
        assert "PXX_CONTENT_DENYLIST" in text
        assert "content-denylist" in text
        # the armed gate must NOT carry the opt-out, or it could never fail
        assert "--allow-empty-denylist" not in text

    def test_ci_uses_explicit_opt_out(self):
        assert "--check --shipped --allow-empty-denylist" in self._wf("ci.yml")


class TestShippedScopeHasNoFleetHostname:
    """[P0.1]+[P0.3] regression: the P0 arming commit re-leaked fleet hostnames
    into shipped source (governance.py docstring; then test fixtures), and the
    armed gate would have blocked the release on them. Guard the ENTIRE shipped
    scope — pxx/ + tests/ + README + pyproject, exactly what release.yml scans —
    so a re-leak anywhere in what ships fails here. Uses the real untracked
    denylist when present (a dev with it armed catches a re-leak pre-commit);
    skips in a bare env (CI), where release.yml's armed-from-secret gate is the
    guard. Real fleet names are never embedded in this tracked test."""

    def test_shipped_scope_has_no_fleet_hostname(self):
        from pathlib import Path

        import pytest

        from pxx.governance import load_content_denylist, scan_public_content

        repo = Path(__file__).resolve().parent.parent
        if not load_content_denylist(repo):
            pytest.skip("no local denylist; release.yml armed gate covers CI")

        leaks = [
            v.detail
            for v in scan_public_content(repo, shipped_only=True)
            if "private denylist entry" in v.detail
        ]
        assert not leaks, f"a shipped file names a fleet host: {leaks}"
