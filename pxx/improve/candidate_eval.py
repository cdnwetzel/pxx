"""Phase 16 seam: one-command both-arms candidate evaluation.

Re-validates a persisted candidate (a hand-edited candidate.json is in the
threat model), runs the held-out corpus at baseline AND under the
candidate's overlay, and feeds both arms to the override-proof promotion
policy. Never applies anything to production; the verdict + evidence are
recorded next to the candidate.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..errors import PxxError
from ..eval.cases import Case, Partition, load_cases
from ..eval.harness import CaseResult, run_case
from ..eval.report import arm_metrics, compute_gates, corpus_fingerprint
from .candidates import Candidate, read_candidate, validate_candidate
from .promotion import classify_risk, compare

_TIERS = ("micro", "regression", "adversarial")

#: Injected seam for tests: (cases, overlay) -> per-case results.
ArmRunner = Callable[[list[Case], Candidate | None], list[CaseResult]]


@dataclass(frozen=True)
class _ArmScorecard:
    """Duck-typed scorecard for improve.promotion.compare."""

    corpus_fingerprint: str
    verdicts: dict[str, bool]
    gates: dict[str, bool]
    partition: str = "held-out"
    metrics: dict[str, float | None] = None  # type: ignore[assignment]


@dataclass(frozen=True)
class CandidateEvalVerdict:
    """The recorded outcome of evaluating one candidate, both arms."""

    candidate_id: str
    promoted: bool
    eligible: bool
    gained: tuple[str, ...]
    lost: tuple[str, ...]
    hard_gate_failures: tuple[str, ...]
    reason: str
    case_count: int
    risk_class: str = ""
    route: str = ""
    required_bars: tuple[str, ...] = ()
    metric_failures: tuple[str, ...] = ()


def _default_arm_runner(cases: list[Case], overlay: Candidate | None) -> list[CaseResult]:
    """Scripted CI arm: run every case through the harness. The overlay is
    recorded by the caller; scripted arms are overlay-insensitive (live arms
    are the real signal and plug into the same seam)."""
    return [run_case(case) for case in cases]


def _arm_scorecard(
    cases: list[Case],
    results: list[CaseResult],
    *,
    candidate_validated: bool = False,
) -> _ArmScorecard:
    gates = compute_gates(cases, results)
    if candidate_validated:
        # permission_expansion evidence: the candidate passed integrity
        # validation (no permission or budget increase) — a real producer,
        # not an assumption.
        gates["permission_expansion"] = True
    return _ArmScorecard(
        corpus_fingerprint=corpus_fingerprint(cases),
        verdicts={r.case_id: r.passed for r in results},
        gates=gates,
        metrics=arm_metrics(results),
    )


def evaluate_candidate(
    candidate_id: str,
    state_dir: Path | str,
    *,
    corpus_root: Path | str,
    arm_runner: ArmRunner | None = None,
    bus=None,
) -> CandidateEvalVerdict:
    """Evaluate ``candidate_id`` against the held-out corpus, both arms.

    Fail closed: an unreadable/invalid candidate raises; an empty held-out
    corpus raises (no evidence, no verdict).
    """
    state_dir = Path(state_dir)
    candidate = read_candidate(state_dir / "candidates" / candidate_id)
    validate_candidate(candidate)  # re-validate: persisted input is untrusted

    corpus_root = Path(corpus_root)
    cases: list[Case] = []
    for tier in _TIERS:
        tier_dir = corpus_root / tier
        if tier_dir.is_dir():
            cases.extend(c for c in load_cases(tier_dir) if c.partition is Partition.HELD_OUT)
    if not cases:
        raise PxxError(
            f"no held-out eval cases under {corpus_root} (fail-closed: "
            "a candidate cannot be judged without held-out evidence)"
        )

    runner = arm_runner or _default_arm_runner
    baseline = _arm_scorecard(cases, runner(cases, None))
    candidate_card = _arm_scorecard(cases, runner(cases, candidate), candidate_validated=True)
    risk = classify_risk(candidate)
    verdict = compare(baseline, candidate_card, risk_class=risk)

    result = CandidateEvalVerdict(
        candidate_id=candidate_id,
        promoted=verdict.promoted,
        eligible=verdict.eligible,
        gained=verdict.gained,
        lost=verdict.lost,
        hard_gate_failures=verdict.hard_gate_failures,
        reason=verdict.reason,
        case_count=len(cases),
        risk_class=str(risk),
        route=verdict.route,
        required_bars=verdict.required_bars,
        metric_failures=verdict.metric_failures,
    )
    _record(state_dir, candidate_id, baseline, candidate_card, result)
    if bus is not None:
        import asyncio

        async def _emit() -> None:
            await bus.emit(
                "evaluation_completed",
                {
                    "candidate_id": candidate_id,
                    "promoted": result.promoted,
                    "eligible": result.eligible,
                    "gained": len(result.gained),
                    "lost": len(result.lost),
                    "route": result.route,
                    "case_count": result.case_count,
                },
            )

        try:
            asyncio.get_running_loop().create_task(_emit())
        except RuntimeError:
            asyncio.run(_emit())
    return result


def _record(
    state_dir: Path,
    candidate_id: str,
    baseline: _ArmScorecard,
    candidate_card: _ArmScorecard,
    verdict: CandidateEvalVerdict,
) -> None:
    """Persist the evidence beside the candidate (best-effort metadata)."""
    payload: dict[str, Any] = {
        "candidate_id": candidate_id,
        "ts": time.time(),
        "baseline_verdicts": baseline.verdicts,
        "candidate_verdicts": candidate_card.verdicts,
        "gates": candidate_card.gates,
        "partition": "held-out",
        "promoted": verdict.promoted,
        "eligible": verdict.eligible,
        "gained": list(verdict.gained),
        "lost": list(verdict.lost),
        "hard_gate_failures": list(verdict.hard_gate_failures),
        "reason": verdict.reason,
        "case_count": verdict.case_count,
        "risk_class": verdict.risk_class,
        "route": verdict.route,
        "required_bars": list(verdict.required_bars),
        "metric_failures": list(verdict.metric_failures),
    }
    try:
        (state_dir / "candidates" / candidate_id / "evaluation.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n"
        )
    except OSError:
        pass  # evidence recording is best-effort, never blocks the verdict


__all__ = ["CandidateEvalVerdict", "evaluate_candidate"]
