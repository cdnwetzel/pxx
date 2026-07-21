"""Phase 17 tests: promotion policy (hard-gate absoluteness, eligible math,
fingerprint fail-closed) + append-only promotion records."""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime

import pytest

from pxx.improve.promotion import (
    HARD_GATES,
    PromotionRecord,
    build_record,
    compare,
    write_promotion_record,
)

ALL_GATES_GREEN = {g: True for g in HARD_GATES}
FP = "fingerprint-abc"


@dataclasses.dataclass(frozen=True)
class FakeScorecard:
    """Duck-typed stand-in for pxx.eval.report.Scorecard."""

    agent_version_id: str
    corpus_fingerprint: str
    verdicts: dict[str, bool]
    gates: dict[str, bool]
    partition: str = "held-out"
    metrics: dict = dataclasses.field(default_factory=dict)


def scorecard(verdicts, gates=None, fp=FP, agent="agent-a"):
    return FakeScorecard(
        agent, fp, dict(verdicts), dict(gates if gates is not None else ALL_GATES_GREEN)
    )


# --- eligible math ------------------------------------------------------------


def test_eligible_requires_gain_no_loss_green_gates():
    baseline = scorecard({"case-1": True, "case-2": False})
    candidate = scorecard({"case-1": True, "case-2": True})
    verdict = compare(baseline, candidate)
    assert verdict.eligible and verdict.promoted
    assert verdict.gained == ("case-2",)
    assert verdict.lost == ()
    assert verdict.hard_gate_failures == ()


def test_no_gained_cases_not_eligible():
    baseline = scorecard({"case-1": True})
    candidate = scorecard({"case-1": True})
    verdict = compare(baseline, candidate)
    assert not verdict.eligible and not verdict.promoted
    assert "no gained cases" in verdict.reason


def test_lost_case_blocks_eligibility_even_with_gains():
    baseline = scorecard({"keep": True, "lose": True})
    candidate = scorecard({"keep": True, "lose": False, "new": True})
    verdict = compare(baseline, candidate)
    assert not verdict.eligible and not verdict.promoted
    assert verdict.lost == ("lose",)
    assert verdict.gained == ("new",)


def test_case_missing_from_candidate_counts_as_lost():
    baseline = scorecard({"gone": True})
    candidate = scorecard({})
    verdict = compare(baseline, candidate)
    assert verdict.lost == ("gone",)


# --- hard gates: absolute -----------------------------------------------------


@pytest.mark.parametrize("gate", HARD_GATES)
def test_each_hard_gate_failure_disqualifies(gate):
    baseline = scorecard({"c": False})
    candidate = scorecard({"c": True}, gates={**ALL_GATES_GREEN, gate: False})
    verdict = compare(baseline, candidate)
    assert not verdict.eligible and not verdict.promoted
    assert verdict.hard_gate_failures == (gate,)


def test_missing_gate_evidence_fails_closed():
    baseline = scorecard({"c": False})
    candidate = scorecard({"c": True}, gates={})  # no gate evidence at all
    verdict = compare(baseline, candidate)
    assert not verdict.promoted
    assert set(verdict.hard_gate_failures) == set(HARD_GATES)


def test_human_override_cannot_rescue_hard_gate_failure():
    baseline = scorecard({"c": False, "d": True})
    candidate = scorecard(
        {"c": True, "d": True},
        gates={**ALL_GATES_GREEN, "scope_violation": False},
    )
    verdict = compare(baseline, candidate, human_override="alice")
    assert not verdict.promoted
    assert not verdict.eligible
    assert verdict.override_refused_hard_gate is True
    assert verdict.human_override == "alice"
    assert "scope_violation" in verdict.hard_gate_failures


def test_override_refused_flag_only_on_hard_gate_override_attempt():
    baseline = scorecard({"c": False})
    candidate = scorecard({"c": True}, gates={**ALL_GATES_GREEN, "permission_expansion": False})
    no_override = compare(baseline, candidate)
    assert no_override.override_refused_hard_gate is False


# --- human override on soft failures -------------------------------------------


def test_human_override_rescues_soft_failure():
    baseline = scorecard({"keep": True, "lose": True})
    candidate = scorecard({"keep": True, "lose": False})
    verdict = compare(baseline, candidate, human_override="bob")
    assert verdict.promoted
    assert not verdict.eligible  # policy math unchanged
    assert not verdict.override_refused_hard_gate
    assert "human override" in verdict.reason


# --- corpus fingerprint: fail closed -------------------------------------------


def test_fingerprint_mismatch_refuses():
    baseline = scorecard({"c": False}, fp="fp-1")
    candidate = scorecard({"c": True}, fp="fp-2")
    verdict = compare(baseline, candidate)
    assert not verdict.eligible and not verdict.promoted
    assert "fingerprint" in verdict.reason


