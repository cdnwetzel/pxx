"""Phase 10.75: checkpoints + resume-from-checkpoint.

A checkpoint snapshots a run's recorded trajectory (its run dir) so the run
can be resumed after an interruption. Resume replays the recorded tool
calls through the SAME broker/gates (B3.4's ReplayBackend) to restore the
tree, then returns the recorded terminal outcome — deterministic, so the
resumed run lands on the same outcome as the original. This is the
substrate Phase 19.5's restart-recovery builds on.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from .backends.replay import ReplayBackend
from .config import Settings
from .errors import BackendError
from .outcome import RunOutcome

log = logging.getLogger("pxx.resume")

CHECKPOINT_FILENAME = "checkpoint.json"


@dataclass(frozen=True)
class Checkpoint:
    """A durable pause point for one run."""

    run_id: str
    events_count: int
    ts: float
    path: str


def write_checkpoint(state_dir: Path | str, run_id: str) -> Checkpoint:
    """Snapshot a run's trajectory as a resume point (metadata-only).

    Fail-closed: a run dir without recorded events cannot be checkpointed.
    """
    state_dir = Path(state_dir)
    run_dir = state_dir / "runs" / run_id
    events_path = run_dir / "events.jsonl"
    if not events_path.is_file():
        raise BackendError(
            f"checkpoint: no recorded events for run {run_id!r} "
            "(nothing to resume from — fail-closed)"
        )
    events_count = sum(1 for line in events_path.read_text().splitlines() if line.strip())
    checkpoint = Checkpoint(
        run_id=run_id,
        events_count=events_count,
        ts=time.time(),
        path=str(run_dir / CHECKPOINT_FILENAME),
    )
    (run_dir / CHECKPOINT_FILENAME).write_text(
        json.dumps(
            {
                "run_id": checkpoint.run_id,
                "events_count": checkpoint.events_count,
                "ts": checkpoint.ts,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    return checkpoint


async def resume_run(
    state_dir: Path | str,
    run_id: str,
    settings: Settings,
    *,
    cwd: Path | None = None,
    bus=None,
) -> RunOutcome:
    """Resume a run from its checkpoint: replay the recorded trajectory
    through the same broker/gates and return the recorded terminal outcome.

    Deterministic: resuming twice yields the same outcome (B3.4's
    replay-honors-gates guarantee applies — an out-of-scope recorded call
    is denied on resume too).
    """
    from .session import Session

    state_dir = Path(state_dir)
    run_dir = state_dir / "runs" / run_id
    if not (run_dir / CHECKPOINT_FILENAME).is_file():
        raise BackendError(
            f"resume: no checkpoint for run {run_id!r} (write one with "
            "write_checkpoint first — fail-closed)"
        )
    backend = ReplayBackend(run_dir)
    session = Session(settings, backend, cwd=cwd or Path.cwd(), bus=bus)
    if bus is not None:
        await bus.emit("resumed", {"run_id": run_id})
    return await session.run(f"resume {run_id}")


async def checkpoint_now(
    state_dir: Path | str,
    run_id: str,
    *,
    bus=None,
    session_id: str = "",
) -> Checkpoint:
    """Write a checkpoint and emit the CheckpointCreated event (B10 vocab)
    when a bus is provided."""
    checkpoint = write_checkpoint(state_dir, run_id)
    if bus is not None:
        await bus.emit(
            "checkpoint_created",
            {
                "run_id": checkpoint.run_id,
                "events_count": checkpoint.events_count,
                "path": checkpoint.path,
            },
            session_id=session_id,
        )
        await bus.emit(
            "run_paused",
            {"run_id": checkpoint.run_id},
            session_id=session_id,
        )
    return checkpoint


__all__ = ["Checkpoint", "checkpoint_now", "resume_run", "write_checkpoint"]
