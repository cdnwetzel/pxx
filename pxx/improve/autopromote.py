"""Phase 21: risk classes + evidence-gated auto-promotion.

Risk classes:

- LOW — memory retrieval limits, tighten-only budgets, non-authoritative
  prompt wording (any prompt other than the main system prompt): eligible
  for automatic promotion.
- MEDIUM — the main system prompt, model changes, budget loosening: human
  approval required.
- HIGH — protected paths, permissions, evaluators, release: manual only,
  NEVER auto.

``readiness`` reports whether the platform has earned auto-promotion at all:
>= 50 eval cases, >= 100 real runs, >= 3 human-approved promotions, and 0
unresolved critical evaluator defects. Missing evidence fails closed.

``auto_promote`` refuses unless readiness is green AND the candidate is LOW
risk AND evidence records repeated wins (full corpus + held-out +
adversarial passes). Every auto-promotion writes an append-only promotion
record (reusing :func:`pxx.improve.promotion.write_promotion_record`) with
the candidate's rationale and a rollback command. Default posture: report
what it WOULD do; refuse — the bars are the point.
"""

from __future__ import annotations

import fcntl
import json
import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .candidates import Candidate
from .promotion import (
    RiskClass,
    build_record,
    classify_risk,
    write_promotion_record,
)

log = logging.getLogger("pxx.improve.autopromote")


AUTO_APPROVER = "auto-promote"
ROLLBACK_COMMAND = "pxx agent rollback"


# -- readiness ---------------------------------------------------------------------


BAR_EVAL_CASES = 50
BAR_REAL_RUNS = 100
BAR_HUMAN_APPROVED_PROMOTIONS = 3
BAR_UNRESOLVED_CRITICAL_DEFECTS = 0

_DEFECTS_FILENAME = "evaluator-defects.json"


@dataclass(frozen=True)
class ReadinessCounts:
    """Raw counts feeding the readiness bars. None = evidence missing."""

    eval_cases: int | None
    real_runs: int | None
    human_approved_promotions: int | None
    unresolved_critical_defects: int | None


@dataclass(frozen=True)
class ReadinessReport:
    """Per-bar pass/fail. ``green`` only when EVERY bar passes."""

    counts: ReadinessCounts
    bars: dict[str, bool]

    @property
    def green(self) -> bool:
        return all(self.bars.values())

    @property
    def unmet(self) -> tuple[str, ...]:
        return tuple(name for name, ok in sorted(self.bars.items()) if not ok)


def evaluate_readiness(counts: ReadinessCounts) -> ReadinessReport:
    """Pure bar math. Missing evidence (None) fails the bar — fail-closed."""
    bars = {
        "eval_cases": counts.eval_cases is not None and counts.eval_cases >= BAR_EVAL_CASES,
        "real_runs": counts.real_runs is not None and counts.real_runs >= BAR_REAL_RUNS,
        "human_approved_promotions": counts.human_approved_promotions is not None
        and counts.human_approved_promotions >= BAR_HUMAN_APPROVED_PROMOTIONS,
        "unresolved_critical_defects": counts.unresolved_critical_defects is not None
        and counts.unresolved_critical_defects <= BAR_UNRESOLVED_CRITICAL_DEFECTS,
    }
    return ReadinessReport(counts=counts, bars=bars)


#: Backends that represent genuine agent work. ``mock`` and ``replay`` are test
#: doubles and must never count toward the earned-enablement ``real_runs`` bar.
_REAL_BACKENDS = frozenset({"native", "aider"})


