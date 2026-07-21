"""Phase 21 tests: risk classes, readiness bars (fail-closed), and
evidence-gated auto-promotion (default posture: refuse)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pxx.improve.autopromote import (
    ROLLBACK_COMMAND,
    AutoPromoteVerdict,
    Evidence,
    ReadinessCounts,
    RiskClass,
    auto_promote,
    classify_risk,
    evaluate_readiness,
    readiness,
)
from pxx.improve.candidates import CandidateClass, make_candidate

# --- risk classification ----------------------------------------------------------


def _settings_candidate(target, value, **kw):
    return make_candidate(
        "cand-test",
        CandidateClass.SETTINGS,
        target,
        value,
        "rationale",
        ("run-1", "run-2", "run-3"),
        **kw,
    )


def _content_candidate(target):
    return make_candidate(
        "cand-test",
        CandidateClass.CONTENT,
        target,
        "prompt text",
        "rationale",
        ("run-1", "run-2", "run-3"),
    )


def test_memory_retrieval_limit_is_low():
    assert classify_risk(_settings_candidate("memory_retrieval_limit", 8)) is RiskClass.LOW


def test_tighten_only_budget_is_low():
    cand = _settings_candidate(
        "budgets",
        {"max_tokens": 1000},
        baseline_budgets={"max_tokens": 2000},
    )
    assert classify_risk(cand) is RiskClass.LOW


def test_budget_loosening_is_medium():
    cand = _settings_candidate(
        "budgets",
        {"max_tokens": 3000},
        baseline_budgets={"max_tokens": 2000},
    )
    assert classify_risk(cand) is RiskClass.MEDIUM


def test_budget_without_baseline_is_medium():
    cand = _settings_candidate("budgets", {"max_tokens": 1000})
    assert classify_risk(cand) is RiskClass.MEDIUM


@pytest.mark.parametrize("target", ["model", "fallback_models", "review_mode"])
def test_model_and_review_mode_are_medium(target):
    value = {"model": "m2"} if target != "review_mode" else "tests"
    assert classify_risk(_settings_candidate(target, value)) is RiskClass.MEDIUM


def test_main_system_prompt_is_medium():
    assert classify_risk(_content_candidate("pxx/prompts/native_system.md")) is RiskClass.MEDIUM


def test_non_authoritative_prompt_wording_is_low():
    assert classify_risk(_content_candidate("pxx/prompts/review.md")) is RiskClass.LOW


@pytest.mark.parametrize(
    "target",
    ["pxx/eval/harness.py", "evals/micro/x.toml", "pxx/improve/promotion.py"],
)
def test_protected_paths_are_high(target):
    assert classify_risk(_content_candidate(target)) is RiskClass.HIGH


@pytest.mark.parametrize("target", ["permissions", "scope", "hooks", "evaluators", "release"])
def test_permissions_evaluators_release_are_high(target):
    assert classify_risk(_settings_candidate(target, "x")) is RiskClass.HIGH


def test_unknown_settings_target_fails_closed_high():
    assert classify_risk(_settings_candidate("something_new", 1)) is RiskClass.HIGH


# --- readiness bars ------------------------------------------------------------------


GREEN = ReadinessCounts(
    eval_cases=50,
    real_runs=100,
    human_approved_promotions=3,
    unresolved_critical_defects=0,
)


def test_readiness_green_at_exact_bars():
    report = evaluate_readiness(GREEN)
    assert report.green
    assert report.unmet == ()


@pytest.mark.parametrize(
    "field,value,bar",
    [
        ("eval_cases", 49, "eval_cases"),
        ("real_runs", 99, "real_runs"),
        ("human_approved_promotions", 2, "human_approved_promotions"),
        ("unresolved_critical_defects", 1, "unresolved_critical_defects"),
    ],
)
def test_each_unmet_bar_fails_readiness(field, value, bar):
    counts = ReadinessCounts(**{**GREEN.__dict__, field: value})
    report = evaluate_readiness(counts)
    assert not report.green
    assert bar in report.unmet


@pytest.mark.parametrize("field", list(GREEN.__dict__))
def test_missing_evidence_fails_closed(field):
    counts = ReadinessCounts(**{**GREEN.__dict__, field: None})
    assert not evaluate_readiness(counts).green


def test_readiness_from_disk(tmp_path):
    state_dir = tmp_path / ".pxx"
    evals_dir = tmp_path / "evals"
    for i in range(50):  # 50 eval cases
        (evals_dir / "micro").mkdir(parents=True, exist_ok=True)
        (evals_dir / "micro" / f"case-{i}.toml").write_text("id = 'x'")
    for i in range(100):  # 100 real runs
        (state_dir / "runs" / f"run-{i:03d}").mkdir(parents=True)
    (state_dir / "promotions").mkdir(parents=True)
    for i in range(3):  # 3 human-approved promotions
        (state_dir / "promotions" / f"p{i}.json").write_text(json.dumps({"approver": f"human-{i}"}))
    (state_dir / "promotions" / "auto-x.json").write_text(
        json.dumps({"approver": "auto-promote"})  # does not count as human
    )
    (state_dir / "evaluator-defects.json").write_text(json.dumps({"unresolved_critical": []}))

    report = readiness(state_dir, evals_dir=evals_dir)
    assert report.green
    assert report.counts.human_approved_promotions == 3


def test_readiness_missing_defects_ledger_fails_closed(tmp_path):
    state_dir = tmp_path / ".pxx"
    evals_dir = tmp_path / "evals"
    evals_dir.mkdir()
    for i in range(50):
        (evals_dir / f"c{i}.toml").write_text("id = 'x'")
    for i in range(100):
        (state_dir / "runs" / f"run-{i:03d}").mkdir(parents=True)
    (state_dir / "promotions").mkdir(parents=True)
    for i in range(3):
        (state_dir / "promotions" / f"p{i}.json").write_text(json.dumps({"approver": "h"}))
    # no evaluator-defects.json: cannot prove zero unresolved defects
    report = readiness(state_dir, evals_dir=evals_dir)
    assert not report.green
    assert "unresolved_critical_defects" in report.unmet


# --- auto_promote ---------------------------------------------------------------------


def _low_candidate():
    return _settings_candidate("memory_retrieval_limit", 8)


FULL_EVIDENCE = Evidence(
    full_pass=True,
    held_out_pass=True,
    adversarial_pass=True,
    canary_pass=True,
    eval_ids=("eval-1", "eval-2"),
    gates={"scope_violation": True},
)


def _verdict(tmp_path, *, candidate=None, evidence=FULL_EVIDENCE, report=None):
    return auto_promote(
        candidate or _low_candidate(),
        evidence,
        readiness_report=report or evaluate_readiness(GREEN),
        state_dir=tmp_path / ".pxx",
    )


def test_green_readiness_low_risk_repeated_wins_promotes(tmp_path):
    verdict = _verdict(tmp_path)
    assert isinstance(verdict, AutoPromoteVerdict)
    assert verdict.promoted
    assert verdict.reasons == ()
    assert verdict.risk == "low"
    assert verdict.rollback_command == ROLLBACK_COMMAND
    # promotion record written with rationale + rollback command
    record_path = Path(verdict.record_path)
    data = json.loads(record_path.read_text())
    assert data["candidate_id"] == "cand-test"
    assert data["approver"] == "auto-promote"
    assert data["rollback_target"] == ROLLBACK_COMMAND
    assert data["eval_ids"] == ["eval-1", "eval-2"]
    assert verdict.rationale == "rationale"


@pytest.mark.parametrize(
    "field,value,bar",
    [
        ("eval_cases", 10, "eval_cases"),
        ("real_runs", 3, "real_runs"),
        ("human_approved_promotions", 0, "human_approved_promotions"),
        ("unresolved_critical_defects", 2, "unresolved_critical_defects"),
    ],
)
def test_refuses_on_every_unmet_bar(tmp_path, field, value, bar):
    report = evaluate_readiness(ReadinessCounts(**{**GREEN.__dict__, field: value}))
    verdict = _verdict(tmp_path, report=report)
    assert not verdict.promoted
    assert any(bar in reason for reason in verdict.reasons)
    assert not (tmp_path / ".pxx" / "promotions").exists()  # nothing written


@pytest.mark.parametrize(
    "candidate",
    [
        _settings_candidate("model", {"model": "m2"}),  # MEDIUM
        _settings_candidate("budgets", {"max_tokens": 9}, baseline_budgets={"max_tokens": 1}),
        _content_candidate("pxx/prompts/native_system.md"),  # MEDIUM
        _content_candidate("pxx/eval/harness.py"),  # HIGH
        _settings_candidate("permissions", "x"),  # HIGH
    ],
)
def test_refuses_medium_and_high_risk_even_when_bars_green(tmp_path, candidate):
    verdict = _verdict(tmp_path, candidate=candidate)
    assert not verdict.promoted
    assert any("risk class" in reason for reason in verdict.reasons)
    assert not (tmp_path / ".pxx" / "promotions").exists()


@pytest.mark.parametrize("field", ["full_pass", "held_out_pass", "adversarial_pass"])
def test_refuses_without_repeated_wins(tmp_path, field):
    evidence = Evidence(**{**FULL_EVIDENCE.__dict__, field: False})
    verdict = _verdict(tmp_path, evidence=evidence)
    assert not verdict.promoted
    assert any("evidence" in reason for reason in verdict.reasons)


def test_evidence_mapping_coerced(tmp_path):
    verdict = _verdict(
        tmp_path,
        evidence={
            "full_pass": True,
            "held_out_pass": True,
            "adversarial_pass": True,
            "canary_pass": True,
        },
    )
    assert verdict.promoted


def test_refusal_reports_what_it_would_do(tmp_path):
    # multiple unmet conditions -> every reason listed, nothing persisted
    report = evaluate_readiness(
        ReadinessCounts(
            eval_cases=1, real_runs=1, human_approved_promotions=0, unresolved_critical_defects=5
        )
    )
    verdict = auto_promote(
        _settings_candidate("model", {"model": "m2"}),
        Evidence(),
        readiness_report=report,
        state_dir=tmp_path / ".pxx",
    )
    assert not verdict.promoted
    assert len(verdict.reasons) == 9  # 1 risk + 4 bars + 4 evidence gaps
    assert not (tmp_path / ".pxx").exists() or not (tmp_path / ".pxx" / "promotions").exists()


# --- B8.3: post-promotion monitoring + auto-rollback; B8.1: commit posture ----------


def test_monitor_healthy_window_no_rollback(tmp_path):
    from pxx.improve.autopromote import monitor_promotion
    from pxx.improve.channels import CandidateRunSignals, Channel, ChannelManager

    manager = ChannelManager(tmp_path / ".pxx")
    manager.activate(Channel.STABLE, "agent-v1")
    manager.activate(Channel.STABLE, "agent-v2")
    verdict = monitor_promotion(manager, CandidateRunSignals())
    assert not verdict.rolled_back
    assert manager.current(Channel.STABLE) == "agent-v2"


def test_monitor_regression_auto_rolls_back(tmp_path):
    from pxx.improve.autopromote import monitor_promotion
    from pxx.improve.channels import (
        CandidateRunSignals,
        Channel,
        ChannelManager,
    )

    manager = ChannelManager(tmp_path / ".pxx")
    manager.activate(Channel.STABLE, "agent-v1")
    manager.activate(Channel.STABLE, "agent-v2")
    verdict = monitor_promotion(manager, CandidateRunSignals(reviewer_availability=0.1))
    assert verdict.rolled_back
    assert verdict.restored == "agent-v1"
    assert "reviewer_availability_drop" in verdict.tripped
    assert "auto-rollback" in verdict.reason
    assert manager.current(Channel.STABLE) == "agent-v1"
    # the rollback is history-visible with the recorded reason
    history = manager.history()
    assert any(e.action == "auto-rollback" and "regression" in e.detail for e in history)


def test_commit_false_reports_would_promote_without_writing(tmp_path):
    """Default posture: all bars green + commit=False -> would_promote,
    nothing persisted."""
    candidate = _settings_candidate("memory_retrieval_limit", 4)
    evidence = Evidence(
        full_pass=True,
        held_out_pass=True,
        adversarial_pass=True,
        canary_pass=True,
        eval_ids=("e1",),
    )
    report = evaluate_readiness(
        ReadinessCounts(
            eval_cases=50,
            real_runs=100,
            human_approved_promotions=3,
            unresolved_critical_defects=0,
        )
    )
    verdict = auto_promote(
        candidate,
        evidence,
        readiness_report=report,
        state_dir=tmp_path / ".pxx",
        commit=False,
    )
    assert not verdict.promoted
    assert verdict.would_promote
    assert not (tmp_path / ".pxx" / "promotions").exists()
