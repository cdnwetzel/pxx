"""Integration tests for scripts/install-precommit-hook.sh (#002 M2, #012 M2).

The installer drops two hooks into .git/hooks/ and the prepare-commit-msg
hook is exercised by making real commits in a tmp git repo. Plain
subprocess + git fixtures, no monkeypatching — these are integration
tests by design.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALLER = REPO_ROOT / "scripts" / "install-precommit-hook.sh"


def _init_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with one initial commit at tmp_path."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "x@x"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "x"], cwd=tmp_path, check=True)
    # First commit so HEAD exists; needed for any --amend test paths.
    (tmp_path / "f.txt").write_text("seed")
    subprocess.run(["git", "add", "f.txt"], cwd=tmp_path, check=True)
    # Bypass the pxx pre-commit hook (which won't be installed yet anyway,
    # but also wouldn't pass: no pyproject.toml in this fake repo).
    env = {**os.environ, "PXX_PRECOMMIT_SKIP": "1"}
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=tmp_path, check=True, env=env)
    return tmp_path


def _run_installer(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(INSTALLER), *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


class TestInstallerDropsBothHooks:
    def test_install_creates_pre_commit_and_prepare_commit_msg(self, tmp_path):
        repo = _init_repo(tmp_path)
        result = _run_installer(repo)
        assert result.returncode == 0, result.stderr
        assert (repo / ".git" / "hooks" / "pre-commit").is_file()
        assert (repo / ".git" / "hooks" / "prepare-commit-msg").is_file()

    def test_both_hooks_are_executable(self, tmp_path):
        repo = _init_repo(tmp_path)
        _run_installer(repo)
        for name in ("pre-commit", "prepare-commit-msg"):
            hook = repo / ".git" / "hooks" / name
            assert os.access(hook, os.X_OK), f"{name} is not executable"

    def test_both_hooks_carry_pxx_marker(self, tmp_path):
        repo = _init_repo(tmp_path)
        _run_installer(repo)
        for name in ("pre-commit", "prepare-commit-msg"):
            content = (repo / ".git" / "hooks" / name).read_text()
            assert "# pxx-managed pre-commit hook" in content

    def test_reinstall_is_idempotent(self, tmp_path):
        repo = _init_repo(tmp_path)
        _run_installer(repo)
        first_pc = (repo / ".git" / "hooks" / "pre-commit").read_text()
        first_pcm = (repo / ".git" / "hooks" / "prepare-commit-msg").read_text()
        result = _run_installer(repo)
        assert result.returncode == 0
        assert (repo / ".git" / "hooks" / "pre-commit").read_text() == first_pc
        assert (repo / ".git" / "hooks" / "prepare-commit-msg").read_text() == first_pcm

    def test_uninstall_removes_both_hooks(self, tmp_path):
        repo = _init_repo(tmp_path)
        _run_installer(repo)
        result = _run_installer(repo, "--uninstall")
        assert result.returncode == 0, result.stderr
        assert not (repo / ".git" / "hooks" / "pre-commit").exists()
        assert not (repo / ".git" / "hooks" / "prepare-commit-msg").exists()

    def test_uninstall_skips_non_pxx_hook(self, tmp_path):
        repo = _init_repo(tmp_path)
        # User-authored hook with no marker.
        foreign = repo / ".git" / "hooks" / "pre-commit"
        foreign.parent.mkdir(parents=True, exist_ok=True)
        foreign.write_text("#!/usr/bin/env bash\necho user hook\n")
        foreign.chmod(0o755)
        result = _run_installer(repo, "--uninstall")
        # Should not delete the foreign hook.
        assert foreign.exists()
        assert "not pxx-managed" in result.stderr or "not pxx-managed" in result.stdout

    def test_refuses_to_overwrite_foreign_hook_without_force(self, tmp_path):
        repo = _init_repo(tmp_path)
        foreign = repo / ".git" / "hooks" / "pre-commit"
        foreign.parent.mkdir(parents=True, exist_ok=True)
        foreign.write_text("#!/usr/bin/env bash\necho user hook\n")
        foreign.chmod(0o755)
        result = _run_installer(repo)
        assert result.returncode != 0
        assert "not pxx-managed" in result.stderr
        # Foreign hook untouched.
        assert "echo user hook" in foreign.read_text()

    def test_force_overwrites_foreign_hook(self, tmp_path):
        repo = _init_repo(tmp_path)
        foreign = repo / ".git" / "hooks" / "pre-commit"
        foreign.parent.mkdir(parents=True, exist_ok=True)
        foreign.write_text("#!/usr/bin/env bash\necho user hook\n")
        foreign.chmod(0o755)
        result = _run_installer(repo, "--force")
        assert result.returncode == 0
        assert "# pxx-managed pre-commit hook" in foreign.read_text()


class TestPrepareCommitMsgHook:
    """End-to-end tests via real `git commit -m` in a tmp repo."""

    def _commit(
        self,
        repo: Path,
        message: str,
        autonomous: bool = False,
        amend: bool = False,
    ) -> str:
        """Make a commit; return the resulting commit message (first line)."""
        (repo / "scratch.txt").write_text(f"content-{message}")
        subprocess.run(["git", "add", "scratch.txt"], cwd=repo, check=True)
        env = {**os.environ, "PXX_PRECOMMIT_SKIP": "1"}
        # Explicitly drop PXX_AUTONOMOUS so leakage from another pytest test
        # in the same process doesn't poison this subprocess. The test
        # controls it via the `autonomous` flag, not via parent env.
        env.pop("PXX_AUTONOMOUS", None)
        if autonomous:
            env["PXX_AUTONOMOUS"] = "1"
        cmd = ["git", "commit", "-q", "-m", message]
        if amend:
            cmd = ["git", "commit", "-q", "--amend", "-m", message]
        subprocess.run(cmd, cwd=repo, check=True, env=env)
        log = subprocess.run(
            ["git", "log", "-1", "--pretty=%s"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        )
        return log.stdout.strip()

    def test_no_env_var_no_tag(self, tmp_path):
        repo = _init_repo(tmp_path)
        _run_installer(repo)
        subject = self._commit(repo, "manual commit")
        assert subject == "manual commit"
        assert "[autonomous]" not in subject

    def test_autonomous_env_prepends_tag(self, tmp_path):
        repo = _init_repo(tmp_path)
        _run_installer(repo)
        subject = self._commit(repo, "fix bug", autonomous=True)
        assert subject.startswith("[autonomous] ")
        assert subject == "[autonomous] fix bug"

    def test_idempotent_on_already_tagged_message(self, tmp_path):
        repo = _init_repo(tmp_path)
        _run_installer(repo)
        subject = self._commit(repo, "[autonomous] pre-tagged", autonomous=True)
        # Should NOT become "[autonomous] [autonomous] pre-tagged".
        assert subject == "[autonomous] pre-tagged"
        assert subject.count("[autonomous]") == 1

    def test_tag_preserves_body(self, tmp_path):
        repo = _init_repo(tmp_path)
        _run_installer(repo)
        env = {
            **os.environ,
            "PXX_PRECOMMIT_SKIP": "1",
            "PXX_AUTONOMOUS": "1",
        }
        (repo / "x.txt").write_text("x")
        subprocess.run(["git", "add", "x.txt"], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "subject line", "-m", "body paragraph"],
            cwd=repo,
            check=True,
            env=env,
        )
        full = subprocess.run(
            ["git", "log", "-1", "--pretty=%B"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert full.startswith("[autonomous] subject line")
        assert "body paragraph" in full