def _int_or_zero(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def counts_as_real_run(run_dir: Path) -> bool:
    """Whether a ``runs/<id>/`` directory counts toward the ``real_runs`` bar.

    A run counts only if it did genuine work (F-1 hardening): (1) a real
    backend — not the ``mock``/``replay`` test doubles; (2) a recorded terminal
    outcome (``outcome.json`` present — a crash before the outcome never
    counts); and (3) evidence the model actually ran — tokens spent OR a diff
    produced. Fail-closed: any unreadable/absent record means "does not count".

    This closes the gap dogfooded on 2026-08-01 (R-014): the bar was a raw
    subdirectory count, so a mock-backend run, a crash, or a zero-work
    connection failure (``MODEL_UNAVAILABLE`` with 0 tokens) all inflated it.
    """
    outcome_path = run_dir / "outcome.json"
    if not outcome_path.is_file():
        return False
    try:
        backend = str(json.loads((run_dir / "manifest.json").read_text()).get("backend", ""))
    except (OSError, ValueError):
        return False
    if backend not in _REAL_BACKENDS:
        return False
    try:
        outcome = json.loads(outcome_path.read_text())
    except (OSError, ValueError):
        return False
    return _int_or_zero(outcome.get("tokens")) > 0 or _int_or_zero(outcome.get("diff_lines")) > 0


def gather_counts(state_dir: Path | str, *, evals_dir: Path | str | None = None) -> ReadinessCounts:
    """Thin I/O edge: read the counts from disk.

    Absent runs/promotions dirs are legitimately zero; a missing or
    malformed defects ledger is MISSING EVIDENCE (None) — we cannot prove
    zero unresolved critical defects, so the bar must fail closed.
    """
    state_dir = Path(state_dir)

    eval_cases: int | None = None
    if evals_dir is not None and Path(evals_dir).is_dir():
        eval_cases = sum(1 for _ in Path(evals_dir).rglob("*.toml"))

    runs_root = state_dir / "runs"
    real_runs = (
        sum(1 for d in runs_root.iterdir() if d.is_dir() and counts_as_real_run(d))
        if runs_root.is_dir()
        else 0
    )

    human_approved = 0
    prom_dir = state_dir / "promotions"
    if prom_dir.is_dir():
        for path in sorted(prom_dir.glob("*.json")):
            try:
                approver = str(json.loads(path.read_text()).get("approver", ""))
            except Exception:
                continue  # unreadable record: not evidence of human approval
            if approver and approver != AUTO_APPROVER:
                human_approved += 1

    defects: int | None = None
    if (state_dir / _DEFECTS_FILENAME).is_file():
        try:
            defects = len(load_defects(state_dir)["unresolved_critical"])
        except Exception:
            defects = None  # fail closed: cannot prove zero unresolved defects

    return ReadinessCounts(
        eval_cases=eval_cases,
        real_runs=real_runs,
        human_approved_promotions=human_approved,
        unresolved_critical_defects=defects,
    )


def readiness(state_dir: Path | str, *, evals_dir: Path | str | None = None) -> ReadinessReport:
    """Readiness of the platform for auto-promotion."""
    return evaluate_readiness(gather_counts(state_dir, evals_dir=evals_dir))


# -- critical-defects ledger ---------------------------------------------------------
#
# The unresolved_critical_defects bar reads _DEFECTS_FILENAME; these are its
# only writers. The ledger is append-through: a resolved defect moves to the
# "resolved" list with its full history — it is never deleted, so the file is
# also the public record of what was found and fixed.


def _defects_path(state_dir: Path | str) -> Path:
    return Path(state_dir) / _DEFECTS_FILENAME


def _utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_defects(state_dir: Path | str) -> dict[str, list[dict[str, Any]]]:
    """The ledger, or an empty one when the file is absent.

    Malformed content raises (a corrupt ledger needs a human, never a
    silent reset or default) — gather_counts fails closed to None. Both
    sections must be lists and every entry must carry a usable id: a
    ledger that decodes but has the wrong shape would otherwise count as
    zero defects and turn the readiness bar green.
    """
    path = _defects_path(state_dir)
    if not path.is_file():
        return {"unresolved_critical": [], "resolved": []}
    try:
        data = json.loads(path.read_text())
    except ValueError as exc:
        raise ValueError(f"{path}: defects ledger is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path}: defects ledger is not a JSON object")
    for section in ("unresolved_critical", "resolved"):
        entries = data.get(section)
        if not isinstance(entries, list):
            raise ValueError(f"{path}: defects ledger {section!r} is missing or not a list")
        for entry in entries:
            ident = entry.get("id") if isinstance(entry, dict) else None
            if not isinstance(ident, str) or not ident:
                raise ValueError(f"{path}: defects ledger {section!r} entry lacks a usable id")
    return data


@contextmanager
def _defects_lock(state_dir: Path | str) -> Iterator[None]:
    """Hold an exclusive flock across a ledger read-modify-write, so the
    CLI and the improve daemon can never interleave (lost entries,
    colliding D-nnn ids)."""
    path = _defects_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(".lock"), "w") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _write_defects(state_dir: Path | str, data: dict[str, list[dict[str, Any]]]) -> None:
    path = _defects_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)


