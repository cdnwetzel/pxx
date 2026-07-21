"""Phase 21: the REAL evidence producer for auto-promotion.

Auto-promotion's bars (full corpus / held-out / adversarial / canary wins)
are COMPUTED from records — never accepted as caller-supplied booleans.
A missing record is a FALSE bar (fail closed): the exact anti-pattern M0's
F1 fixed for eval reports, applied here. This module also encodes the
roadmap's "ten mandatory items" precondition gate (B8.4): auto-promotion is
globally disabled until every item is verifiably present.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..eval.cases import Family, Partition, load_cases
from .channels import CANARY_ADVANCE_RUNS, Channel, ChannelManager

log = logging.getLogger("pxx.improve.evidence")

_TIERS = ("micro", "regression", "adversarial")


@dataclass(frozen=True)
class ComputedEvidence:
    """Evidence bars computed from records. Each bar is False until a real
    record proves it."""

    full_pass: bool
    held_out_pass: bool
    adversarial_pass: bool
    canary_pass: bool
    eval_ids: tuple[str, ...] = ()
    gates: dict[str, bool] = field(default_factory=dict)
    details: dict[str, str] = field(default_factory=dict)


def _load_evaluation(state_dir: Path, candidate_id: str) -> dict[str, Any]:
    path = state_dir / "candidates" / candidate_id / "evaluation.json"
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _adversarial_ids(corpus_root: Path) -> set[str]:
    ids: set[str] = set()
    for tier in _TIERS:
        tier_dir = corpus_root / tier
        if tier_dir.is_dir():
            for case in load_cases(tier_dir):
                if case.family is Family.SAFETY or case.tier.value == "adversarial":
                    ids.add(case.id)
    return ids


def compute_evidence(
    candidate_id: str,
    state_dir: Path | str,
    *,
    corpus_root: Path | str,
    channels: ChannelManager | None = None,
) -> ComputedEvidence:
    """Compute the auto-promotion evidence bars from records.

    - ``full_pass``: every case in the candidate's evaluation record passed
      on the candidate arm.
    - ``held_out_pass``: the evaluation record is a held-out verdict AND the
      promotion verdict promoted (held-out + multi-metric, B6).
    - ``adversarial_pass``: every safety/adversarial case in the corpus
      passed on the candidate arm.
    - ``canary_pass``: the canary ledger holds >= CANARY_ADVANCE_RUNS green
      outcomes for the candidate's agent id (B7). No ledger -> False.
    """
    state_dir = Path(state_dir)
    corpus_root = Path(corpus_root)
    evaluation = _load_evaluation(state_dir, candidate_id)
    details: dict[str, str] = {}

    candidate_verdicts = evaluation.get("candidate_verdicts")
    if isinstance(candidate_verdicts, dict) and candidate_verdicts:
        full_pass = all(bool(v) for v in candidate_verdicts.values())
        details["full_pass"] = (
            f"{sum(bool(v) for v in candidate_verdicts.values())}/"
            f"{len(candidate_verdicts)} cases passed on candidate arm"
        )
    else:
        full_pass = False
        details["full_pass"] = "no evaluation record (missing evidence)"

    held_out_pass = bool(evaluation.get("partition") == "held-out" and evaluation.get("promoted"))
    details["held_out_pass"] = (
        "held-out verdict promoted" if held_out_pass else "no promoted held-out verdict on record"
    )

    adversarial_ids = _adversarial_ids(corpus_root)
    if adversarial_ids and isinstance(candidate_verdicts, dict):
        adversarial_pass = all(bool(candidate_verdicts.get(cid)) for cid in adversarial_ids)
        details["adversarial_pass"] = (
            f"{sum(bool(candidate_verdicts.get(cid)) for cid in adversarial_ids)}/"
            f"{len(adversarial_ids)} safety cases passed"
        )
    else:
        adversarial_pass = False
        details["adversarial_pass"] = "no safety-case evidence on record"

    canary_pass = False
    if channels is not None:
        outcomes = channels._state.get("canary_outcomes", [])  # trusted read
        mine = [
            o
            for o in outcomes
            if o.get("agent_version_id") in (candidate_id,)
            or o.get("agent_version_id") == channels.current(Channel.CANARY)
        ]
        green = sum(1 for o in mine if o.get("code") == "COMPLETED")
        canary_pass = len(mine) >= CANARY_ADVANCE_RUNS and green == len(mine) and len(mine) > 0
        if not mine:
            details["canary_pass"] = "no canary outcomes on record (missing evidence)"
        else:
            details["canary_pass"] = f"{green}/{len(mine)} canary runs green"
    else:
        details["canary_pass"] = "no canary ledger (missing evidence)"

    gates = evaluation.get("gates")
    return ComputedEvidence(
        full_pass=full_pass,
        held_out_pass=held_out_pass,
        adversarial_pass=adversarial_pass,
        canary_pass=canary_pass,
        eval_ids=tuple(str(e) for e in evaluation.get("eval_ids", ())),
        gates={str(k): bool(v) for k, v in gates.items()} if isinstance(gates, dict) else {},
        details=details,
    )


# --- the "ten mandatory items" precondition gate (B8.4) ---------------------------


@dataclass(frozen=True)
class Precondition:
    """One mandatory platform capability, verified by execution."""

    name: str
    ok: bool
    detail: str


def check_preconditions(root: Path | str, state_dir: Path | str) -> tuple[Precondition, ...]:
    """Verify the roadmap's mandatory items exist before ANY auto-promotion.

    Every item is COMPUTED from the tree/modules/corpus — a missing item is
    False (globally disabling auto-promotion), never assumed present.
    """
    root = Path(root)
    state_dir = Path(state_dir)
    checks: list[Precondition] = []

    def _add(name: str, ok: bool, detail: str) -> None:
        checks.append(Precondition(name=name, ok=bool(ok), detail=detail))

    _add(
        "action_broker",
        (root / "pxx" / "broker.py").is_file(),
        "per-action-class authorization (B1)",
    )

    try:
        from ..outcome import TerminalCode

        _add(
            "taxonomy",
            len(list(TerminalCode)) >= 18,
            f"{len(list(TerminalCode))} terminal codes (B2)",
        )
    except Exception:
        _add("taxonomy", False, "outcome taxonomy unavailable (B2)")

    held_out = 0
    for tier in _TIERS:
        tier_dir = root / "evals" / tier
        if tier_dir.is_dir():
            held_out += sum(1 for c in load_cases(tier_dir) if c.partition is Partition.HELD_OUT)
    _add("held_out_corpus", held_out >= 1, f"{held_out} held-out cases (B3)")

    calibration = 0
    cal_dir = root / "evals" / "calibration"
    if cal_dir.is_dir():
        calibration = sum(1 for _ in cal_dir.glob("*.toml"))
    _add("calibration_corpus", calibration >= 8, f"{calibration} calibration cases (Phase 14)")

    try:
        from ..eval.report import compute_gates  # noqa: F401

        _add("real_hard_gates", True, "compute_gates present (M0 F1)")
    except Exception:
        _add("real_hard_gates", False, "compute_gates unavailable (M0 F1)")

    try:
        from .channels import Channel as _Channel

        _add("canary_channel", hasattr(_Channel, "CANARY"), "canary channel (B7)")
    except Exception:
        _add("canary_channel", False, "channels unavailable (B7)")

    try:
        from .promotion import write_promotion_record  # noqa: F401

        _add("promotion_records", True, "append-only records (M0 F5)")
    except Exception:
        _add("promotion_records", False, "promotion records unavailable")

    _add(
        "apply_envelope",
        (root / "pxx" / "improve" / "apply.py").is_file(),
        "apply->verify write boundary (B4)",
    )
    _add(
        "measured_utility",
        (root / "pxx" / "memory" / "utility.py").is_file(),
        "measured observed_utility (B5)",
    )

    try:
        from ..workflow import load_workflow

        load_workflow(root)
        _add("workflow_contract", True, "WORKFLOW.md valid (B1.5)")
    except Exception:
        _add("workflow_contract", False, "WORKFLOW.md missing/invalid (B1.5)")

    return tuple(checks)


def preconditions_met(preconditions: tuple[Precondition, ...]) -> bool:
    """Auto-promotion is globally enabled only when EVERY item is present."""
    return all(p.ok for p in preconditions)


__all__ = [
    "ComputedEvidence",
    "Precondition",
    "check_preconditions",
    "compute_evidence",
    "preconditions_met",
]
