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

import pytest

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
    subprocess.run(
        ["git", "commit", "-q", "-m", "seed"], cwd=tmp_path, check=True, env=env
    )
    return tmp_path


def _run_installer(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(INSTALLER), *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


ALL_HOOKS = ("pre-commit", "prepare-commit-msg", "post-commit")


class TestInstallerDropsBothHooks:
    def test_install_creates_all_three_hooks(self, tmp_path):
        repo = _init_repo(tmp_path)
        result = _run_installer(repo)
        assert result.returncode == 0, result.stderr
        for name in ALL_HOOKS:
            assert (repo / ".git" / "hooks" / name).is_file(), name

    def test_all_hooks_are_executable(self, tmp_path):
        repo = _init_repo(tmp_path)
        _run_installer(repo)
        for name in ALL_HOOKS:
            hook = repo / ".git" / "hooks" / name
            assert os.access(hook, os.X_OK), f"{name} is not executable"

    def test_all_hooks_carry_pxx_marker(self, tmp_path):
        repo = _init_repo(tmp_path)
        _run_installer(repo)
        for name in ALL_HOOKS:
            content = (repo / ".git" / "hooks" / name).read_text()
            assert "# pxx-managed pre-commit hook" in content

    def test_shebang_is_line_one(self, tmp_path):
        # The marker must go AFTER the shebang, not before it — otherwise git
        # ignores the shebang and runs the hook under /bin/sh (dash on Ubuntu),
        # where the template's `set -o pipefail` is illegal. CI caught this on
        # its first run (2026-07-17); this test keeps it caught.
        repo = _init_repo(tmp_path)
        _run_installer(repo)
        for name in ALL_HOOKS:
            first = (repo / ".git" / "hooks" / name).read_text().splitlines()[0]
            assert first.startswith("#!"), f"{name} line 1 is not a shebang: {first!r}"

    def test_reinstall_is_idempotent(self, tmp_path):
        repo = _init_repo(tmp_path)
        _run_installer(repo)
        before = {n: (repo / ".git" / "hooks" / n).read_text() for n in ALL_HOOKS}
        result = _run_installer(repo)
        assert result.returncode == 0
        for n in ALL_HOOKS:
            assert (repo / ".git" / "hooks" / n).read_text() == before[n]

    def test_uninstall_removes_all_hooks(self, tmp_path):
        repo = _init_repo(tmp_path)
        _run_installer(repo)
        result = _run_installer(repo, "--uninstall")
        assert result.returncode == 0, result.stderr
        for name in ALL_HOOKS:
            assert not (repo / ".git" / "hooks" / name).exists(), name

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


class TestPostCommitRestartHint:
    """End-to-end: the post-commit hook fires when a commit touches a pxx
    core file, and is silent otherwise.

    Runs the installed hook against a tmp repo with PYTHONPATH wired to the
    real pxx package so ``from pxx._core_files import CORE_FILES`` succeeds
    inside the hook subprocess.
    """

    def _make_commit(
        self,
        repo: Path,
        relpath: str,
        content: str = "x",
    ) -> subprocess.CompletedProcess[str]:
        path = repo / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        subprocess.run(["git", "add", relpath], cwd=repo, check=True)
        env = {
            **os.environ,
            "PXX_PRECOMMIT_SKIP": "1",
            "PYTHONPATH": str(REPO_ROOT),
        }
        return subprocess.run(
            ["git", "commit", "-q", "-m", f"touch {relpath}"],
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )

    def test_fires_on_core_file(self, tmp_path):
        repo = _init_repo(tmp_path)
        _run_installer(repo)
        result = self._make_commit(repo, "pxx/cli.py", content="# stub")
        assert "modified core pxx modules" in result.stderr, result.stderr
        assert "pxx/cli.py" in result.stderr

    def test_fires_on_endpoints(self, tmp_path):
        repo = _init_repo(tmp_path)
        _run_installer(repo)
        result = self._make_commit(repo, "pxx/endpoints.py", content="# stub")
        assert "modified core pxx modules" in result.stderr
        assert "pxx/endpoints.py" in result.stderr

    def test_silent_on_readme(self, tmp_path):
        repo = _init_repo(tmp_path)
        _run_installer(repo)
        result = self._make_commit(repo, "README.md", content="hi")
        assert "modified core pxx modules" not in result.stderr

    def test_silent_on_prompt_file(self, tmp_path):
        # Prompts apply on next aider session, not on process restart —
        # they are not core, per #008 design.
        repo = _init_repo(tmp_path)
        _run_installer(repo)
        result = self._make_commit(repo, "pxx/prompts/system.md", content="prompt")
        assert "modified core pxx modules" not in result.stderr

    def test_silent_when_pxx_not_importable(self, tmp_path):
        # Point PYTHONPATH at an empty dir so the hook's `python3 -c
        # 'from pxx._core_files import CORE_FILES'` fails — exercising the
        # silent-no-op fallback path (post-commit hook in a non-pxx repo).
        repo = _init_repo(tmp_path)
        _run_installer(repo)
        (repo / "pxx").mkdir(parents=True, exist_ok=True)
        (repo / "pxx" / "cli.py").write_text("# stub")
        subprocess.run(["git", "add", "pxx/cli.py"], cwd=repo, check=True)
        empty = tmp_path / "empty_pythonpath"
        empty.mkdir()
        env = {
            **{k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
            "PXX_PRECOMMIT_SKIP": "1",
            "PYTHONPATH": str(empty),
        }
        result = subprocess.run(
            ["git", "commit", "-q", "-m", "core file with broken pythonpath"],
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        # If the system python3 happens to have pxx globally installed, this
        # test will be a no-op (notice appears anyway). That's tolerable —
        # we still pin the positive case in the other tests.
        if "modified core pxx modules" in result.stderr:
            pytest.skip(
                "System python3 has pxx globally importable; "
                "cannot exercise the not-importable fallback path here."
            )
