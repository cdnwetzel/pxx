"""Phase 19: the scheduler/daemon that drives the improvement cycle.

Runs :func:`pxx.improve.cycle.run_cycle` on an interval, honoring THREE
non-overlap guarantees: the daemon flock (never two daemons), the cycle
flock (never two cycles — enforced inside run_cycle), and a repo/GPU work
lock (never overlapping heavy work). Each candidate is evaluated in its OWN
deterministic git worktree, never the shared tree.

Operator control is a durable control file (``daemon-control.json``):
paused daemons idle cleanly at tick boundaries — no half-run corruption.
Clock and sleep are injected for deterministic tests.
"""

from __future__ import annotations

import fcntl
import json
import logging
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..errors import PxxError
from .cycle import run_cycle

log = logging.getLogger("pxx.improve.scheduler")

CONTROL_FILENAME = "daemon-control.json"
DAEMON_LOCK = "daemon.lock"
WORK_LOCK = "work.lock"
STATUS_FILENAME = "daemon-status.json"


@dataclass(frozen=True)
class DaemonReport:
    """What the daemon did over its ticks."""

    ticks: int
    cycles_run: int
    skipped_paused: int
    stopped_reason: str


def _read_control(state_dir: Path) -> dict[str, Any]:
    try:
        data = json.loads((state_dir / CONTROL_FILENAME).read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def is_paused(state_dir: Path | str) -> bool:
    """Whether the daemon is paused (durable operator control)."""
    return bool(_read_control(Path(state_dir)).get("paused"))


def set_paused(state_dir: Path | str, paused: bool) -> None:
    """Pause/resume the daemon at the next tick boundary (clean halt)."""
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    control = _read_control(state_dir)
    control["paused"] = bool(paused)
    tmp = (state_dir / CONTROL_FILENAME).with_suffix(".tmp")
    tmp.write_text(json.dumps(control, indent=2, sort_keys=True) + "\n")
    tmp.replace(state_dir / CONTROL_FILENAME)


def candidate_worktree(root: Path | str, name: str) -> Path:
    """A deterministic isolated worktree for one candidate/task.

    ``<root>/.pxx/worktrees/<name>`` — a real ``git worktree add --detach``
    when possible, else a plain copy (``.git`` excluded). The shared tree is
    never written. Refuses a name that isn't a single safe path segment.
    """
    root = Path(root)
    if not name or "/" in name or name.startswith("."):
        raise PxxError(f"unsafe worktree name: {name!r}")
    dest = root / ".pxx" / "worktrees" / name
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    if (root / ".git").exists() and shutil.which("git"):
        try:
            subprocess.run(
                ["git", "-C", str(root), "worktree", "add", "--detach", str(dest)],
                check=True,
                capture_output=True,
                text=True,
            )
            return dest
        except (OSError, subprocess.CalledProcessError):
            log.warning("git worktree add failed; copying instead", exc_info=True)
    shutil.copytree(root, dest, ignore=shutil.ignore_patterns(".git", ".pxx"))
    return dest


def _write_status(state_dir: Path, **fields: Any) -> None:
    status = _read_control(state_dir)
    status.update(fields)
    tmp = (state_dir / STATUS_FILENAME).with_suffix(".tmp")
    tmp.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n")
    tmp.replace(state_dir / STATUS_FILENAME)


def run_daemon(
    state_dir: Path | str,
    *,
    interval_seconds: float = 3600.0,
    clock: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
    max_ticks: int | None = None,
    cycle_fn: Callable[[Path], Any] = run_cycle,
) -> DaemonReport:
    """Drive the improvement cycle on a schedule.

    Refuses to overlap (daemon flock + repo/GPU work lock); honors the
    durable pause control at every tick boundary. ``max_ticks`` bounds the
    loop (tests and `--once`); ``clock``/``sleep`` are injectable so tests
    never really wait.
    """
    import time as _time

    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    now = clock or _time.monotonic
    do_sleep = sleep or _time.sleep

    daemon_lock = (state_dir / DAEMON_LOCK).open("w")
    try:
        fcntl.flock(daemon_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        raise PxxError("another improvement daemon is already running") from exc

    work_lock = (state_dir / WORK_LOCK).open("w")
    try:
        fcntl.flock(work_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        raise PxxError("the repo/GPU work lock is held by another process") from exc

    ticks = cycles = skipped = 0
    stopped = "max_ticks"
    try:
        _write_status(state_dir, state="running")
        while max_ticks is None or ticks < max_ticks:
            if ticks:
                do_sleep(interval_seconds)  # between ticks, never after the last
            ticks += 1
            if is_paused(state_dir):
                skipped += 1
                _write_status(state_dir, state="paused", last_tick=now())
                continue
            try:
                report = cycle_fn(state_dir)
                cycles += 1
                _write_status(
                    state_dir,
                    state="running",
                    last_tick=now(),
                    last_cycle=str(getattr(report, "cycle_id", "")),
                )
            except PxxError as exc:
                # the cycle lock is held: skip this tick, never overlap
                log.warning("cycle skipped: %s", exc)
                _write_status(state_dir, state="running", last_tick=now(), last_skip=str(exc)[:200])
        if max_ticks is None:
            stopped = "unbounded"
    finally:
        _write_status(state_dir, state="stopped")
        fcntl.flock(work_lock.fileno(), fcntl.LOCK_UN)
        fcntl.flock(daemon_lock.fileno(), fcntl.LOCK_UN)
    return DaemonReport(
        ticks=ticks,
        cycles_run=cycles,
        skipped_paused=skipped,
        stopped_reason=stopped,
    )


__all__ = [
    "DaemonReport",
    "candidate_worktree",
    "is_paused",
    "run_daemon",
    "set_paused",
]
