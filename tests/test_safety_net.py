"""Tests for pxx.safety_net — real git in tmp repos (the net is git I/O)."""

from __future__ import annotations

import asyncio
import subprocess
import time

from pxx.safety_net import tie_safety_net


def git(cwd, *args) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"git {args}: {proc.stderr}"
    return proc.stdout.strip()


def make_repo(path):
    path.mkdir()
    git(path, "init", "-q")
    (path / "a.txt").write_text("hello\n")
    git(path, "add", "-A")
    git(path, "-c", "user.name=t", "-c", "user.email=t@e.c", "commit", "-q", "-m", "i")


def test_clean_tree_ties_tag_only(tmp_path):
    repo = tmp_path / "repo"
    make_repo(repo)
    net = asyncio.run(tie_safety_net(repo, "run-1"))
    assert net is not None
    assert net.tag and net.tag.startswith("pxx-pre/")
    assert net.stash_message is None  # clean tree: no stash
    assert git(repo, "tag", "-l", "pxx-pre/*") == net.tag
    assert git(repo, "rev-parse", net.tag) == git(repo, "rev-parse", "HEAD")
    assert git(repo, "stash", "list") == ""


def test_dirty_tree_stashes_and_tags_and_round_trips(tmp_path):
    repo = tmp_path / "repo"
    make_repo(repo)
    (repo / "a.txt").write_text("modified\n")
    (repo / "new.txt").write_text("untracked\n")

    net = asyncio.run(tie_safety_net(repo, "run-42"))
    assert net is not None and net.tag
    assert net.stash_message and "run-42" in net.stash_message
    assert git(repo, "status", "--porcelain") == ""  # dirt parked
    assert "run-42" in git(repo, "stash", "list")

    # the reviewer's live repro as a round-trip: reset to the tag, pop the
    # stash — the user's pre-session state comes back exactly.
    git(repo, "reset", "--hard", net.tag)
    git(repo, "stash", "pop")
    assert (repo / "a.txt").read_text() == "modified\n"
    assert (repo / "new.txt").read_text() == "untracked\n"


def test_non_git_cwd_is_noop(tmp_path):
    assert asyncio.run(tie_safety_net(tmp_path, "run-1")) is None


def test_tag_collision_suffixes(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    make_repo(repo)
    fixed = time.struct_time((2026, 7, 21, 4, 5, 6, 1, 202, 0))
    monkeypatch.setattr(time, "gmtime", lambda: fixed)
    git(repo, "tag", "pxx-pre/20260721T040506Z", "HEAD")  # squat the base name

    net = asyncio.run(tie_safety_net(repo, "run-1"))
    assert net is not None
    assert net.tag == "pxx-pre/20260721T040506Z-2"
    assert git(repo, "rev-parse", net.tag) == git(repo, "rev-parse", "HEAD")


def test_repo_without_commits_is_noop(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")  # no commits: nothing to tag
    assert asyncio.run(tie_safety_net(repo, "run-1")) is None
