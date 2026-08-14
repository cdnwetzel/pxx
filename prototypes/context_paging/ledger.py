"""Task ledger — the durable task state (host-owned, survives restart).

The task lives HERE, not in a transcript: objective, acceptance command, invariants,
decisions, failed attempts, verification state, and a monotonic revision. Persisted as JSON
with an **atomic tmp-then-replace** write so a crash never leaves a half-written ledger. On
restart the runtime reloads the ledger and resumes — no transcript replay.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .pages import _write_durably


@dataclass
class Ledger:
    """The task's durable state. ``revision`` bumps on every committed mutation."""

    objective: str
    acceptance_cmd: list[str]  # the host-owned test command; the ONLY thing RUN_TEST runs
    target_path: str = ""  # the file under edit — its source is never evicted from the capsule
    invariants: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    failed_attempts: list[str] = field(default_factory=list)
    verified: bool = False  # set only after a host RUN_TEST of acceptance_cmd passes
    revision: int = 0

    # --- persistence (atomic) ---------------------------------------------------------
    @staticmethod
    def _path(state_dir: Path) -> Path:
        return Path(state_dir) / "ledger.json"

    def save(self, state_dir: Path) -> None:
        """Atomic tmp-then-replace + fsync: never observed half-written; survives power loss."""
        path = self._path(state_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        _write_durably(tmp, json.dumps(asdict(self), indent=2, sort_keys=True).encode("utf-8"))
        tmp.replace(path)  # atomic on POSIX

    @classmethod
    def load(cls, state_dir: Path) -> Ledger:
        data = json.loads(cls._path(state_dir).read_text())
        return cls(**data)

    @classmethod
    def exists(cls, state_dir: Path) -> bool:
        return cls._path(state_dir).is_file()

    # --- mutation ---------------------------------------------------------------------
    def bump(self) -> int:
        """Advance the revision. Callers persist within the executor's atomic commit."""
        self.revision += 1
        return self.revision

    def record_failure(self, note: str) -> None:
        self.failed_attempts.append(note)

    def record_decision(self, note: str) -> None:
        self.decisions.append(note)
