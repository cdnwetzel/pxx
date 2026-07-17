"""Candidate evaluation — roadmap Phase 16→17 seam, minimum slice.

Turns a validated candidate into a scored promotion verdict in one step:
run the live eval corpus at baseline, run it again under the candidate's
env overlay, then feed both scorecards to the promotion policy. This is the
sweep that was hand-run three times on 2026-07-17 (baseline / candidate /
compare), now one command.

Human-gated by construction: it produces a verdict and a promotion record,
never applies anything — the candidate generator cannot promote itself.

The per-case arm runner is injected so the orchestration is unit-testable
without driving real loops; the CLI wires in the live arm.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from pxx import candidates, evaluation, promotion

# (case_id, tier, ok) — the minimal per-case result the scorecard needs.
ArmRunner = Callable[[evaluation.EvalCase, dict[str, str]], bool]


def _scorecard(
    agent_id: str,
    cases: list[evaluation.EvalCase],
    runner: ArmRunner,
    overlay: dict,
    corpus_fingerprint: str,
) -> dict:
    """Run every case through one arm and shape the result for
    promotion.compare (which keys on case/tier/ok). Stamped with the corpus
    fingerprint so compare() can refuse arms scored on a drifted corpus."""
    rows = []
    for case in cases:
        ok = runner(case, overlay)
        rows.append({"case": case.id, "tier": case.tier, "ok": ok})
    return {
        "agent_version_id": agent_id,
        "corpus_fingerprint": corpus_fingerprint,
        "cases": rows,
    }


def evaluate_candidate(
    cand: candidates.Candidate,
    runner: ArmRunner,
    evals_dir: Path | None = None,
) -> dict:
    """Both-arms eval + compare for one candidate. Returns a promotion record.

    Refuses to run an invalid candidate — the integrity gate applies here too,
    not just at proposal time (a persisted candidate could have been
    hand-edited)."""
    validation = candidates.validate_candidate(cand)
    if not validation.ok:
        return {
            "candidate": cand.candidate_id,
            "error": "candidate failed integrity validation",
            "reasons": list(validation.reasons),
            "promoted": False,
        }

    cases: list[evaluation.EvalCase] = []
    for tier in evaluation.TIERS:
        cases.extend(evaluation.load_suite(tier, evals_dir))
    if not cases:
        # Fail closed on an empty corpus — same rule as pxx --eval.
        return {
            "candidate": cand.candidate_id,
            "error": "no eval cases found (corpus ships with a repo checkout only)",
            "promoted": False,
        }

    fp = evaluation.corpus_fingerprint(evals_dir)
    baseline = _scorecard("baseline", cases, runner, {}, fp)
    candidate_card = _scorecard(
        f"candidate:{cand.field}={cand.value}",
        cases,
        runner,
        candidates.env_overlay(cand),
        fp,
    )
    decision = promotion.compare(baseline, candidate_card)
    record = promotion.promotion_record(baseline, candidate_card, decision)
    record["candidate"] = cand.candidate_id
    record["field"] = cand.field
    record["value"] = cand.value
    record["from_observation"] = cand.from_observation
    return record


def live_runner(evals_dir: Path | None = None) -> ArmRunner:
    """The production arm: run the real loop in a fixture worktree under the
    given overlay, judged by the corpus's hidden checks. Slow (a real loop per
    case) and compute-bound on the inference node — this is the honest signal."""
    import os

    def _run(case: evaluation.EvalCase, overlay: dict[str, str]) -> bool:
        saved = {k: os.environ.get(k) for k in overlay}
        os.environ.update(overlay)
        try:
            result, _run_id = evaluation.run_live_arm(case)
            return result.ok
        finally:
            for k, prev in saved.items():
                if prev is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = prev

    return _run