def add_defect(state_dir: Path | str, summary: str, defect_id: str | None = None) -> dict[str, Any]:
    """Record an unresolved critical defect; returns the entry."""
    with _defects_lock(state_dir):
        data = load_defects(state_dir)
        existing = {e["id"] for e in data["unresolved_critical"]} | {
            e["id"] for e in data["resolved"]
        }
        if defect_id is None:
            n = 1
            while f"D-{n:03d}" in existing:
                n += 1
            defect_id = f"D-{n:03d}"
        elif defect_id in existing:
            raise ValueError(f"defect id already exists: {defect_id}")
        entry: dict[str, Any] = {"id": defect_id, "summary": summary, "opened": _utc_stamp()}
        data["unresolved_critical"].append(entry)
        _write_defects(state_dir, data)
    return entry


def resolve_defect(state_dir: Path | str, defect_id: str, note: str = "") -> dict[str, Any]:
    """Move a defect to the resolved list; raises KeyError when unknown."""
    with _defects_lock(state_dir):
        data = load_defects(state_dir)
        for i, entry in enumerate(data["unresolved_critical"]):
            if entry["id"] == defect_id:
                resolved = {**entry, "resolved": _utc_stamp(), "note": note}
                del data["unresolved_critical"][i]
                data["resolved"].append(resolved)
                _write_defects(state_dir, data)
                return resolved
    raise KeyError(f"no unresolved defect with id {defect_id!r}")


# -- auto-promotion ------------------------------------------------------------------


@dataclass(frozen=True)
class Evidence:
    """Recorded wins backing an auto-promotion. All four passes required."""

    full_pass: bool = False
    held_out_pass: bool = False
    adversarial_pass: bool = False
    canary_pass: bool = False
    eval_ids: tuple[str, ...] = ()
    gates: dict[str, bool] | None = None


def _coerce_evidence(evidence: Any) -> Evidence:
    if isinstance(evidence, Evidence):
        return evidence
    if isinstance(evidence, Mapping):
        return Evidence(
            full_pass=bool(evidence.get("full_pass", False)),
            held_out_pass=bool(evidence.get("held_out_pass", False)),
            adversarial_pass=bool(evidence.get("adversarial_pass", False)),
            canary_pass=bool(evidence.get("canary_pass", False)),
            eval_ids=tuple(str(e) for e in evidence.get("eval_ids", ())),
            gates=dict(evidence.get("gates") or {}),
        )
    return Evidence(
        full_pass=bool(getattr(evidence, "full_pass", False)),
        held_out_pass=bool(getattr(evidence, "held_out_pass", False)),
        adversarial_pass=bool(getattr(evidence, "adversarial_pass", False)),
        canary_pass=bool(getattr(evidence, "canary_pass", False)),
        eval_ids=tuple(str(e) for e in getattr(evidence, "eval_ids", ())),
        gates=dict(getattr(evidence, "gates", None) or {}),
    )


@dataclass(frozen=True)
class AutoPromoteVerdict:
    """The decision. ``reasons`` lists every refusal reason (empty when
    promoted); ``rationale`` echoes the candidate's rationale."""

    promoted: bool
    risk: str
    reasons: tuple[str, ...]
    rationale: str
    rollback_command: str = ROLLBACK_COMMAND
    record_path: str | None = None
    would_promote: bool = False  # all bars green but commit=False (report posture)


