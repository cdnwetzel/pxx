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


@needs_git
def test_delta_sees_a_dirty_file_restored_to_head(tmp_path: Path) -> None:
    # Codex review (2.9.1 r2): a file dirty at baseline that the run restored
    # to HEAD dropped out of the final numstat and so out of the delta.
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "a.py").write_text("x = 2\n")  # pre-existing dirt
    start = run(worktree_snapshot(repo))
    (repo / "a.py").write_text("x = 1\n")  # the run restores it to HEAD
    changed, lines = run(worktree_delta(repo, start))
    assert changed == ["a.py"] and lines == 2


@needs_git
def test_delta_sees_a_preexisting_untracked_file_deleted(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "notes.txt").write_text("mine\n")
    start = run(worktree_snapshot(repo))
    (repo / "notes.txt").unlink()  # the run deletes it
    changed, _ = run(worktree_delta(repo, start))
    assert changed == ["notes.txt"]


@needs_git
def test_delta_still_excludes_untouched_preexisting_dirt(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "a.py").write_text("x = 2\n")
    (repo / "notes.txt").write_text("mine\n")
    start = run(worktree_snapshot(repo))
    (repo / "new.py").write_text("y = 1\n")  # the run's only change
    changed, lines = run(worktree_delta(repo, start))
    assert changed == ["new.py"] and lines == 1


@needs_git
def test_new_file_patch_never_reads_outside_the_repo(tmp_path: Path) -> None:
    # Grok review (2.9.1 r2): the path was not confined to the repo.
    from pxx.worktree import new_file_patch

    repo = tmp_path / "repo"
    _init_repo(repo)
    (tmp_path / "secret.txt").write_text("TOP SECRET\n")
    assert run(new_file_patch(repo, "../secret.txt")) == ""
    assert run(new_file_patch(repo, str(tmp_path / "secret.txt"))) == ""
    (repo / "d").mkdir()
    assert run(new_file_patch(repo, "d")) == ""  # never diff a directory
    (repo / "ok.txt").write_text("fine\n")
    assert "+fine" in run(new_file_patch(repo, "ok.txt"))


@needs_git
def test_snapshot_sees_a_staged_rename_as_two_real_paths(tmp_path: Path) -> None:
    # agy + Grok reviews (2.9.1 r2): a staged rename was one synthetic
    # "a => b" path in the numstat, which no scope check could use.
    repo = tmp_path / "repo"
    _init_repo(repo)
    start = run(worktree_snapshot(repo))
    _git(repo, "mv", "a.py", "moved.py")
    changed, _ = run(worktree_delta(repo, start))
    assert sorted(changed) == ["a.py", "moved.py"]


@needs_git
def test_delta_sees_new_dangling_and_directory_symlinks(tmp_path: Path) -> None:
    # Codex review (2.9.1 r3): a dangling or directory symlink fingerprinted
    # as None on both sides and vanished from the delta.
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "realdir").mkdir()
    (repo / "realdir" / "f.txt").write_text("x\n")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@e.c", "commit", "-q", "-m", "d")
    start = run(worktree_snapshot(repo))
    (repo / "dangling").symlink_to("does-not-exist")
    (repo / "dirlink").symlink_to("realdir", target_is_directory=True)
    changed, _ = run(worktree_delta(repo, start))
    assert sorted(changed) == ["dangling", "dirlink"]


@needs_git
def test_delta_sees_a_symlink_retargeted(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "link").symlink_to("a.py")
    start = run(worktree_snapshot(repo))
    (repo / "link").unlink()
    (repo / "link").symlink_to("elsewhere")
    changed, _ = run(worktree_delta(repo, start))
    assert changed == ["link"]


@needs_git
def test_a_failed_numstat_is_an_unavailable_snapshot_not_an_empty_one(
    tmp_path: Path, monkeypatch
) -> None:
    # Codex review (2.9.1 r4): a failed `git diff --numstat` read as "no
    # tracked changes".
    import pxx.worktree as wt

    repo = tmp_path / "repo"
    _init_repo(repo)
    real = wt._git_raw

    async def failing(cwd, *args):
        return None if "--numstat" in args else await real(cwd, *args)

    monkeypatch.setattr(wt, "_git_raw", failing)
    assert run(worktree_snapshot(repo)) is None
    assert (
        run(wt.worktree_delta_checked(repo, {"untracked": {}, "numstat": {}, "content": {}}))
        is None
    )


@needs_git
@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_delta_sees_edits_hidden_behind_index_flags(tmp_path: Path, flag: str) -> None:
    # Grok review (2.9.1 r5): git reports a flagged file as clean, so an edit
    # to it was invisible to every measurement.
    repo = tmp_path / "repo"
    _init_repo(repo)
    start = run(worktree_snapshot(repo))
    _git(repo, "update-index", flag, "a.py")
    (repo / "a.py").write_text("x = 'hidden edit'\n")
    assert _git(repo, "status", "--porcelain") == ""  # git itself sees nothing
    changed, _ = run(worktree_delta(repo, start))
    assert changed == ["a.py"]


@needs_git
@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_delta_sees_a_deletion_behind_an_index_flag(tmp_path: Path, flag: str) -> None:
    # Codex review (2.9.1 r6): every missing flagged file was skipped, so a
    # deletion during the run was invisible.
    repo = tmp_path / "repo"
    _init_repo(repo)
    _git(repo, "update-index", flag, "a.py")
    start = run(worktree_snapshot(repo))
    (repo / "a.py").unlink()
    changed, _ = run(worktree_delta(repo, start))
    assert changed == ["a.py"]


@needs_git
def test_a_sparse_skip_worktree_file_absent_throughout_is_not_a_change(tmp_path: Path) -> None:
    # the legitimate case the old "missing = skip" rule protected
    repo = tmp_path / "repo"
    _init_repo(repo)
    _git(repo, "update-index", "--skip-worktree", "a.py")
    (repo / "a.py").unlink()  # absent before the run, as in a sparse checkout
    start = run(worktree_snapshot(repo))
    changed, _ = run(worktree_delta(repo, start))
    assert changed == []
