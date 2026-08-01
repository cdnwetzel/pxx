"""Tests for pxx.improve.scheduler: daemon, locks, worktree isolation (B9.1)."""

from __future__ import annotations

import fcntl
import shutil
import subprocess

import pytest

from pxx.errors import PxxError
from pxx.improve.scheduler import (
    DAEMON_LOCK,
    candidate_worktree,
    is_paused,
    is_running,
    run_daemon,
    set_paused,
)

GIT = shutil.which("git")
needs_git = pytest.mark.skipif(GIT is None, reason="git not available")


def _cycle_recorder(calls: list):
    def cycle(state_dir):
        calls.append(str(state_dir))

        class Report:
            cycle_id = "cycle-test"

        return Report()

    return cycle


def test_daemon_runs_cycle_on_schedule(tmp_path):
    calls: list = []
    report = run_daemon(
        tmp_path / "state",
        interval_seconds=10,
        clock=lambda: 0.0,
        sleep=lambda s: None,  # never really wait
        max_ticks=3,
        cycle_fn=_cycle_recorder(calls),
    )
    assert report.ticks == 3
    assert report.cycles_run == 3
    assert len(calls) == 3


def test_daemon_refuses_overlap(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    lock = (state / "daemon.lock").open("w")
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(PxxError, match="another improvement daemon"):
            run_daemon(state, sleep=lambda s: None, max_ticks=1, cycle_fn=_cycle_recorder([]))
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


def test_daemon_refuses_when_work_lock_held(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    lock = (state / "work.lock").open("w")
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(PxxError, match="work lock"):
            run_daemon(state, sleep=lambda s: None, max_ticks=1, cycle_fn=_cycle_recorder([]))
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


def test_paused_daemon_idles_cleanly(tmp_path):
    state = tmp_path / "state"
    set_paused(state, True)
    assert is_paused(state)
    calls: list = []
    report = run_daemon(state, sleep=lambda s: None, max_ticks=2, cycle_fn=_cycle_recorder(calls))
    assert report.cycles_run == 0
    assert report.skipped_paused == 2
    assert calls == []
    set_paused(state, False)
    assert not is_paused(state)


@needs_git
def test_candidate_worktree_isolation(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run([GIT, "init", "-q"], cwd=repo, check=True, capture_output=True)
    (repo / "a.py").write_text("x = 1\n")
    subprocess.run([GIT, "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        [GIT, "-c", "user.name=t", "-c", "user.email=t@e.c", "commit", "-q", "-m", "i"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    worktree = candidate_worktree(repo, "cand-1")
    assert worktree == repo / ".pxx" / "worktrees" / "cand-1"
    assert (worktree / "a.py").read_text() == "x = 1\n"
    # write in the candidate worktree — the shared tree is untouched
    (worktree / "a.py").write_text("x = 999\n")
    assert (repo / "a.py").read_text() == "x = 1\n"
    # deterministic: same name -> same worktree
    assert candidate_worktree(repo, "cand-1") == worktree


def test_candidate_worktree_rejects_unsafe_name(tmp_path):
    with pytest.raises(PxxError, match="unsafe"):
        candidate_worktree(tmp_path, "../escape")


# --- is_running: real process liveness via the daemon flock (not the pause flag) --


def test_is_running_false_without_lockfile(tmp_path):
    assert is_running(tmp_path) is False


def test_is_running_false_when_lock_is_free(tmp_path):
    (tmp_path / DAEMON_LOCK).write_text("")  # stale lock file, not held
    assert is_running(tmp_path) is False


def test_is_running_true_when_lock_is_held(tmp_path):
    lock = tmp_path / DAEMON_LOCK
    lock.write_text("")
    handle = lock.open("a")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # simulate a live daemon
        assert is_running(tmp_path) is True
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
    assert is_running(tmp_path) is False  # released -> stopped


def test_is_running_independent_of_pause_flag(tmp_path):
    # A pause flag with no live daemon must NOT read as running.
    set_paused(tmp_path, True)
    assert is_paused(tmp_path) is True
    assert is_running(tmp_path) is False
