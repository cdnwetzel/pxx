"""Phase 19: scheduled propose-only improvement cycle + triage inbox.

``run_cycle(state_dir, mode="propose-only")`` walks COLLECT -> NORMALIZE ->
ANALYZE -> PROPOSE -> VALIDATE, persists candidates and a report, and STOPS
BEFORE PROMOTION: it never activates a channel and never writes a promotion
record. Only ``"propose-only"`` mode exists; anything else is refused.

Durable state lives in ``<state_dir>/cycle-state.json``. Every transition is
idempotent: candidate ids are content-derived, existing candidate files are
never rewritten, inbox entries have deterministic names, so re-running after
an interruption resumes without duplicating work.

Triage inbox: ``<state_dir>/inbox/{qualified,rejected,human-review-required}/``
— one JSON file per proposal, naming the reason for rejected items.

Anti-spam rules (each skips the proposal, routed to ``rejected``):

- evidence thin: the source cluster has < :data:`MIN_CLUSTER_EVIDENCE` runs;
- the cluster already has an active candidate;
- a prior identical candidate (same target+operation) failed.

Only proposals with a deterministically derivable value become candidates
today (``memory_retrieval_limit`` adjustments); everything else is routed to
``human-review-required``. Concurrent cycles are serialized by an
``fcntl.flock`` on ``<state_dir>/cycle.lock``; a second cycle is refused.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..errors import ConfigError, PxxError
from ..runs import list_runs
from .candidates import (
    CandidateClass,
    make_candidate,
    validate_candidate,
    write_candidate,
)
from .mining import Cluster, Proposal, cluster_outcomes, propose_from_clusters

log = logging.getLogger("pxx.improve.cycle")

MODE_PROPOSE_ONLY = "propose-only"

#: Anti-spam: clusters smaller than this never yield proposals.
MIN_CLUSTER_EVIDENCE = 3

#: The one proposal type whose value is derivable without human judgment.
_DERIVABLE_TARGET = "memory_retrieval_limit"
_DERIVABLE_OPERATION = "adjust_memory"
DEFAULT_MEMORY_RETRIEVAL_LIMIT = 8

INBOX_QUALIFIED = "qualified"
INBOX_REJECTED = "rejected"
INBOX_HUMAN = "human-review-required"

_STATE_FILENAME = "cycle-state.json"
_LOCK_FILENAME = "cycle.lock"
_REPORT_FILENAME = "cycle-report.json"


@dataclass(frozen=True)
class CycleReport:
    """What one cycle did. ``stopped_before_promotion`` is pinned True."""

    cycle_id: str
    mode: str
    runs_collected: int
    clusters: int
    proposals: int
    candidates: tuple[str, ...]  # qualified candidate ids persisted
    skipped: tuple[dict[str, str], ...]  # {"signature", "reason"} anti-spam skips
    human_review: tuple[str, ...]  # proposal signatures routed to humans
    stopped_before_promotion: bool = True


def _proposal_signature(proposal: Proposal) -> str:
    return f"{proposal.target}:{proposal.operation}"


def _candidate_id(proposal: Proposal) -> str:
    digest = hashlib.sha256(_proposal_signature(proposal).encode()).hexdigest()
    return f"cand-{digest[:12]}"


def _cluster_key(cluster: Cluster) -> str:
    return "|".join(
        [
            cluster.terminal_code,
            cluster.backend,
            cluster.model,
            str(cluster.memory_used),
            cluster.rounds_bucket,
        ]
    )


def _source_cluster_keys(clusters: list[Cluster], proposal: Proposal) -> list[str]:
    evidence = set(proposal.evidence)
    return sorted(_cluster_key(c) for c in clusters if evidence.intersection(c.run_ids))


def _load_state(state_dir: Path) -> dict[str, Any]:
    try:
        data = json.loads((state_dir / _STATE_FILENAME).read_text())
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {
        "processed_run_ids": list(data.get("processed_run_ids") or []),
        "active_candidates": dict(data.get("active_candidates") or {}),
        "failed_signatures": list(data.get("failed_signatures") or []),
        "cycles": list(data.get("cycles") or []),
    }


def _save_state(state_dir: Path, state: dict[str, Any]) -> None:
    path = state_dir / _STATE_FILENAME
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def _inbox_write(state_dir: Path, box: str, proposal: Proposal, reason: str) -> Path:
    """Persist one triage entry. Deterministic filename -> idempotent."""
    dest = state_dir / "inbox" / box
    dest.mkdir(parents=True, exist_ok=True)
    slug = hashlib.sha256(_proposal_signature(proposal).encode()).hexdigest()[:12]
    path = dest / f"{slug}.json"
    payload = proposal.to_dict()
    payload["reason"] = reason
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return path


def _candidate_path(state_dir: Path, candidate_id: str) -> Path:
    return state_dir / "candidates" / candidate_id / "candidate.json"


def _normalize(runs: Any) -> list[dict[str, Any]]:
    """NORMALIZE: project run records into mining-shaped mappings."""
    mined = []
    for run in runs:
        mined.append(
            {
                "run_id": run.run_id,
                "terminal_code": run.code,
                "backend": run.backend,
                "model": run.model,
                "agent_version_id": run.agent_version_id,
                "rounds": run.rounds,
                "memory_used": run.memory,
            }
        )
    return mined


def _derive_candidate(proposal: Proposal) -> Any:
    """Build the deterministically derivable candidate for a proposal."""
    return make_candidate(
        _candidate_id(proposal),
        CandidateClass.SETTINGS,
        _DERIVABLE_TARGET,
        DEFAULT_MEMORY_RETRIEVAL_LIMIT,
        rationale=proposal.hypothesis,
        evidence=proposal.evidence,
    )


def _validate_and_persist(state_dir: Path, state: dict[str, Any]) -> CycleReport:
    """ANALYZE -> PROPOSE -> VALIDATE + triage. Never promotes."""
    runs = list_runs(state_dir, limit=1_000_000)  # COLLECT
    mined = _normalize(runs)  # NORMALIZE

    clusters = cluster_outcomes(mined)  # ANALYZE
    thick = [c for c in clusters if c.size >= MIN_CLUSTER_EVIDENCE]
    thin = [c for c in clusters if c.size < MIN_CLUSTER_EVIDENCE]

    proposals = propose_from_clusters(thick)  # PROPOSE

    # VALIDATE + triage
    candidates: list[str] = []
    skipped: list[dict[str, str]] = []
    human_review: list[str] = []
    failed = set(state["failed_signatures"])
    active = state["active_candidates"]

    for cluster in thin:  # anti-spam: evidence thin (< 3 runs in cluster)
        skipped.append(
            {
                "signature": _cluster_key(cluster),
                "reason": (
                    f"thin evidence: {cluster.size} run(s) in cluster (< {MIN_CLUSTER_EVIDENCE})"
                ),
            }
        )

    for proposal in proposals:
        signature = _proposal_signature(proposal)
        source_keys = _source_cluster_keys(thick, proposal)
        active_ids = [active[k] for k in source_keys if k in active]

        if signature in failed:
            reason = "prior identical candidate failed"
            skipped.append({"signature": signature, "reason": reason})
            _inbox_write(state_dir, INBOX_REJECTED, proposal, reason)
            continue

        derivable = (
            proposal.target == _DERIVABLE_TARGET and proposal.operation == _DERIVABLE_OPERATION
        )
        if derivable:
            candidate = _derive_candidate(proposal)
            if active_ids and candidate.id not in active_ids:
                reason = "cluster already has an active candidate"
                skipped.append({"signature": signature, "reason": reason})
                _inbox_write(state_dir, INBOX_REJECTED, proposal, reason)
                continue
            if _candidate_path(state_dir, candidate.id).exists():
                candidates.append(candidate.id)  # idempotent no-op re-run
            else:
                validate_candidate(candidate)
                write_candidate(candidate, state_dir)
                candidates.append(candidate.id)
                for key in source_keys:
                    active.setdefault(key, candidate.id)
            _inbox_write(state_dir, INBOX_QUALIFIED, proposal, "candidate persisted")
        else:
            if active_ids:
                reason = "cluster already has an active candidate"
                skipped.append({"signature": signature, "reason": reason})
                _inbox_write(state_dir, INBOX_REJECTED, proposal, reason)
            else:
                reason = "value not derivable without human judgment"
                human_review.append(signature)
                _inbox_write(state_dir, INBOX_HUMAN, proposal, reason)

    # durable state (processed runs recorded; nothing is ever promoted here)
    seen = set(state["processed_run_ids"])
    seen.update(r.run_id for r in runs)
    state["processed_run_ids"] = sorted(seen)

    cycle_id = hashlib.sha256(
        json.dumps(
            {
                "runs": state["processed_run_ids"],
                "candidates": sorted(candidates),
                "skipped": sorted((s["signature"], s["reason"]) for s in skipped),
                "human": sorted(human_review),
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()[:12]
    report = CycleReport(
        cycle_id=f"cycle-{cycle_id}",
        mode=MODE_PROPOSE_ONLY,
        runs_collected=len(runs),
        clusters=len(clusters),
        proposals=len(proposals),
        candidates=tuple(sorted(candidates)),
        skipped=tuple(sorted(skipped, key=lambda s: (s["signature"], s["reason"]))),
        human_review=tuple(sorted(human_review)),
        stopped_before_promotion=True,
    )
    state["cycles"].append(
        {"id": report.cycle_id, "ts": datetime.now(UTC).isoformat(), "mode": report.mode}
    )
    _save_state(state_dir, state)

    # persist the report (latest wins; candidates are the durable artifacts)
    report_path = state_dir / _REPORT_FILENAME
    payload = {
        "cycle_id": report.cycle_id,
        "mode": report.mode,
        "runs_collected": report.runs_collected,
        "clusters": report.clusters,
        "proposals": report.proposals,
        "candidates": list(report.candidates),
        "skipped": [dict(s) for s in report.skipped],
        "human_review": list(report.human_review),
        "stopped_before_promotion": report.stopped_before_promotion,
    }
    report_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return report


def run_cycle(state_dir: Path | str, mode: str = MODE_PROPOSE_ONLY) -> CycleReport:
    """Run one propose-only improvement cycle. STOPS BEFORE PROMOTION.

    Raises :class:`ConfigError` for any mode other than ``"propose-only"``
    and :class:`PxxError` when another cycle holds the lock (refused, never
    queued).
    """
    if mode != MODE_PROPOSE_ONLY:
        raise ConfigError(
            f"unknown cycle mode: {mode!r} (only {MODE_PROPOSE_ONLY!r} exists; "
            "the cycle stops before promotion by design)"
        )
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = state_dir / _LOCK_FILENAME
    with lock_path.open("w") as lock_fh:
        try:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise PxxError("another improvement cycle is already running") from exc
        try:
            state = _load_state(state_dir)
            return _validate_and_persist(state_dir, state)
        finally:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
