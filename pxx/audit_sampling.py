"""Phase 14.5: deterministic human audit sampling.

Flags runs and promotions for human review at the roadmap's policy rates —
100% of promotions and high-risk actions, ~20% of ordinary runs. Selection
is a pure hash of the run id (no RNG): the same run id always yields the
same decision, so the flagged set is reproducible across passes and
byte-identical reports are possible.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

#: Policy rates (roadmap Phase 14.5).
PROMOTION_RATE = 1.0  # 100% of promotions
HIGH_RISK_RATE = 1.0  # 100% of high-risk actions/runs
ORDINARY_RATE = 0.2  # ~20% of ordinary runs


@dataclass(frozen=True)
class AuditSample:
    """Whether a run/promotion is flagged for human audit, and why."""

    sampled: bool
    reason: str
    rate: float


def _bucket(run_id: str) -> float:
    """Deterministic [0, 1) bucket for a run id (sha256, no RNG)."""
    digest = hashlib.sha256(run_id.encode()).hexdigest()[:8]
    return int(digest, 16) / 0xFFFFFFFF


def audit_sample(run_id: str, *, risk: str = "ordinary", promotion: bool = False) -> AuditSample:
    """Decide whether ``run_id`` is flagged for human audit.

    ``promotion=True`` or ``risk="high"`` -> always flagged (100%).
    Otherwise a deterministic ~20% sample keyed on the run id — reproducible
    across any number of passes.
    """
    if promotion:
        return AuditSample(True, "100% of promotions are human-audited", PROMOTION_RATE)
    if risk == "high":
        return AuditSample(True, "100% of high-risk runs are human-audited", HIGH_RISK_RATE)
    hit = _bucket(run_id) < ORDINARY_RATE
    return AuditSample(
        hit,
        "deterministic 20% sample of ordinary runs (hash of run_id)",
        ORDINARY_RATE,
    )


__all__ = [
    "HIGH_RISK_RATE",
    "ORDINARY_RATE",
    "PROMOTION_RATE",
    "AuditSample",
    "audit_sample",
]
