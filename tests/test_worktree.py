"""Tests for pxx.worktree snapshot/delta + NUL status parsing."""

from __future__ import annotations

import asyncio
import os
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


@needs_git
@pytest.mark.parametrize(
    "change",
    [
        ("config", ["config", "filter.evil.clean", "cat"]),
        ("info/attributes", None),
        ("refs/replace", None),
        ("attributes/in-tree", "root"),
        ("attributes/in-tree", "nested"),
        ("attributes/in-tree", "ignored"),
    ],
)
def test_git_control_state_sees_changes_git_status_hides(tmp_path: Path, change) -> None:
    # Grok review (2.9.1 r7): filter drivers, attributes and replace refs
    # change what git reports, and none of them shows in `git status`.
    from pxx.worktree import git_control_changed, git_control_state

    repo = tmp_path / "repo"
    _init_repo(repo)
    start = asyncio.run(git_control_state(repo))
    assert start and git_control_changed(start, asyncio.run(git_control_state(repo))) == []
    name, args = change
    if isinstance(args, list):
        subprocess.run([GIT, *args], cwd=repo, check=True)
    elif name == "attributes/in-tree":
        # Grok review (2.9.1 r8, P1): `* -diff` makes the review diff a
        # binary stub and zeroes the diff budget -- reachable via write_file.
        target = repo / (".gitattributes" if args == "root" else "sub/.gitattributes")
        target.parent.mkdir(exist_ok=True)
        if args == "ignored":  # the run hides its own attributes file (Grok r9)
            (repo / "sub" / ".gitignore").write_text(".gitattributes\n")
        target.write_text("* -diff\n")
    elif name == "info/attributes":
        (repo / ".git" / "info").mkdir(exist_ok=True)
        (repo / ".git" / "info" / "attributes").write_text("* filter=evil\n")
    else:
        head = subprocess.run(
            [GIT, "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
        ).stdout.strip()
        tree = subprocess.run(
            [GIT, "rev-parse", "HEAD^{tree}"], cwd=repo, check=True, capture_output=True, text=True
        ).stdout.strip()
        other = subprocess.run(
            [GIT, "-c", "user.name=t", "-c", "user.email=t@e.c", "commit-tree", tree, "-m", "x"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        subprocess.run([GIT, "replace", head, other], cwd=repo, check=True)
    now = asyncio.run(git_control_state(repo))
    assert any(key.endswith(name) for key in git_control_changed(start, now))
    snapshot_start = {**asyncio.run(worktree_snapshot(repo)), "control": start}
    from pxx.worktree import worktree_delta_checked

    assert asyncio.run(worktree_delta_checked(repo, snapshot_start)) is None  # fail closed


@needs_git
@pytest.mark.parametrize("via", ["include", "attributesFile"])
def test_git_control_state_follows_files_the_config_points_at(tmp_path: Path, via: str) -> None:
    # An include.path target (or a core.attributesFile target) is not one of
    # the git directory's own files: editing it changes git's behaviour with
    # every fingerprinted file unchanged.
    from pxx.worktree import git_control_changed, git_control_state

    repo = tmp_path / "repo"
    _init_repo(repo)
    target = tmp_path / "pointed-at"
    target.write_text("[core]\n\tquotePath = true\n" if via == "include" else "")
    key = "include.path" if via == "include" else "core.attributesFile"
    _git(repo, "config", key, str(target))
    start = run(git_control_state(repo))
    target.write_text('[filter "x"]\n\tclean = cat\n' if via == "include" else "* filter=x\n")
    changed = git_control_changed(start, run(git_control_state(repo)))
    assert changed == (["config/effective"] if via == "include" else ["global/attributes"])


@needs_git
@pytest.mark.parametrize(
    ("name", "link"),
    [
        ("plain", "target"),
        ("nl\nname", "target"),
        ('quo"te', "target"),
        ("back\\slash", "target"),
        ("tab\tname", "target"),
        ("wörk", "target"),
        ("multi", "line\none"),
        ("trailing", "dir/\n"),
    ],
)
def test_a_synthesized_symlink_patch_applies_and_round_trips(
    tmp_path: Path, name: str, link: str
) -> None:
    # Codex review (2.9.1 r8, P2): the synthesized header did not quote
    # names, so a newline in a name made the recorded patch unappliable.
    from pxx.worktree import new_file_patch

    src = tmp_path / "src"
    _init_repo(src)
    (src / name).symlink_to(link)
    patch = run(new_file_patch(src, name))
    assert patch
    dst = tmp_path / "dst"
    _init_repo(dst)
    proc = subprocess.run(
        [GIT, "apply", "-"], cwd=dst, input=patch.encode(), capture_output=True, check=False
    )
    assert proc.returncode == 0, proc.stderr
    assert (dst / name).is_symlink() and os.readlink(dst / name) == link


@needs_git
def test_no_internal_diff_runs_a_textconv_or_external_diff_driver(tmp_path: Path) -> None:
    # Codex review (2.9.1 r8, P1): the session's evidence diff still ran a
    # textconv driver, which can execute and rewrite what is saved. Every diff
    # pxx produces for itself must be git's own patch text.
    import types

    import pxx.loop as loop_mod
    from pxx.session import Session
    from pxx.worktree import new_file_patch

    repo = tmp_path / "repo"
    _init_repo(repo)
    pre = _git(repo, "rev-parse", "HEAD")
    marker = tmp_path / "driver-ran"
    _git(repo, "config", "diff.spy.textconv", f"touch {marker} && cat")
    _git(repo, "config", "diff.external", f"sh -c 'touch {marker}'")
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "attributes").write_text("* diff=spy\n")
    (repo / "a.py").write_text("x = 2\n")
    (repo / "new.py").write_text("y = 1\n")

    run(worktree_snapshot(repo))
    run(loop_mod._diff_since(repo, pre, {}))
    run(loop_mod._record_diff(repo, pre, {}))
    run(new_file_patch(repo, "new.py"))
    run(Session._collect_diff(types.SimpleNamespace(cwd=repo)))
    assert not marker.exists()
    subprocess.run([GIT, "diff"], cwd=repo, capture_output=True, check=False)
    assert marker.exists()  # the planted driver is live: the check above is real
