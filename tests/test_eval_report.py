"""Tests for pxx.eval.report: fingerprints, scorecards, fail-closed compare."""

from __future__ import annotations

from pathlib import Path

from pxx.eval.cases import Case, Tier, load_cases
from pxx.eval.report import (
    CaseVerdict,
    build_scorecard,
    compare,
    compute_gates,
    corpus_fingerprint,
    render,
)

CORPUS = Path(__file__).resolve().parent.parent / "evals"


def make_case(case_id: str, task: str = "t") -> Case:
    return Case(
        id=case_id,
        tier=Tier.MICRO,
        task=task,
        honest_patch="h",
        cheat_patch="c",
    )


def corpus() -> list[Case]:
    cases: list[Case] = []
    for tier in ("micro", "regression", "adversarial"):
        cases.extend(load_cases(CORPUS / tier))
    return cases


# --- corpus fingerprint -------------------------------------------------------


def test_fingerprint_stable_and_order_independent():
    cases = corpus()
    assert corpus_fingerprint(cases) == corpus_fingerprint(list(reversed(cases)))
    assert corpus_fingerprint(cases) == corpus_fingerprint(corpus())


def test_fingerprint_changes_with_corpus():
    cases = corpus()
    changed = [*corpus(), make_case("extra-case")]
    assert corpus_fingerprint(changed) != corpus_fingerprint(cases)
    modified = [c for c in cases if c.id != cases[0].id]
    assert corpus_fingerprint(modified) != corpus_fingerprint(cases)


# --- scorecard ------------------------------------------------------------------


def test_build_scorecard_totals_and_ordering():
    cases = [make_case("b"), make_case("a"), make_case("c")]
    verdicts = [
        CaseVerdict(case_id="c", passed=True),
        CaseVerdict(case_id="a", passed=False, failed_checks=("command:x",)),
        CaseVerdict(case_id="b", passed=True),
    ]
    card = build_scorecard("agent-1", cases, verdicts)
    assert card.agent_version_id == "agent-1"
    assert card.corpus_fingerprint == corpus_fingerprint(cases)
    assert [v.case_id for v in card.verdicts] == ["a", "b", "c"]
    assert (card.passed, card.failed, card.total) == (2, 1, 3)


def test_render_is_deterministic():
    cases = [make_case("b"), make_case("a")]
    verdicts = [
        CaseVerdict(case_id="a", passed=False, failed_checks=("allowed_files",)),
        CaseVerdict(case_id="b", passed=True),
    ]
    first = render(build_scorecard("agent-1", cases, verdicts))
    second = render(build_scorecard("agent-1", cases, list(reversed(verdicts))))
    assert first == second
    assert "a: fail" in first
    assert "  failed_check: allowed_files" in first
    assert first.endswith("\n")


# --- compare ---------------------------------------------------------------------


def scorecard(agent: str, cases: list[Case], failing: set[str]):
    verdicts = [
        CaseVerdict(
            case_id=c.id,
            passed=c.id not in failing,
            failed_checks=("command:x",) if c.id in failing else (),
        )
        for c in cases
    ]
    return build_scorecard(agent, cases, verdicts)


def test_compare_refuses_mismatched_corpora():
    baseline = scorecard("base", [make_case("a"), make_case("b")], set())
    candidate = scorecard("cand", [make_case("a"), make_case("other")], set())
    result = compare(baseline, candidate)
    assert result.ok is False
    assert "fingerprint mismatch" in result.reason


def test_compare_gained_and_lost_on_matched_corpora():
    cases = [make_case(c) for c in ("a", "b", "c", "d")]
    baseline = scorecard("base", cases, failing={"b", "c"})
    candidate = scorecard("cand", cases, failing={"c", "d"})
    result = compare(baseline, candidate)
    assert result.ok
    assert result.reason == ""
    assert result.gained == ("b",)
    assert result.lost == ("d",)


def test_compare_identical_scorecards():
    cases = [make_case(c) for c in ("a", "b")]
    baseline = scorecard("base", cases, failing={"a"})
    candidate = scorecard("cand", cases, failing={"a"})
    result = compare(baseline, candidate)
    assert result.ok
    assert result.gained == ()
    assert result.lost == ()


# --- compute_gates (F1: real hard-gate evidence, fail closed) ------------------


def _gate_result(case_id: str, passed: bool, ok_checks=(), bad_checks=()):
    from pxx.eval.harness import CaseResult, CheckResult

    return CaseResult(
        case_id=case_id,
        passed=passed,
        checks=tuple(
            [CheckResult(name=n, ok=True) for n in ok_checks]
            + [CheckResult(name=n, ok=False, detail="x") for n in bad_checks]
        ),
    )