def test_fingerprint_mismatch_refuses_even_with_override():
    baseline = scorecard({"c": False}, fp="fp-1")
    candidate = scorecard({"c": True}, fp="fp-2")
    verdict = compare(baseline, candidate, human_override="carol")
    assert not verdict.promoted


def test_missing_fingerprint_differs_from_present():
    baseline = scorecard({"c": False}, fp=FP)
    candidate = dataclasses.replace(scorecard({"c": True}), corpus_fingerprint="")
    verdict = compare(baseline, candidate)
    assert not verdict.promoted
    assert "fingerprint" in verdict.reason


def test_both_fingerprints_missing_refuses():
    baseline = scorecard({"c": False}, fp="")
    candidate = scorecard({"c": True}, fp="")
    assert not compare(baseline, candidate).promoted


def test_scorecard_without_gates_attribute_fails_closed():
    @dataclasses.dataclass(frozen=True)
    class BareScorecard:
        corpus_fingerprint: str
        verdicts: dict[str, bool]
        partition: str = "held-out"

    baseline = BareScorecard(FP, {"c": False})
    candidate = BareScorecard(FP, {"c": True})
    verdict = compare(baseline, candidate)
    assert not verdict.promoted
    assert set(verdict.hard_gate_failures) == set(HARD_GATES)


# --- promotion records: append-only --------------------------------------------


def make_record(record_id="promo-001", **kw):
    return build_record(
        record_id,
        kw.pop("baseline_id", "agent-baseline"),
        kw.pop("candidate_id", "agent-candidate"),
        kw.pop("eval_ids", ("eval-1", "eval-2")),
        kw.pop("gates", ALL_GATES_GREEN),
        kw.pop("approver", "alice"),
        kw.pop("rollback_target", "agent-baseline"),
        now=datetime(2026, 7, 18, tzinfo=UTC),
    )


def test_promotion_record_roundtrip(tmp_path):
    record = make_record()
    path = write_promotion_record(record, tmp_path / ".pxx")
    assert path == tmp_path / ".pxx" / "promotions" / "promo-001.json"
    data = json.loads(path.read_text())
    assert data["baseline_id"] == "agent-baseline"
    assert data["candidate_id"] == "agent-candidate"
    assert data["eval_ids"] == ["eval-1", "eval-2"]
    assert data["gates"] == ALL_GATES_GREEN
    assert data["approver"] == "alice"
    assert data["rollback_target"] == "agent-baseline"
    assert data["timestamp"].startswith("2026-07-18")


def test_promotion_records_append_only_never_overwrite(tmp_path):
    record = make_record()
    write_promotion_record(record, tmp_path)
    changed = dataclasses.replace(record, approver="mallory")
    with pytest.raises(FileExistsError):
        write_promotion_record(changed, tmp_path)
    # original untouched
    path = tmp_path / "promotions" / "promo-001.json"
    assert json.loads(path.read_text())["approver"] == "alice"


def test_promotion_record_id_traversal_rejected(tmp_path):
    with pytest.raises(ValueError, match="unsafe"):
        write_promotion_record(make_record(record_id="../escape"), tmp_path)


def test_build_record_uses_injected_clock():
    record = make_record()
    assert isinstance(record, PromotionRecord)
    assert record.timestamp == datetime(2026, 7, 18, tzinfo=UTC).isoformat()


# --- B6: held-out judgment, multi-metric guards, risk routing ------------------------


def test_dev_win_refused_partition_recorded():
    """B6.1: a candidate that WINS every case still isn't eligible when the
    scorecard is development-only; the verdict records the partition."""
    candidate = scorecard({"c1": True, "c2": True})
    candidate = FakeScorecard(
        candidate.agent_version_id,
        candidate.corpus_fingerprint,
        candidate.verdicts,
        candidate.gates,
        partition="dev",
    )
    verdict = compare(scorecard({"c1": True, "c2": False}), candidate)
    assert not verdict.eligible and not verdict.promoted
    assert "held-out" in verdict.reason
    assert verdict.partition == "dev"


def test_held_out_loss_not_eligible():
    """B6.1: on the held-out partition a losing candidate is not eligible."""
    verdict = compare(
        scorecard({"c1": True, "c2": True}),
        scorecard({"c1": True, "c2": False}),
    )
    assert not verdict.eligible
    assert verdict.partition == "held-out"


# --- multi-metric guards ---------------------------------------------------------------


def _metric_scorecard(metrics, **kw):
    return FakeScorecard(
        "agent-x",
        FP,
        kw.pop("verdicts", {"c1": True}),
        kw.pop("gates", ALL_GATES_GREEN),
        metrics=metrics,
    )


