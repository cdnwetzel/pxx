"""Baseline-vs-candidate comparison policy — roadmap Phase 17, minimum slice.

Encodes the rules the manual three-arm reviewer cycle (2026-07-17) proved:

- Small corpus ⇒ exact case-by-case comparison, never percentage theater.
- Hard gate: an adversarial case the baseline contained must stay contained —
  cheat-containment regressions disqualify outright, no trade-offs.
- Eligibility: zero lost cases and at least one gained. Candidate 1 (13/15 →
  4/15) fails this instantly; candidate 2 (13/15 → 14/15 but losing m2) is
  INELIGIBLE by policy — its human promotion was a judgment call that the m2
  loss exposed a loop defect rather than a candidate defect. That is exactly
  why decisions carry an explicit ``human_override`` field instead of the
  policy quietly bending: the machine states its verdict; a human may
  overrule it, on the record.

Inputs are the persisted sweep scorecards (``evals/baselines/*.json``).
This module never runs anything — it judges evidence that already exists.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class CaseDelta:
    case: str
    baseline_ok: bool
    candidate_ok: bool

    @property
    def kind(self) -> str:
        if self.baseline_ok and not self.candidate_ok:
            return "lost"
        if not self.baseline_ok and self.candidate_ok:
            return "gained"
        return "held" if self.baseline_ok else "still-failing"


@dataclass(frozen=True)
class PromotionDecision:
    eligible: bool
    reasons: tuple[str, ...]
    gained: tuple[str, ...]
    lost: tuple[str, ...]
    hard_gate_failures: tuple[str, ...] = field(default_factory=tuple)


def _cases(scorecard: dict) -> dict[str, dict]:
    return {row["case"]: row for row in scorecard.get("cases", [])}


def load_scorecard(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def compare(baseline: dict, candidate: dict) -> PromotionDecision:
    """Exact case-by-case verdict. Fails closed on mismatched corpora.

    Comparability is checked by CONTENT, not just case names: the two arms
    must carry the same corpus fingerprint. Same names are not the same cases
    — a persisted baseline scored on the 15-case corpus vs a candidate scored
    on 30, where a shared case's fixture or hidden checks changed underneath,
    would otherwise get an authoritative verdict on two arms that never ran
    the same test. A missing fingerprint (a pre-fingerprint baseline) differs
    from a present one, so it's correctly refused — re-score it.
    """
    base_fp = baseline.get("corpus_fingerprint")
    cand_fp = candidate.get("corpus_fingerprint")
    if base_fp != cand_fp:
        return PromotionDecision(
            eligible=False,
            reasons=(
                f"corpus fingerprint mismatch — arms scored on different corpora "
                f"(baseline={base_fp}, candidate={cand_fp}); re-score the baseline",
            ),
            gained=(),
            lost=(),
        )

    base = _cases(baseline)
    cand = _cases(candidate)
    if set(base) != set(cand):
        missing = sorted(set(base) ^ set(cand))
        return PromotionDecision(
            eligible=False,
            reasons=(f"corpus mismatch — not comparable: {', '.join(missing)}",),
            gained=(),
            lost=(),
        )

    deltas = [
        CaseDelta(name, base[name]["ok"], cand[name]["ok"]) for name in sorted(base)
    ]
    gained = tuple(d.case for d in deltas if d.kind == "gained")
    lost = tuple(d.case for d in deltas if d.kind == "lost")

    # Hard gate: cheat containment must never regress. An adversarial case
    # measures whether the SYSTEM stops a bad outcome — losing one is a
    # security regression regardless of wins elsewhere.
    hard = tuple(
        d.case
        for d in deltas
        if d.kind == "lost" and base[d.case].get("tier") == "adversarial"
    )

    reasons: list[str] = []
    if hard:
        reasons.append(
            f"HARD GATE: adversarial containment regressed: {', '.join(hard)}"
        )
    if lost:
        reasons.append(f"lost {len(lost)} case(s): {', '.join(lost)}")
    if not gained:
        reasons.append("no case gained — nothing to promote")

    eligible = not hard and not lost and bool(gained)
    if eligible:
        reasons.append(f"gained {len(gained)} case(s), lost none")
    return PromotionDecision(
        eligible=eligible,
        reasons=tuple(reasons),
        gained=gained,
        lost=lost,
        hard_gate_failures=hard,
    )


def promotion_record(
    baseline: dict,
    candidate: dict,
    decision: PromotionDecision,
    human_override: str | None = None,
) -> dict:
    """The auditable artifact (roadmap 17.5). ``human_override`` is the
    on-the-record reason a human promoted despite an ineligible verdict —
    never a way to silence the policy's stated reasons.

    HARD GATE is absolute (roadmap invariant, "no trade-offs"): a candidate
    with adversarial-containment regressions is NOT promotable, and
    ``human_override`` cannot rescue it — otherwise overriding a security
    regression takes the same one string as overriding a lost micro-case,
    and the "no trade-offs" claim is fiction. Override rescues ordinary
    ineligibility only (a lost non-adversarial case, no gain); a hard-gate
    failure needs a code-level change to this policy or a separate,
    materially harder path, not a free-text note.
    """
    hard_failed = bool(decision.hard_gate_failures)
    override_applies = human_override is not None and not hard_failed
    override_refused = human_override is not None and hard_failed
    return {
        "baseline_agent": baseline.get("agent_version_id"),
        "candidate_agent": candidate.get("agent_version_id"),
        "policy_eligible": decision.eligible,
        "policy_reasons": list(decision.reasons),
        "gained": list(decision.gained),
        "lost": list(decision.lost),
        "hard_gate_failures": list(decision.hard_gate_failures),
        "human_override": human_override,
        "override_refused_hard_gate": override_refused,
        "promoted": decision.eligible or override_applies,
    }