def auto_promote(
    candidate: Candidate,
    evidence: Any,
    *,
    readiness_report: ReadinessReport,
    state_dir: Path | str,
    now: datetime | None = None,
    commit: bool = True,
) -> AutoPromoteVerdict:
    """Evidence-gated auto-promotion. Refuses unless readiness is green AND
    risk is LOW AND repeated wins (full + held-out + adversarial + canary)
    are on record. On promotion (``commit=True``), writes an append-only
    promotion record carrying the rollback command; with ``commit=False``
    it reports ``would_promote`` and writes nothing (default posture)."""
    ev = _coerce_evidence(evidence)
    risk = classify_risk(candidate)

    reasons: list[str] = []
    if risk is not RiskClass.LOW:
        gate = "human" if risk is RiskClass.MEDIUM else "manual (never auto)"
        reasons.append(f"risk class {risk}: requires {gate} promotion")
    for bar in readiness_report.unmet:
        reasons.append(f"readiness bar unmet: {bar}")
    if not ev.full_pass:
        reasons.append("no full-corpus win recorded in evidence")
    if not ev.held_out_pass:
        reasons.append("no held-out win recorded in evidence")
    if not ev.adversarial_pass:
        reasons.append("no adversarial pass recorded in evidence")
    if not ev.canary_pass:
        reasons.append("no green canary window recorded in evidence")

    if reasons:
        # Default posture: report what it would do; refuse. Bars are the point.
        return AutoPromoteVerdict(
            promoted=False,
            risk=str(risk),
            reasons=tuple(reasons),
            rationale=candidate.rationale,
        )

    if not commit:
        return AutoPromoteVerdict(
            promoted=False,
            risk=str(risk),
            reasons=(),
            rationale=candidate.rationale,
            would_promote=True,
        )

    record = build_record(
        f"auto-{candidate.id}",
        baseline_id="stable",
        candidate_id=candidate.id,
        eval_ids=ev.eval_ids,
        gates=ev.gates or {},
        approver=AUTO_APPROVER,
        rollback_target=ROLLBACK_COMMAND,
        now=now,
    )
    path = write_promotion_record(record, state_dir)
    log.info("auto-promoted %s (record %s)", candidate.id, path)
    return AutoPromoteVerdict(
        promoted=True,
        risk=str(risk),
        reasons=(),
        rationale=candidate.rationale,
        record_path=str(path),
    )


# -- post-promotion monitoring + auto-rollback (Phase 21.3) --------------------------


@dataclass(frozen=True)
class MonitoringVerdict:
    """The outcome of one post-promotion monitoring window."""

    rolled_back: bool
    restored: str | None  # the stable version restored by the rollback
    tripped: tuple[str, ...] = ()
    reason: str = ""


def monitor_promotion(
    manager: Any,
    signals: Any,
) -> MonitoringVerdict:
    """Monitor the CURRENT stable against the circuit breakers (B7.2).

    Any tripped breaker = a post-promotion regression: AUTO-ROLLBACK to the
    prior stable (B7.3) with a recorded reason. A healthy window does
    nothing. ``manager`` is a ChannelManager; ``signals`` a
    CandidateRunSignals for the new stable's window.
    """
    from .channels import tripped_breakers

    tripped = tripped_breakers(signals)
    if not tripped:
        return MonitoringVerdict(rolled_back=False, restored=None)
    reason = "auto-rollback: post-promotion regression: " + ", ".join(str(b) for b in tripped)
    restored = manager.rollback()
    manager._record(  # metadata-only, alongside the rollback event
        "auto-rollback",
        "stable",
        restored or "",
        detail=reason,
    )
    manager._save()
    log.warning("%s (restored %s)", reason, restored)
    return MonitoringVerdict(
        rolled_back=True,
        restored=restored,
        tripped=tuple(str(b) for b in tripped),
        reason=reason,
    )
