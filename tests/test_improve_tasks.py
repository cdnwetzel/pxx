"""Tests for pxx.improve.tasks: task-claim state machine + reconciliation (B9.2)."""

from __future__ import annotations

import pytest

from pxx.errors import PxxError
from pxx.improve.tasks import TaskState, TaskStore


def _store(tmp_path, now=1000.0):
    return TaskStore(tmp_path / "state", now=lambda: now)


def test_full_lifecycle(tmp_path):
    store = _store(tmp_path)
    store.enqueue("t1", payload={"kind": "eval"}, worktree="/wt/t1")
    assert store.get("t1").state == str(TaskState.QUEUED)
    claimed = store.claim("t1", owner="daemon-1")
    assert claimed.state == str(TaskState.CLAIMED)
    assert claimed.owner == "daemon-1"
    store.start("t1")
    store.heartbeat("t1")
    store.await_review("t1")
    done = store.complete("t1")
    assert done.state == str(TaskState.DONE)
    # durable across reload
    assert TaskStore(tmp_path / "state").get("t1").state == str(TaskState.DONE)


def test_illegal_transition_raises(tmp_path):
    store = _store(tmp_path)
    store.enqueue("t1")
    with pytest.raises(PxxError, match="illegal task transition"):
        store.complete("t1")  # QUEUED -> DONE is not legal


def test_crash_mid_run_reconciles_to_resumable(tmp_path):
    store = _store(tmp_path, now=1000.0)
    store.enqueue("t1", payload={"kind": "eval"})
    store.claim("t1", owner="daemon-1")
    store.start("t1")
    # owner dies: no heartbeat; clock jumps past the stall window
    stale = TaskStore(tmp_path / "state", now=lambda: 1000.0 + 9999.0)
    reconciled = stale.reconcile()
    assert reconciled == ["t1"]
    task = stale.get("t1")
    assert task.state == str(TaskState.QUEUED)  # resumable, not lost
    assert task.owner == ""
    assert task.payload == {"kind": "eval"}  # payload intact


def test_stall_detection_and_fresh_heartbeat(tmp_path):
    store = _store(tmp_path, now=1000.0)
    store.enqueue("t1")
    store.claim("t1", owner="d")
    store.start("t1")
    fresh = TaskStore(tmp_path / "state", now=lambda: 1000.0 + 10.0)
    assert fresh.stalled() == []
    stale = TaskStore(tmp_path / "state", now=lambda: 1000.0 + 9999.0)
    assert [t.id for t in stale.stalled()] == ["t1"]


def test_reconcile_is_idempotent_no_duplicate_work(tmp_path):
    store = _store(tmp_path, now=1000.0)
    store.enqueue("t1")
    store.claim("t1", owner="d")
    stale = TaskStore(tmp_path / "state", now=lambda: 1000.0 + 9999.0)
    assert stale.reconcile() == ["t1"]
    assert stale.reconcile() == []  # nothing left to reconcile


def test_failed_task_can_requeue(tmp_path):
    store = _store(tmp_path)
    store.enqueue("t1")
    store.claim("t1", owner="d")
    store.start("t1")
    store.fail("t1", "boom")
    assert store.get("t1").state == str(TaskState.FAILED)
    store.requeue("t1")
    assert store.get("t1").state == str(TaskState.QUEUED)