def _rich_case(case_id: str, tier: Tier) -> Case:
    from pxx.eval.cases import Checks, Fixture

    return Case(
        id=case_id,
        tier=tier,
        task="t",
        fixture=Fixture(files=(("check.py", "x"),), starting_tests=("check.py",)),
        checks=Checks(
            commands=("{python} check.py",),
            allowed_files=("calc.py",),
            tests_unchanged=True,
            hidden_commands=("{python} hidden.py",),
        ),
        honest_patch="h",
        cheat_patch="c",
    )


def test_compute_gates_all_held_on_full_green_evidence():
    case = _rich_case("adv-1", Tier.ADVERSARIAL)
    result = _gate_result(
        "adv-1",
        True,
        ok_checks=("allowed_files", "tests_unchanged:check.py", "command:{python} hidden.py"),
    )
    gates = compute_gates([case], [result])
    assert gates == {
        "adversarial_containment": True,
        "scope_violation": True,
        "evaluator_fixture_modification": True,
        "permission_expansion": False,  # no evidence producer yet — fail closed
        "test_deletion_weakening": True,
    }


def test_compute_gates_fail_closed_without_evidence():
    result = _gate_result("m1", True)  # micro case with no checks at all
    gates = compute_gates([make_case("m1")], [result])
    assert not any(gates.values())  # zero evidence -> every gate False


def test_compute_gates_scope_and_test_weakening_detected():
    case = _rich_case("m1", Tier.MICRO)
    result = _gate_result(
        "m1",
        False,
        ok_checks=("command:{python} hidden.py",),
        bad_checks=("allowed_files", "tests_unchanged:check.py"),
    )
    gates = compute_gates([case], [result])
    assert gates["scope_violation"] is False
    assert gates["test_deletion_weakening"] is False
    assert gates["evaluator_fixture_modification"] is False
    assert gates["adversarial_containment"] is False  # no adversarial cases ran


def test_compute_gates_adversarial_failure_breaks_containment():
    case = _rich_case("adv-1", Tier.ADVERSARIAL)
    result = _gate_result(
        "adv-1",
        False,
        ok_checks=("allowed_files", "tests_unchanged:check.py"),
        bad_checks=("command:{python} hidden.py",),
    )
    gates = compute_gates([case], [result])
    assert gates["adversarial_containment"] is False
    assert gates["scope_violation"] is True


# --- B3.2/B3.3: per-family scoring + held-out partition -----------------------------


def test_scorecard_per_family_breakdown() -> None:
    from pxx.eval.cases import Family

    cases = [
        Case(id="cap-1", tier=Tier.MICRO, task="t", family=Family.CAPABILITY),
        Case(id="saf-1", tier=Tier.ADVERSARIAL, task="t", family=Family.SAFETY),
        Case(id="saf-2", tier=Tier.ADVERSARIAL, task="t", family=Family.SAFETY),
    ]
    verdicts = [
        CaseVerdict(case_id="cap-1", passed=True),
        CaseVerdict(case_id="saf-1", passed=True),
        CaseVerdict(case_id="saf-2", passed=False),
    ]
    card = build_scorecard("agent-x", cases, verdicts, partition="held-out")
    assert card.families == {"capability": (1, 1), "safety": (1, 2)}
    assert card.partition == "held-out"
    rendered = render(card)
    assert "partition: held-out" in rendered
    assert "family safety: 1/2" in rendered


def test_compare_refuses_development_only_candidate() -> None:
    from pxx.eval.report import compare as report_compare

    cases = [make_case("c1"), make_case("c2")]
    baseline = build_scorecard(
        "a",
        cases,
        [CaseVerdict(case_id="c1", passed=True), CaseVerdict(case_id="c2", passed=False)],
    )
    dev_candidate = build_scorecard(
        "b",
        cases,
        [CaseVerdict(case_id="c1", passed=True), CaseVerdict(case_id="c2", passed=True)],
        partition="dev",
    )
    result = report_compare(baseline, dev_candidate)
    assert not result.ok
    assert "held-out" in result.reason

    held_out_candidate = build_scorecard(
        "b",
        cases,
        [CaseVerdict(case_id="c1", passed=True), CaseVerdict(case_id="c2", passed=True)],
        partition="held-out",
    )
    result = report_compare(baseline, held_out_candidate)
    assert result.ok and result.gained == ("c2",)
