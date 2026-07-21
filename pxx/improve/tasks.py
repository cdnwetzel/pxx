"""Phase 19.5: durable task-claim state machine + reconciliation + liveness.

Tasks move QUEUED -> CLAIMED -> RUNNING -> AWAITING_REVIEW -> DONE | FAILED
with a durable record (owner, heartbeat, deterministic worktree). A crashed
owner is detected by a stale heartbeat and reconciled back to QUEUED on
restart — a task is never lost and never duplicated.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from ..errors import PxxError

log = logging.getLogger("pxx.improve.tasks")

TASKS_FILENAME = "tasks.json"

#: A task with no heartbeat for this long is stalled (owner presumed dead).
STALL_SECONDS = 300.0


class TaskState(StrEnum):
    QUEUED = "QUEUED"
    CLAIMED = "CLAIMED"
    RUNNING = "RUNNING"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    DONE = "DONE"
    FAILED = "FAILED"


#: Legal transitions. Anything else is a PxxError (deterministic machine).
_TRANSITIONS: dict[str, frozenset[str]] = {
    "QUEUED": frozenset({"CLAIMED", "FAILED"}),
    "CLAIMED": frozenset({"RUNNING", "QUEUED", "FAILED"}),
    "RUNNING": frozenset({"AWAITING_REVIEW", "FAILED", "QUEUED"}),
    "AWAITING_REVIEW": frozenset({"DONE", "FAILED", "QUEUED"}),
    "DONE": frozenset(),
    "FAILED": frozenset({"QUEUED"}),
}


@dataclass(frozen=True)
class Task:
    """One durable task record."""

    id: str
    state: str
    owner: str = ""
    heartbeat_ts: float = 0.0
    worktree: str = ""  # deterministic per task
    payload: dict[str, Any] | None = None


class TaskStore:
    """Durable task records with claims, heartbeats, and reconciliation."""

    def __init__(self, state_dir: Path | str, *, now=lambda: 0.0) -> None:
        import time as _time

        self._dir = Path(state_dir)
        self._now = now if now is not None else _time.time
        self._tasks = self._load()

    @property
    def _path(self) -> Path:
        return self._dir / TASKS_FILENAME

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(self._path.read_text())
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._tasks, indent=2, sort_keys=True) + "\n")
        tmp.replace(self._path)

    def _get(self, task_id: str) -> dict[str, Any]:
        task = self._tasks.get(task_id)
        if task is None:
            raise PxxError(f"no such task: {task_id!r}")
        return task

    def _transition(self, task_id: str, to: TaskState, **fields: Any) -> Task:
        task = self._get(task_id)
        current = str(task["state"])
        if str(to) not in _TRANSITIONS[current]:
            raise PxxError(f"illegal task transition {current} -> {to} for {task_id!r}")
        task["state"] = str(to)
        task.update(fields)
        self._save()
        return Task(
            id=task_id,
            state=str(to),
            owner=str(task.get("owner", "")),
            heartbeat_ts=float(task.get("heartbeat_ts", 0.0)),
            worktree=str(task.get("worktree", "")),
            payload=task.get("payload"),
        )

    # -- lifecycle -------------------------------------------------------------

    def enqueue(
        self, task_id: str, *, payload: dict[str, Any] | None = None, worktree: str = ""
    ) -> Task:
        if task_id in self._tasks:
            raise PxxError(f"task already exists: {task_id!r}")
        self._tasks[task_id] = {
            "state": str(TaskState.QUEUED),
            "owner": "",
            "heartbeat_ts": 0.0,
            "worktree": worktree,
            "payload": payload or {},
        }
        self._save()
        return self.get(task_id)

    def get(self, task_id: str) -> Task:
        task = self._get(task_id)
        return Task(
            id=task_id,
            state=str(task["state"]),
            owner=str(task.get("owner", "")),
            heartbeat_ts=float(task.get("heartbeat_ts", 0.0)),
            worktree=str(task.get("worktree", "")),
            payload=task.get("payload"),
        )

    def list(self, *, state: TaskState | None = None) -> list[Task]:
        tasks = [self.get(tid) for tid in sorted(self._tasks)]
        if state is not None:
            tasks = [t for t in tasks if t.state == str(state)]
        return tasks

    def claim(self, task_id: str, owner: str) -> Task:
        """Claim a QUEUED task for ``owner`` (one owner at a time)."""
        return self._transition(task_id, TaskState.CLAIMED, owner=owner, heartbeat_ts=self._now())

    def start(self, task_id: str) -> Task:
        return self._transition(task_id, TaskState.RUNNING, heartbeat_ts=self._now())

    def heartbeat(self, task_id: str) -> Task:
        """Liveness signal from the running owner."""
        task = self._get(task_id)
        task["heartbeat_ts"] = self._now()
        self._save()
        return self.get(task_id)

    def await_review(self, task_id: str) -> Task:
        return self._transition(task_id, TaskState.AWAITING_REVIEW)

    def complete(self, task_id: str) -> Task:
        return self._transition(task_id, TaskState.DONE)

    def fail(self, task_id: str, reason: str = "") -> Task:
        return self._transition(
            task_id,
            TaskState.FAILED,
            payload={**self._get(task_id).get("payload", {}), "failure": reason[:200]},
        )

    def requeue(self, task_id: str) -> Task:
        """Back to QUEUED (from CLAIMED/RUNNING/AWAITING_REVIEW/FAILED)."""
        return self._transition(task_id, TaskState.QUEUED, owner="")

    # -- reconciliation ------------------------------------------------------------

    def stalled(self, *, stall_seconds: float = STALL_SECONDS) -> list[Task]:
        """CLAIMED/RUNNING tasks whose heartbeat is stale (owner presumed dead)."""
        now = self._now()
        out = []
        for task in self.list():
            if task.state in ("CLAIMED", "RUNNING"):
                if now - task.heartbeat_ts > stall_seconds:
                    out.append(task)
        return out

    def reconcile(self, *, stall_seconds: float = STALL_SECONDS) -> list[str]:
        """Startup reconciliation: every stalled task is requeued — never
        lost, never duplicated. Returns the reconciled task ids."""
        reconciled: list[str] = []
        for task in self.stalled(stall_seconds=stall_seconds):
            log.warning(
                "reconciling stalled task %s (owner %s, last heartbeat %.0f)",
                task.id,
                task.owner,
                task.heartbeat_ts,
            )
            self.requeue(task.id)
            reconciled.append(task.id)
        return reconciled


__all__ = ["STALL_SECONDS", "Task", "TaskState", "TaskStore"]