def test_cost_over_115_percent_refused():
    """B6.2: pass-rate up but cost up 16% -> NOT eligible (the 1.15x rule)."""
    baseline = _metric_scorecard({"cost_per_task": 0.010}, verdicts={"c1": False, "c2": False})
    candidate = _metric_scorecard({"cost_per_task": 0.0116}, verdicts={"c1": True, "c2": True})
    verdict = compare(baseline, candidate)
    assert not verdict.eligible
    assert any("cost_per_task" in f for f in verdict.metric_failures)
    assert "not eligible" in verdict.reason


def test_cost_within_115_percent_eligible():
    baseline = _metric_scorecard({"cost_per_task": 0.010}, verdicts={"c1": False})
    candidate = _metric_scorecard({"cost_per_task": 0.0115}, verdicts={"c1": True})
    verdict = compare(baseline, candidate)
    assert verdict.eligible and verdict.promoted
    assert "metrics within guards" in verdict.reason


def test_each_metric_guard():
    cases = [
        ("avg_rounds", 4.0, 5.2),  # > 1.25x
        ("p95_seconds", 10.0, 13.0),  # > 1.25x
        ("avg_diff_lines", 100.0, 160.0),  # > 1.5x
        ("rollback_rate", 0.0, 0.10),  # delta > 0.05
        ("memory_utility", 0.8, 0.70),  # drop > 0.05
    ]
    for name, base_val, cand_val in cases:
        baseline = _metric_scorecard({name: base_val}, verdicts={"c1": False})
        candidate = _metric_scorecard({name: cand_val}, verdicts={"c1": True})
        verdict = compare(baseline, candidate)
        assert not verdict.eligible, name
        assert any(name in f for f in verdict.metric_failures), name


def test_null_metrics_unmeasured_not_blocking():
    """B6.2: unpriced (null cost) records as unmeasured — never fabricated,
    never blocking."""
    baseline = _metric_scorecard({}, verdicts={"c1": False})
    candidate = _metric_scorecard({}, verdicts={"c1": True})
    verdict = compare(baseline, candidate)
    assert verdict.eligible
    assert verdict.metrics_report["cost_per_task"] == "unmeasured"


def test_metric_regression_is_soft_for_human_override():
    """Metric regressions are SOFT (human can override); hard gates stay absolute."""
    baseline = _metric_scorecard({"cost_per_task": 0.010}, verdicts={"c1": False})
    candidate = _metric_scorecard({"cost_per_task": 0.012}, verdicts={"c1": True})
    verdict = compare(baseline, candidate, human_override="alice")
    assert verdict.promoted and not verdict.eligible
    assert "human override" in verdict.reason


# --- risk-class route table ----------------------------------------------------------


def test_route_table_per_risk_class():
    from pxx.improve.promotion import RiskClass

    baseline = scorecard({"c1": False})
    candidate = scorecard({"c1": True})
    verdict = compare(baseline, candidate, risk_class=RiskClass.LOW)
    assert verdict.route == "fast"
    assert "canary" not in verdict.required_bars
    verdict = compare(baseline, candidate, risk_class=RiskClass.MEDIUM)
    assert verdict.route == "standard"
    assert "shadow" in verdict.required_bars and "canary" in verdict.required_bars
    verdict = compare(baseline, candidate, risk_class=RiskClass.HIGH)
    assert verdict.route == "human"
    assert "human-approval" in verdict.required_bars


def test_unknown_risk_routes_human_fail_closed():
    verdict = compare(scorecard({"c1": False}), scorecard({"c1": True}))
    assert verdict.route == "human"
    assert "human-approval" in verdict.required_bars


def test_classify_risk_lives_in_promotion():
    from pxx.improve.candidates import CandidateClass, make_candidate
    from pxx.improve.promotion import RiskClass, classify_risk

    low = make_candidate("a", CandidateClass.SETTINGS, "memory_retrieval_limit", 4, "r", ("run-1",))
    assert classify_risk(low) is RiskClass.LOW
    medium = make_candidate(
        "b", CandidateClass.SETTINGS, "model", {"provider": "ollama", "model": "x"}, "r", ("run-1",)
    )
    assert classify_risk(medium) is RiskClass.MEDIUM
    high = make_candidate("c", CandidateClass.CONTENT, "pxx/prompts/x.md", "text", "r", ("run-1",))
    assert classify_risk(high) is RiskClass.LOW  # non-authoritative prompt
    evil = make_candidate("d", CandidateClass.SETTINGS, "permissions", {}, "r", ("run-1",))
    assert classify_risk(evil) is RiskClass.HIGH
