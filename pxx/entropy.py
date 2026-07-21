"""Phase 20.5: entropy control — golden principles, quality grades, GC.

Agents reproduce the patterns already in a codebase — including bad ones.
Entropy control applies mechanical counter-pressure on three axes:

1. **Golden principles**: declarative forbidden/required patterns enforced
   over the source tree (the top rung of the rule-promotion ladder: a
   repeated lesson becomes a deterministic lint, not a bigger prompt).
2. **Quality grades**: per-layer memory health letters (A through F) so weak
   domains are visible instead of silently rotting.
3. **Garbage collection**: deterministic pruning of expired, low-utility,
   and stale entries — same store in, same report out, every time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .memory.store import MemoryStore

# --- golden principles ----------------------------------------------------------


@dataclass(frozen=True)
class GoldenPrinciple:
    """A declarative, mechanically enforced rule over the source tree."""

    name: str
    glob: str  # file glob, e.g. "pxx/**/*.py"
    forbidden: str  # regex that must NOT match
    message: str
    exclude: tuple[str, ...] = ()  # repo-relative paths exempt by design


@dataclass(frozen=True)
class Violation:
    principle: str
    path: str
    line: int
    message: str


GOLDEN_PRINCIPLES: tuple[GoldenPrinciple, ...] = (
    GoldenPrinciple(
        name="no-print-outside-cli",
        glob="pxx/**/*.py",
        forbidden=r"\bprint\(",
        message="no print() outside cli.py/doctor.py (use logging.getLogger('pxx'))",
        exclude=("pxx/cli.py", "pxx/doctor.py", "pxx/entropy.py"),
    ),
    GoldenPrinciple(
        name="no-os-environ-outside-config",
        glob="pxx/*.py",
        forbidden=r"\bos\.environ",
        message="environment reads in core modules belong in config.py "
        "(edge modules — cli/server/backends/tools/eval — may touch env at the edge)",
        exclude=(
            "pxx/cli.py",
            "pxx/doctor.py",
            "pxx/config.py",
            "pxx/entropy.py",
            "pxx/server.py",
        ),
    ),
    GoldenPrinciple(
        name="no-fabricated-cost",
        glob="pxx/**/*.py",
        forbidden=r"cost_usd=0\.0[,)]",
        message="reported cost is None when unpriced — never a fabricated 0.0 "
        "(a budget accumulator starting at 0 is fine)",
    ),
)


def run_golden_principles(
    root: Path | str, principles: tuple[GoldenPrinciple, ...] = GOLDEN_PRINCIPLES
) -> list[Violation]:
    """Check every golden principle against the tree. Deterministic."""
    root = Path(root)
    violations: list[Violation] = []
    for principle in principles:
        pattern = re.compile(principle.forbidden)
        for path in sorted(root.glob(principle.glob)):
            rel = path.relative_to(root).as_posix()
            if not path.is_file() or rel in principle.exclude:
                continue
            try:
                text = path.read_text(errors="replace")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), start=1):
                if pattern.search(line):
                    violations.append(
                        Violation(
                            principle=principle.name,
                            path=rel,
                            line=i,
                            message=principle.message,
                        )
                    )
    return violations


# --- quality grades -------------------------------------------------------------


def quality_grades(store: MemoryStore) -> dict[str, str]:
    """A-to-F health letter per knowledge layer (active, quarantine ratio,
    mean measured utility)."""
    from .memory.store import LAYER_TTL_DAYS

    db = store._db  # trusted internal read (this module is part of the plane)
    grades: dict[str, str] = {}
    for layer in LAYER_TTL_DAYS:
        row = db.execute(
            "SELECT COUNT(*) AS n,"
            " COALESCE(SUM(quarantined), 0) AS q,"
            " COALESCE(AVG(observed_utility), 0.5) AS u"
            " FROM observations WHERE layer = ? AND archived = 0",
            (layer,),
        ).fetchone()
        n = int(row["n"])
        if n == 0:
            grades[layer] = "—"
            continue
        quarantine_ratio = float(row["q"]) / n
        utility = float(row["u"])
        score = utility * (1.0 - quarantine_ratio)
        grades[layer] = (
            "A"
            if score >= 0.8
            else "B"
            if score >= 0.65
            else "C"
            if score >= 0.5
            else "D"
            if score >= 0.35
            else "F"
        )
    return grades


# --- garbage collection ---------------------------------------------------------


@dataclass(frozen=True)
class GCReport:
    """What one deterministic GC pass did."""

    archived_expired: int
    pruned_low_utility: int
    auto_quarantined: int
    pruned_ids: tuple[int, ...] = field(default_factory=tuple)


#: Utility below this (with recurrence evidence) is provably unhelpful.
_LOW_UTILITY = 0.2


def run_gc(store: MemoryStore, *, now: float | None = None) -> GCReport:
    """One deterministic GC pass: archive expired entries, prune measured
    low-utility episodic entries, auto-quarantine contaminated ones.

    Deterministic: the same store state always yields the same report.
    """
    archived = store.archive_expired(now=now)
    rows = store._db.execute(
        "SELECT id FROM observations WHERE archived = 0 AND quarantined = 0"
        " AND layer = 'episodic' AND seen_count >= 2 AND observed_utility < ?"
        " ORDER BY id",
        (_LOW_UTILITY,),
    ).fetchall()
    pruned_ids = tuple(int(r["id"]) for r in rows)
    for oid in pruned_ids:
        store._db.execute("UPDATE observations SET archived = 1 WHERE id = ?", (oid,))
    store._db.commit()
    quarantined = store.auto_quarantine()
    return GCReport(
        archived_expired=archived,
        pruned_low_utility=len(pruned_ids),
        auto_quarantined=quarantined,
        pruned_ids=pruned_ids,
    )


__all__ = [
    "GOLDEN_PRINCIPLES",
    "GCReport",
    "GoldenPrinciple",
    "Violation",
    "quality_grades",
    "run_gc",
    "run_golden_principles",
]
