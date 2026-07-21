"""Calibration suite tests: scoring math, thresholds, corpus loading."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pxx.calibration import (
    MAX_FP_RATE,
    MIN_AVAILABILITY,
    MIN_FORMAT_COMPLIANCE,
    MIN_RECALL,
    Case,
    CaseKind,
    Expect,
    breaches,
    load_case,
    load_cases,
    run_calibration,
)
from pxx.errors import ConfigError
from pxx.review import Verdict

CORPUS_DIR = Path(__file__).resolve().parent.parent / "evals" / "calibration"

REVISE_HIGH = "VERDICT: REVISE\nF-001 [high] app/x.py:1 known defect"
APPROVE = "VERDICT: APPROVE"
JUNK = "well hmm let me think about this diff... maybe fine? {{{"


class ScriptedReviewer:
    """Fake reviewer returning canned responses keyed by exact diff text."""

    def __init__(self, responses: dict[str, str], default: str = APPROVE) -> None:
        self._responses = responses
        self._default = default
        self.calls = 0

    async def review(self, diff: str, task: str) -> str:
        self.calls += 1
        return self._responses.get(diff, self._default)


class JunkReviewer:
    async def review(self, diff: str, task: str) -> str:
        return JUNK


class RaisingReviewer:
    async def review(self, diff: str, task: str) -> str:
        raise RuntimeError("reviewer backend down")


def perfect_reviewer(cases: tuple[Case, ...]) -> ScriptedReviewer:
    responses = {c.diff: REVISE_HIGH if c.expect is Expect.FLAG else APPROVE for c in cases}
    return ScriptedReviewer(responses)


def corpus() -> tuple[Case, ...]:
    return load_cases(CORPUS_DIR)


def run(reviewer, cases) -> object:
    return asyncio.run(run_calibration(reviewer, cases))


# --- corpus loading ----------------------------------------------------------


def test_corpus_loads():
    cases = corpus()
    assert len(cases) == 14
    assert len({c.id for c in cases}) == 14
    kinds = {c.kind for c in cases}
    assert {CaseKind.CRITICAL, CaseKind.ACCEPTABLE, CaseKind.NOISY, CaseKind.EDGE} <= kinds
    by_id = {c.id: c for c in cases}
    assert by_id["critical-sql-injection"].min_severity == "high"
    assert by_id["critical-sql-injection"].expect is Expect.FLAG
    assert by_id["edge-empty-diff"].diff == ""
    assert by_id["edge-empty-diff"].expect is Expect.CLEAN
    # flag/clean split used by the metric math below
    assert sum(1 for c in cases if c.expect is Expect.FLAG) == 7
    assert sum(1 for c in cases if c.expect is Expect.CLEAN) == 7


def test_corpus_is_metadata_only_and_sorted():
    cases = corpus()
    ids = [c.id for c in cases]
    assert ids == sorted(ids)  # filenames sort to id order


def test_load_case_missing_field_raises(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text('id = "x"\nkind = "critical"\nexpect = "flag"\n')
    with pytest.raises(ConfigError):
        load_case(bad)


def test_load_case_unknown_kind_raises(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text('id = "x"\nkind = "spicy"\ndiff = ""\ntask = "t"\nexpect = "flag"\n')
    with pytest.raises(ConfigError):
        load_case(bad)


def test_load_case_bad_min_severity_raises(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text(
        'id = "x"\nkind = "critical"\ndiff = ""\ntask = "t"\n'
        'expect = "flag"\nmin_severity = "severe"\n'
    )
    with pytest.raises(ConfigError):
        load_case(bad)


def test_load_case_malformed_toml_raises(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text("this is = = not toml [")
    with pytest.raises(ConfigError):
        load_case(bad)


def test_load_cases_empty_dir_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_cases(tmp_path)


# --- scoring math ------------------------------------------------------------


def test_perfect_reviewer_passes_all_thresholds():
    cases = corpus()
    reviewer = perfect_reviewer(cases)
    report = run(reviewer, cases)
    assert report.recall == 1.0
    assert report.fp_rate == 0.0
    assert report.format_compliance == 1.0
    assert report.availability == 1.0
    assert all(r.passed for r in report.results)
    assert breaches(report) == []
    assert report.ok
    assert reviewer.calls == len(cases)


def test_flag_everything_reviewer_breaches_fp_rate():
    reviewer = ScriptedReviewer({}, default=REVISE_HIGH)
    report = run(reviewer, corpus())
    assert report.recall == 1.0
    assert report.fp_rate == 1.0
    assert report.availability == 1.0
    assert not report.ok
    names = breaches(report)
    joined = "; ".join(names)
    assert "fp_rate" in joined and str(MAX_FP_RATE) in joined
    assert "agreement" in joined  # 0.5 < MIN_AGREEMENT also breaches


def test_junk_reviewer_fails_closed_and_breaches_format():
    report = run(JunkReviewer(), corpus())
    # fail closed: junk parses to NO_REVIEW, which flags (blocks) every change
    assert all(r.flagged for r in report.results)
    assert all(r.verdict is Verdict.NO_REVIEW for r in report.results)
    assert not any(r.parseable for r in report.results)
    assert report.availability == 0.0
    assert report.format_compliance == 0.0
    # flag cases without min_severity still "pass" (defect not approved);
    # min_severity cases fail because junk carries no findings
    by_id = {r.case_id: r for r in report.results}
    assert by_id["edge-wrong-line-number"].passed
    assert not by_id["critical-sql-injection"].passed
    assert not report.ok
    names = " ".join(breaches(report))
    assert "format_compliance" in names
    assert "availability" in names


def test_raising_reviewer_never_crashes_and_breaches_availability():
    report = run(RaisingReviewer(), corpus())
    assert all(not r.available for r in report.results)
    assert report.availability == 0.0
    assert report.format_compliance == 0.0
    assert not report.ok
    assert any("availability" in b for b in breaches(report))


def test_partial_recall_breach_names_recall():
    cases = corpus()
    responses = {
        c.diff: REVISE_HIGH if c.id == "critical-sql-injection" else APPROVE for c in cases
    }
    report = run(ScriptedReviewer(responses), cases)
    flag_total = sum(1 for c in cases if c.expect is Expect.FLAG)
    assert report.recall == 1 / flag_total < MIN_RECALL
    assert report.fp_rate == 0.0
    assert not report.ok
    assert any("recall" in b for b in breaches(report))


def test_one_exception_counts_against_availability_only():
    cases = corpus()

    class FlakyReviewer:
        def __init__(self) -> None:
            self._inner = perfect_reviewer(cases)

        async def review(self, diff: str, task: str) -> str:
            if diff == "":  # the empty-diff clean case
                raise RuntimeError("boom")
            return await self._inner.review(diff, task)

    report = run(FlakyReviewer(), cases)
    assert report.availability == pytest.approx(13 / 14)
    assert report.format_compliance == 1.0  # all answered calls parsed
    assert report.recall == 1.0
    # the crashed clean case fails closed -> flagged -> one false positive
    assert report.fp_rate == 1 / 7
    assert report.ok  # 0.25 is not above MAX_FP_RATE; 7/8 >= MIN_AVAILABILITY
    assert report.availability >= MIN_AVAILABILITY
    assert report.format_compliance >= MIN_FORMAT_COMPLIANCE


def test_min_severity_gates_flag_cases():
    case = Case(
        id="c1",
        kind=CaseKind.CRITICAL,
        diff="d",
        task="t",
        expect=Expect.FLAG,
        min_severity="high",
    )
    low_flag = ScriptedReviewer({"d": "VERDICT: REVISE\nF-001 [low] a.py:1 nit"})
    report = run(low_flag, [case])
    assert report.results[0].flagged
    assert not report.results[0].passed
    assert report.recall == 0.0
    report = run(ScriptedReviewer({"d": REVISE_HIGH}), [case])
    assert report.results[0].passed
    assert report.recall == 1.0


def test_wrong_line_number_tolerance():
    cases = corpus()
    edge = next(c for c in cases if c.id == "edge-wrong-line-number")
    responses = {
        c.diff: (
            "VERDICT: REVISE\nF-001 [medium] totally/wrong.py:999 drops first result"
            if c is edge
            else (REVISE_HIGH if c.expect is Expect.FLAG else APPROVE)
        )
        for c in cases
    }
    report = run(ScriptedReviewer(responses), cases)
    by_id = {r.case_id: r for r in report.results}
    result = by_id["edge-wrong-line-number"]
    assert result.passed
    assert result.findings[0].line == 999  # parsed, but not matched against
    assert report.ok


def test_malformed_kind_case_fails_closed():
    case = Case(
        id="m1",
        kind=CaseKind.MALFORMED,
        diff="d",
        task="t",
        expect=Expect.FLAG,
    )
    report = run(JunkReviewer(), [case])
    result = report.results[0]
    assert result.verdict is Verdict.NO_REVIEW
    assert result.flagged  # fail closed, never silently approved
    assert result.passed  # no min_severity: flag expectation met
    assert not result.parseable
    assert not report.ok  # availability/format breach


def test_breaches_of_empty_report_is_clean():
    report = run(ScriptedReviewer({}), [])
    assert report.recall == 1.0
    assert report.fp_rate == 0.0
    assert report.availability == 1.0
    assert report.ok
    assert breaches(report) == []


# --- M0 regression: F6 (lopsided corpus warns instead of passing silently) -------


def test_lopsided_corpus_warns_vacuous_dimensions():
    """A corpus missing one expectation class makes that dimension vacuous —
    the report must say so loudly, not pass the missing dimension silently."""
    import asyncio

    from pxx.calibration import Case, CaseKind, Expect, run_calibration

    cases = [
        Case(
            id="flag-only",
            kind=CaseKind.CRITICAL,
            diff="d",
            task="t",
            expect=Expect.FLAG,
            min_severity=None,
        )
    ]
    report = asyncio.run(run_calibration(ScriptedReviewer({}), cases))
    assert any("false-positive dimension is vacuous" in w for w in report.warnings)
    assert not any("recall dimension is vacuous" in w for w in report.warnings)


# --- B3.5: verdict agreement metric -------------------------------------------------


def test_agreement_metric_tracks_ground_truth():
    """Agreement = fraction of cases where the reviewer's flag/clean call was
    correct. A perfect reviewer agrees 1.0; flag-everything agrees partially."""
    cases = corpus()
    perfect = run(perfect_reviewer(cases), cases)
    assert perfect.agreement == 1.0

    everything = run(ScriptedReviewer({}, default=REVISE_HIGH), cases)
    clean_total = sum(1 for c in cases if c.expect.value == "clean")
    expected = clean_total / len(cases)  # only the false positives are wrong
    assert everything.agreement == expected
    assert everything.agreement < 1.0


def test_agreement_breach_named():
    from pxx.calibration import MIN_AGREEMENT, CalibrationReport, breaches

    report = CalibrationReport(
        recall=1.0,
        fp_rate=0.0,
        format_compliance=1.0,
        availability=1.0,
        results=(),
        agreement=0.5,
    )
    names = breaches(report)
    assert any("agreement" in n and str(MIN_AGREEMENT) in n for n in names)
