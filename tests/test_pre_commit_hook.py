"""Integration tests for scripts/pre-commit-template (#016).

Philosophy: subprocess + real git repo, no monkeypatching. Each test runs
the actual hook with staged changes and verifies the exit code and stderr.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_TEMPLATE = REPO_ROOT / "scripts" / "pre-commit-template"
INSTALLER = REPO_ROOT / "scripts" / "install-precommit-hook.sh"


def _init_repo(tmp_path: Path) -> Path:
    """Initialize a bare git repo with seed commit (pre-commit hook bypassed)."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "x@x"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "x"], cwd=tmp_path, check=True)

    # Create minimal pyproject.toml for ruff (no build-system to avoid hatchling)
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        "[project]\n"
        'name = "test-repo"\n'
        'version = "0.0.1"\n'
        "\n"
        "[tool.ruff]\n"
        "[tool.ruff.lint]\n"
        'select = ["E", "F", "W"]\n'
    )

    # Seed commit with a passing test so pytest always passes initially
    (tmp_path / "README.md").write_text("# Test\n")
    (tmp_path / "test_seed.py").write_text("def test_seed():\n    assert True\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    env = {**os.environ, "PXX_PRECOMMIT_SKIP": "1"}
    subprocess.run(
        ["git", "commit", "-q", "-m", "seed"], cwd=tmp_path, check=True, env=env
    )

    return tmp_path


def _install_hook(repo: Path) -> None:
    """Install the pre-commit hook into the repo."""
    subprocess.run(
        ["bash", str(INSTALLER)],
        cwd=repo,
        capture_output=True,
        check=True,
    )


def _commit(
    repo: Path, env_extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Attempt a git commit in the repo with optional env overrides."""
    env = {**os.environ}
    # Remove PXX_PRECOMMIT_SKIP to test the actual hook (unless overridden)
    env.pop("PXX_PRECOMMIT_SKIP", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["git", "commit", "-q", "-m", "test"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


class TestBypassGate:
    """Test PXX_PRECOMMIT_SKIP=1 emergency bypass."""

    def test_skip_bypasses_all_checks_on_broken_test(self, tmp_path):
        """Stage a failing test; with bypass set, commit succeeds."""
        repo = _init_repo(tmp_path)
        _install_hook(repo)

        # Stage a file with failing test
        test_file = repo / "test_broken.py"
        test_file.write_text("def test_fail():\n    assert False\n")
        subprocess.run(["git", "add", "test_broken.py"], cwd=repo, check=True)

        # Without bypass, pytest would fail — but bypass should allow it
        result = _commit(repo, {"PXX_PRECOMMIT_SKIP": "1"})
        assert result.returncode == 0, f"bypass should succeed; stderr: {result.stderr}"

    def test_skip_bypasses_all_checks_on_lint_error(self, tmp_path):
        """Stage a file with ruff violation; with bypass set, commit succeeds."""
        repo = _init_repo(tmp_path)
        _install_hook(repo)

        # Stage a file with lint violation (undefined name)
        bad_py = repo / "bad.py"
        bad_py.write_text("x = undefined_var\n")
        subprocess.run(["git", "add", "bad.py"], cwd=repo, check=True)

        result = _commit(repo, {"PXX_PRECOMMIT_SKIP": "1"})
        assert result.returncode == 0, f"bypass should succeed; stderr: {result.stderr}"


class TestRuffGate:
    """Test ruff linting gate."""

    def test_ruff_fails_on_undefined_name(self, tmp_path):
        """Stage a file with undefined name; hook should block."""
        repo = _init_repo(tmp_path)
        _install_hook(repo)

        bad_py = repo / "bad.py"
        bad_py.write_text("x = undefined_variable\n")
        subprocess.run(["git", "add", "bad.py"], cwd=repo, check=True)

        result = _commit(repo)
        assert result.returncode != 0, "ruff should block commit"
        assert "ruff check FAILED" in result.stderr, (
            f"expected ruff error in stderr: {result.stderr}"
        )

    def test_ruff_succeeds_on_clean_code(self, tmp_path):
        """Stage clean code; hook should pass ruff gate."""
        repo = _init_repo(tmp_path)
        _install_hook(repo)

        clean_py = repo / "clean.py"
        clean_py.write_text("x = 1\n")
        subprocess.run(["git", "add", "clean.py"], cwd=repo, check=True)

        result = _commit(repo)
        # Should pass ruff but may fail pytest if no tests
        assert "ruff check FAILED" not in result.stderr, (
            f"ruff should not fail: {result.stderr}"
        )


class TestPytestGate:
    """Test pytest gate."""

    def test_pytest_fails_on_failing_test(self, tmp_path):
        """Stage a failing test; hook should block."""
        repo = _init_repo(tmp_path)
        _install_hook(repo)

        test_file = repo / "test_fail.py"
        test_file.write_text("def test_one():\n    assert False\n")
        subprocess.run(["git", "add", "test_fail.py"], cwd=repo, check=True)

        result = _commit(repo)
        assert result.returncode != 0, "pytest should block commit"
        assert "pytest FAILED" in result.stderr, (
            f"expected pytest error in stderr: {result.stderr}"
        )

    def test_pytest_succeeds_on_passing_test(self, tmp_path):
        """Stage a passing test; hook should pass pytest gate."""
        repo = _init_repo(tmp_path)
        _install_hook(repo)

        test_file = repo / "test_pass.py"
        test_file.write_text("def test_one():\n    assert True\n")
        subprocess.run(["git", "add", "test_pass.py"], cwd=repo, check=True)

        result = _commit(repo)
        assert "pytest FAILED" not in result.stderr, (
            f"pytest should pass: {result.stderr}"
        )


class TestDiffCap:
    """Test diff cap gate (PXX_DIFF_CAP, PXX_ALLOW_BIG_DIFF)."""

    def test_diff_cap_blocks_large_diff(self, tmp_path):
        """Stage >100 lines; hook should block."""
        repo = _init_repo(tmp_path)
        _install_hook(repo)

        big_file = repo / "big.py"
        # Create 120 lines (more than default 100-line cap)
        big_file.write_text("\n".join(f"x_{i} = {i}" for i in range(120)) + "\n")
        subprocess.run(["git", "add", "big.py"], cwd=repo, check=True)

        result = _commit(repo)
        assert result.returncode != 0, "diff cap should block large commit"
        assert (
            "staged diff is" in result.stderr and "lines (cap 100)" in result.stderr
        ), f"expected diff cap error: {result.stderr}"

    def test_allow_big_diff_env_overrides_cap(self, tmp_path):
        """Stage >100 lines with PXX_ALLOW_BIG_DIFF=1; hook should pass."""
        repo = _init_repo(tmp_path)
        _install_hook(repo)

        big_file = repo / "big.py"
        big_file.write_text("\n".join(f"y_{i} = {i}" for i in range(120)) + "\n")
        subprocess.run(["git", "add", "big.py"], cwd=repo, check=True)

        result = _commit(repo, {"PXX_ALLOW_BIG_DIFF": "1"})
        # Should pass diff cap (though may fail pytest if no tests)
        assert "staged diff is" not in result.stderr, (
            f"PXX_ALLOW_BIG_DIFF should bypass cap: {result.stderr}"
        )

    def test_diff_cap_env_raises_threshold(self, tmp_path):
        """Stage 150 lines with PXX_DIFF_CAP=200; hook should pass."""
        repo = _init_repo(tmp_path)
        _install_hook(repo)

        big_file = repo / "big.py"
        big_file.write_text("\n".join(f"z_{i} = {i}" for i in range(150)) + "\n")
        subprocess.run(["git", "add", "big.py"], cwd=repo, check=True)

        result = _commit(repo, {"PXX_DIFF_CAP": "200"})
        assert "staged diff is" not in result.stderr, (
            f"PXX_DIFF_CAP=200 should allow 150 lines: {result.stderr}"
        )

    def test_diff_cap_respects_default_when_unset(self, tmp_path):
        """Stage exactly 100 lines; hook should pass (at threshold)."""
        repo = _init_repo(tmp_path)
        _install_hook(repo)

        file_100 = repo / "file_100.py"
        # Exactly 100 lines to test boundary
        file_100.write_text("\n".join(f"a_{i} = {i}" for i in range(100)) + "\n")
        subprocess.run(["git", "add", "file_100.py"], cwd=repo, check=True)

        result = _commit(repo)
        assert "staged diff is" not in result.stderr, (
            f"100 lines should not exceed cap of 100: {result.stderr}"
        )


class TestScopeGate:
    """Test scope gate (PXX_SCOPE + pxx-scope-check)."""

    def test_scope_gate_blocks_file_outside_scope(self, tmp_path):
        """Set PXX_SCOPE=tests/, stage file in root; hook should block."""
        repo = _init_repo(tmp_path)
        _install_hook(repo)

        # Create tests/ dir for scope
        (repo / "tests").mkdir()
        (repo / "tests" / "test_in_scope.py").write_text("def test_x():\n    pass\n")

        # Stage a file outside scope (in root)
        outside = repo / "outside.py"
        outside.write_text("x = 1\n")
        subprocess.run(["git", "add", "outside.py"], cwd=repo, check=True)

        result = _commit(repo, {"PXX_SCOPE": "tests/"})
        assert result.returncode != 0, "scope gate should block file outside scope"
        assert "staged files outside PXX_SCOPE" in result.stderr, (
            f"expected scope error: {result.stderr}"
        )

    def test_scope_gate_allows_file_inside_scope(self, tmp_path):
        """Set PXX_SCOPE=tests/, stage file in tests/; hook should pass scope gate."""
        repo = _init_repo(tmp_path)
        _install_hook(repo)

        (repo / "tests").mkdir()
        inside = repo / "tests" / "test_in_scope.py"
        inside.write_text("def test_one():\n    assert True\n")
        subprocess.run(["git", "add", "tests/test_in_scope.py"], cwd=repo, check=True)

        result = _commit(repo, {"PXX_SCOPE": "tests/"})
        assert "staged files outside PXX_SCOPE" not in result.stderr, (
            f"file in scope should not trigger error: {result.stderr}"
        )

    def test_scope_gate_skipped_when_not_set(self, tmp_path):
        """Without PXX_SCOPE set, scope gate should be skipped."""
        repo = _init_repo(tmp_path)
        _install_hook(repo)

        # Stage file anywhere without PXX_SCOPE set
        file_root = repo / "anywhere.py"
        file_root.write_text("y = 1\n")
        subprocess.run(["git", "add", "anywhere.py"], cwd=repo, check=True)

        result = _commit(repo)
        assert "staged files outside PXX_SCOPE" not in result.stderr, (
            f"scope gate should not fire when PXX_SCOPE unset: {result.stderr}"
        )
