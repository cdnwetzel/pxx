"""Tests for pxx.worktree snapshot/delta + NUL status parsing."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from pxx.worktree import untracked_paths, worktree_delta, worktree_snapshot

GIT = shutil.which("git")
needs_git = pytest.mark.skipif(GIT is None, reason="git not available")


def run(coro):
    return asyncio.run(coro)


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run([GIT, *args], cwd=root, check=True, capture_output=True, text=True)
    return proc.stdout.strip()


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    (path / "a.py").write_text("x = 1\n")
    _git(path, "add", "-A")
    _git(path, "-c", "user.name=t", "-c", "user.email=t@e.c", "commit", "-q", "-m", "i")


@needs_git
def test_status_entries_rename_source_not_misparsed(tmp_path: Path) -> None:
    """A rename's source field must never be read as its own status entry."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    _git(repo, "mv", "a.py", "b.py")  # staged rename: R entry + source field
    snap = run(worktree_snapshot(repo))
    assert snap is not None
    # the old path must not appear as a phantom untracked/changed entry
    assert "a.py" not in snap["untracked"]
    assert "b.py" not in snap["untracked"]  # renamed (tracked), not untracked


@needs_git
def test_untracked_paths_exact_for_hard_names(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    names = ["plain.py", "two words.py", "café.py", "sub dir/nested é.py"]
    for name in names:
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x = 1\n")
    paths = run(untracked_paths(repo))
    assert paths is not None
    assert sorted(paths) == sorted(names)


@needs_git
def test_delta_includes_hard_names(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    start = run(worktree_snapshot(repo))
    assert start is not None
    names = ["plain.py", "two words.py", "café.py", "sub dir/nested é.py"]
    for name in names:
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x = 1\n")
    changed, _ = run(worktree_delta(repo, start))
    assert sorted(changed) == sorted(names)
