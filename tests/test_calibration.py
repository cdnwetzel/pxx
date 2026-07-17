"""Tests for pxx.calibration — reviewer scoring against ground truth (#014.3)."""

from __future__ import annotations

from pxx import calibration
from pxx.calibration import (
    CalibrationCase,
    judge_response,
    load_calibration_cases,
    run_calibration,
)


def _case(kind="clean", cid="c1"):
    return CalibrationCase(
        id=cid,
        kind=kind,
        description="",
        diff="diff\n+x",
        task=None,
        expected_severity=None,
        path=calibration.CALIBRATION_DIR / "x.toml",
    )


class TestJudgeResponse:
    def test_clean_with_no_findings_line_is_correct_and_compliant(self):
        v = judge_response(_case("clean"), "# Review pass: no findings.")
        assert v.correct and v.format_compliant and v.findings == 0

    def test_clean_with_findings_is_false_positive(self):
        v = judge_response(
            _case("clean"), "### F-001 — bogus issue in a.py:3 (P1, state: open)"
        )
        assert not v.correct and v.findings == 1 and v.format_compliant

    def test_defect_flagged_is_correct(self):
        v = judge_response(
            _case("defect"), "### F-001 — real bug in a.py:3 (P0, state: open)"
        )
        assert v.correct and v.format_compliant

    def test_defect_missed_is_incorrect(self):
        v = judge_response(_case("defect"), "# Review pass: no findings.")
        assert not v.correct

    def test_prose_without_contract_is_noncompliant(self):
        v = judge_response(_case("clean"), "Looks fine to me, nice work!")
        assert v.correct  # no findings parsed -> clean verdict holds
        assert not v.format_compliant  # but the contract was not honored


class TestShippedCalibrationCorpus:
    def test_corpus_loads_and_is_balanced(self):
        cases = load_calibration_cases()
        kinds = [c.kind for c in cases]
        assert len(cases) == 20
        assert kinds.count("clean") == 12 and kinds.count("defect") == 8

    def test_r5_regression_case_is_permanent(self):
        ids = [c.id for c in load_calibration_cases()]
        assert "cal-clean-r5-exit-codes" in ids


class TestRunCalibration:
    def test_metrics_from_scripted_reviewer(self, monkeypatch):
        cases = [
            _case("defect", "d1"),
            _case("defect", "d2"),
            _case("clean", "c1"),
            _case("clean", "c2"),
        ]
        responses = {
            "d1": "### F-001 — bug in a.py:1 (P0, state: open)",
            "d2": "# Review pass: no findings.",  # miss
            "c1": "# Review pass: no findings.",
            "c2": "### F-001 — imaginary in b.py:2 (P1, state: open)",  # FP
        }
        calls = iter(cases)

        def fake_post(url, model, prompt, timeout):
            return responses[next(calls).id]

        monkeypatch.setattr("pxx.review_gate._post_chat", fake_post)
        report = run_calibration(cases)
        assert report.recall == 0.5
        assert report.false_positive_rate == 0.5
        assert report.availability == 1.0
        assert not report.within_thresholds

    def test_unreachable_reviewer_counts_against_availability(self, monkeypatch):
        def boom(*a, **k):
            raise OSError("down")

        monkeypatch.setattr("pxx.review_gate._post_chat", boom)
        report = run_calibration([_case("defect", "d1")])
        assert report.availability == 0.0
        assert report.recall == 0.0
