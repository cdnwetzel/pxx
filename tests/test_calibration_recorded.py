"""RecordedReviewer — score an EXTERNAL reviewer through the production path.

CodeRabbit, Greptile, and Copilot review pull requests, not diffs handed to an
API, so they cannot implement the Reviewer protocol live. These tests lock the
replay adapter that lets their captured output be scored by the SAME
``run_calibration`` / ``parse_review`` / threshold machinery the sovereign
reviewer is scored by — the whole point being that no separate scoring path
exists to drift or to flatter an external tool.

The controls matter most. A bench that cannot FAIL a reviewer measures nothing,
so `always_approve` and `always_revise` must both breach.
"""

from __future__ import annotations

import asyncio

import pytest

from pxx.calibration import (
    MAX_FP_RATE,
    MIN_AGREEMENT,
    MIN_RECALL,
    CaptureMissing,
    Expect,
    RecordedReviewer,
    breaches,
    load_cases,
    run_calibration,
)
from pxx.errors import ConfigError

CORPUS = "evals/calibration"


@pytest.fixture(scope="module")
def cases():
    return load_cases(CORPUS)


def score(cases, responses):
    reviewer = RecordedReviewer.from_cases(cases, responses)
    return asyncio.run(run_calibration(reviewer, cases))


def approve() -> str:
    return "VERDICT: APPROVE"


def revise(severity: str = "high") -> str:
    return f"VERDICT: REVISE\nF-001 [{severity}] app/x.py:1 problem found"


# ---- the adapter itself --------------------------------------------------------------


def test_replays_recorded_text_verbatim(cases):
    reviewer = RecordedReviewer.from_cases(cases, {cases[0].id: "VERDICT: APPROVE"})
    assert asyncio.run(reviewer.review(cases[0].diff, cases[0].task)) == "VERDICT: APPROVE"


def test_unknown_case_id_is_a_config_error(cases):
    """A capture for a case not in the corpus means the bench and the corpus have
    diverged; scoring it anyway would silently measure the wrong thing."""
    with pytest.raises(ConfigError, match="unknown case ids"):
        RecordedReviewer.from_cases(cases, {"not-a-real-case": approve()})


def test_missing_capture_raises_rather_than_defaulting(cases):
    reviewer = RecordedReviewer.from_cases(cases, {})
    with pytest.raises(CaptureMissing):
        asyncio.run(reviewer.review(cases[0].diff, cases[0].task))


# ---- scoring through the production path ---------------------------------------------


def test_a_perfect_reviewer_scores_perfectly(cases):
    responses = {c.id: (revise() if c.expect is Expect.FLAG else approve()) for c in cases}
    report = score(cases, responses)
    assert report.recall == 1.0
    assert report.fp_rate == 0.0
    assert report.ok is True
    assert breaches(report) == []


def test_min_severity_is_enforced_for_flag_cases(cases):
    """Flagging a hardcoded-secret case with a 'low' finding is not catching it.
    The corpus sets min_severity per case and the replay path must honour it."""
    high_bar = [c for c in cases if c.expect is Expect.FLAG and c.min_severity == "high"]
    assert high_bar, "corpus should contain high-severity flag cases"
    responses = {c.id: (revise("low") if c in high_bar else revise()) for c in cases}
    responses.update({c.id: approve() for c in cases if c.expect is Expect.CLEAN})
    report = score(cases, responses)
    failed = {r.case_id for r in report.results if not r.passed}
    assert {c.id for c in high_bar} <= failed


# ---- negative controls: the bench must be able to FAIL a reviewer --------------------


def test_always_approve_fails_calibration(cases):
    """The rubber-stamp reviewer. Catches nothing, so recall must floor and the
    report must breach — otherwise the bench would certify a reviewer that
    approves a hardcoded secret."""
    report = score(cases, {c.id: approve() for c in cases})
    assert report.recall == 0.0
    assert report.fp_rate == 0.0  # it never flags, so no false positives
    assert report.ok is False
    assert any("recall" in b for b in breaches(report))


def test_always_revise_fails_calibration(cases):
    """The opposite failure: flag everything. Perfect recall, useless in
    practice, and the false-positive threshold must catch it."""
    report = score(cases, {c.id: revise() for c in cases})
    assert report.recall == 1.0
    assert report.fp_rate == 1.0
    assert report.ok is False
    assert any("fp_rate" in b for b in breaches(report))


def test_silence_is_not_approval(cases):
    """A reviewer that said nothing about a case must score as unavailable and
    flagged, never as a pass. This is the shape an external reviewer takes when
    it is rate-limited or its app is down — the exact case where a bench that
    treated absence as approval would certify a reviewer that never ran."""
    responses = {c.id: approve() for c in cases if c.expect is Expect.CLEAN}
    report = score(cases, responses)  # every FLAG case missing
    missing = [r for r in report.results if r.expect is Expect.FLAG]
    assert all(not r.available for r in missing)
    assert all(r.flagged for r in missing)
    assert report.availability < 1.0
    assert report.ok is False


def test_thresholds_are_the_published_ones(cases):
    """Guards against the bench being quietly loosened to let a tool through."""
    assert MIN_RECALL == 0.75
    assert MAX_FP_RATE == 0.25
    # MIN_AGREEMENT is part of the published bar too; locking only two of the
    # three would leave a way to loosen the benchmark unnoticed.
    assert MIN_AGREEMENT == 0.75


def test_duplicate_diffs_are_rejected_not_silently_merged(cases):
    """Replay identity is the diff (the Reviewer protocol passes no case id), so
    two cases sharing a diff would be last-write-wins and would replay one case's
    response for the other. Reject the ambiguity instead of resolving it."""
    from dataclasses import replace as _replace

    clash = _replace(cases[1], id="clashing-case", diff=cases[0].diff)
    with pytest.raises(ConfigError, match="identical diff"):
        RecordedReviewer.from_cases([*cases, clash], {})
