"""Phase 18 tests: channels (activate/rollback/history persistence),
shadow runs (isolation, scoring, never merged, stable untouched), and
circuit breakers (immediate retire + best-effort audit)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pxx.improve.channels import (
    Breaker,
    CandidateRunSignals,
    Channel,
    ChannelManager,
    evaluate_candidate_run,
    shadow_run,
    tripped_breakers,
)


def manager(tmp_path, **kw):
    return ChannelManager(tmp_path / ".pxx", **kw)


# --- activate / history / persistence ------------------------------------------


def test_activate_and_current_persisted(tmp_path):
    m = manager(tmp_path)
    m.activate(Channel.STABLE, "agent-v1")
    m.activate(Channel.CANDIDATE, "agent-v2")
    assert m.current(Channel.STABLE) == "agent-v1"
    assert m.current("candidate") == "agent-v2"

    reloaded = manager(tmp_path)  # state survives a fresh manager
    assert reloaded.current(Channel.STABLE) == "agent-v1"
    assert reloaded.current(Channel.CANDIDATE) == "agent-v2"

    history = reloaded.history()
    assert [e.action for e in history] == ["activate", "activate"]
    assert history[0].channel == "stable"
    assert history[0].agent_version_id == "agent-v1"


def test_retired_is_not_assignable(tmp_path):
    m = manager(tmp_path)
    with pytest.raises(ValueError, match="retired"):
        m.activate(Channel.RETIRED, "agent-x")


# --- rollback under a simulated bad promotion -----------------------------------


def test_rollback_restores_exact_previous_stable(tmp_path):
    m = manager(tmp_path)
    m.activate(Channel.STABLE, "agent-v1")
    m.activate(Channel.CANDIDATE, "agent-v2")
    # simulated bad promotion: candidate is activated as stable
    m.activate(Channel.STABLE, "agent-v2")
    assert m.current(Channel.STABLE) == "agent-v2"

    previous = m.rollback()
    assert previous == "agent-v1"  # EXACT previous stable restored
    assert m.current(Channel.STABLE) == "agent-v1"
    # candidate channel assignment is untouched by the rollback
    assert m.current(Channel.CANDIDATE) == "agent-v2"

    reloaded = manager(tmp_path)
    assert reloaded.current(Channel.STABLE) == "agent-v1"
    actions = [(e.action, e.agent_version_id) for e in reloaded.history()]
    assert ("rollback", "agent-v1") in actions


def test_rollback_twice_walks_the_stack(tmp_path):
    m = manager(tmp_path)
    m.activate(Channel.STABLE, "v1")
    m.activate(Channel.STABLE, "v2")
    m.activate(Channel.STABLE, "v3")
    assert m.rollback() == "v2"
    assert m.rollback() == "v1"
    assert m.rollback() is None  # nothing left to roll back to
    assert m.current(Channel.STABLE) == "v1"


def test_rollback_without_previous_returns_none(tmp_path):
    m = manager(tmp_path)
    m.activate(Channel.STABLE, "v1")
    assert m.rollback() is None
    assert m.current(Channel.STABLE) == "v1"


def test_uses_injected_clock(tmp_path):
    m = manager(tmp_path, clock=lambda: datetime(2026, 7, 18, tzinfo=UTC))
    m.activate(Channel.STABLE, "v1")
    assert m.history()[0].ts.startswith("2026-07-18")


# --- shadow runs ------------------------------------------------------------------


def _tree_snapshot(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): p.read_text() for p in sorted(root.rglob("*")) if p.is_file()}


def _writing_backend(filename: str, text: str):
    def run(task: str, cwd: Path) -> str:
        (cwd / filename).write_text(text)
        return f"did {task}"

    return run


def test_shadow_run_candidate_isolated_never_merged(tmp_path):
    worktree = tmp_path / "repo"
    worktree.mkdir()
    (worktree / "app.py").write_text("original\n")
    before = _tree_snapshot(worktree)

    report = shadow_run(
        "fix the bug",
        _writing_backend("stable_out.txt", "stable was here\n"),
        _writing_backend("candidate_out.txt", "candidate was here\n"),
        worktree,
        scorer=lambda result: 0.75,
    )

    # stable did the real task in the main worktree
    assert (worktree / "stable_out.txt").read_text() == "stable was here\n"
    # candidate output exists ONLY in the isolated worktree, never merged
    assert not (worktree / "candidate_out.txt").exists()
    assert (Path(report.candidate_worktree) / "candidate_out.txt").exists()
    assert report.merged is False
    assert report.candidate_score == 0.75
    # main worktree otherwise untouched (only stable's own change added)
    after = _tree_snapshot(worktree)
    assert after == {**before, "stable_out.txt": "stable was here\n"}


def test_shadow_run_candidate_error_scores_zero(tmp_path):
    worktree = tmp_path / "repo"
    worktree.mkdir()
    (worktree / "app.py").write_text("original\n")

    def broken(task: str, cwd: Path) -> str:
        raise RuntimeError("candidate exploded")

    report = shadow_run("task", _writing_backend("s.txt", "ok"), broken, worktree)
    assert report.candidate_score == 0.0
    assert report.candidate_summary == "RuntimeError"  # metadata-only
    assert (worktree / "s.txt").exists()
    assert report.merged is False


def test_shadow_run_does_not_touch_channels_or_stable_config(tmp_path):
    m = manager(tmp_path)
    m.activate(Channel.STABLE, "agent-v1")
    m.activate(Channel.CANDIDATE, "agent-v2")
    channels_before = (tmp_path / ".pxx" / "channels.json").read_bytes()

    worktree = tmp_path / "repo"
    worktree.mkdir()
    (worktree / "app.py").write_text("original\n")
    shadow_run("task", _writing_backend("s.txt", "ok"), _writing_backend("c.txt", "ok"), worktree)

    # stable config immutable during candidate runs: channels file unchanged
    assert (tmp_path / ".pxx" / "channels.json").read_bytes() == channels_before
    assert m.current(Channel.STABLE) == "agent-v1"


# --- circuit breakers --------------------------------------------------------------


@pytest.mark.parametrize(
    "signals,expected",
    [
        (CandidateRunSignals(scope_violation=True), (Breaker.SCOPE_VIOLATION,)),
        (
            CandidateRunSignals(evaluator_critical_failure=True),
            (Breaker.EVALUATOR_CRITICAL_FAILURE,),
        ),
        (CandidateRunSignals(budget_overrun=True), (Breaker.BUDGET_OVERRUN,)),
        (
            CandidateRunSignals(unexpected_files=("secrets.txt",)),
            (Breaker.UNEXPECTED_FILES,),
        ),
        (
            CandidateRunSignals(scope_violation=True, budget_overrun=True),
            (Breaker.SCOPE_VIOLATION, Breaker.BUDGET_OVERRUN),
        ),
        (CandidateRunSignals(), ()),
    ],
)
def test_tripped_breakers_pure(signals, expected):
    assert tripped_breakers(signals) == expected


@pytest.mark.parametrize(
    "signals",
    [
        CandidateRunSignals(scope_violation=True),
        CandidateRunSignals(evaluator_critical_failure=True),
        CandidateRunSignals(budget_overrun=True),
        CandidateRunSignals(unexpected_files=("x.py",)),
    ],
)
def test_breaker_retires_candidate_immediately_with_audit(tmp_path, signals):
    audit_events: list[tuple[str, dict]] = []
    m = manager(tmp_path, audit=lambda kind, data: audit_events.append((kind, data)))
    m.activate(Channel.STABLE, "agent-v1")
    m.activate(Channel.CANDIDATE, "agent-v2")

    tripped = evaluate_candidate_run(m, signals)

    assert tripped  # at least one breaker fired
    assert m.current(Channel.CANDIDATE) is None  # disabled immediately
    assert "agent-v2" in m.retired()
    assert m.current(Channel.STABLE) == "agent-v1"  # stable untouched
    # audit event recorded (metadata-only)
    assert len(audit_events) == 1
    kind, data = audit_events[0]
    assert kind == "candidate_retired"
    assert data["agent_version_id"] == "agent-v2"
    assert "circuit breaker" in data["reason"]

    reloaded = manager(tmp_path)  # retirement is durable
    assert "agent-v2" in reloaded.retired()
    assert reloaded.current(Channel.CANDIDATE) is None


def test_clean_candidate_run_keeps_candidate_active(tmp_path):
    m = manager(tmp_path)
    m.activate(Channel.CANDIDATE, "agent-v2")
    assert evaluate_candidate_run(m, CandidateRunSignals()) == ()
    assert m.current(Channel.CANDIDATE) == "agent-v2"
    assert m.retired() == ()


def test_audit_sink_failure_never_crashes(tmp_path):
    def bad_audit(kind, data):
        raise OSError("disk on fire")

    m = manager(tmp_path, audit=bad_audit)
    m.activate(Channel.CANDIDATE, "agent-v2")
    tripped = evaluate_candidate_run(m, CandidateRunSignals(scope_violation=True))
    assert tripped == (Breaker.SCOPE_VIOLATION,)
    assert "agent-v2" in m.retired()  # retirement happened regardless


def test_channels_file_is_metadata_only(tmp_path):
    m = manager(tmp_path)
    m.activate(Channel.STABLE, "agent-v1")
    data = json.loads((tmp_path / ".pxx" / "channels.json").read_text())
    assert set(data) == {
        "channels",
        "stable_stack",
        "retired",
        "history",
        "canary_outcomes",
    }


# --- B7.1: canary channel — selection, evidence accrual, advance/retire -------------


def test_canary_selection_deterministic_and_rate():
    from pxx.improve.channels import CANARY_RATE, select_canary_run

    ids = [f"20260718T00000{i:02d}Z-{n:08x}" for i in range(10) for n in range(200)]
    first = [select_canary_run(r) for r in ids]
    second = [select_canary_run(r) for r in ids]
    assert first == second  # reproducible, no RNG
    rate = sum(first) / len(first)
    assert abs(rate - CANARY_RATE) < 0.02  # ~1-in-20


def test_canary_outcomes_accrue_as_distinct_evidence(tmp_path):
    m = manager(tmp_path)
    m.activate(Channel.CANARY, "agent-canary-1")
    m.record_canary_outcome("run-1", "COMPLETED")
    m.record_canary_outcome("run-2", "COMPLETED")
    m.record_canary_outcome("run-3", "MODEL_UNAVAILABLE", "endpoint down")
    status = m.canary_status()
    assert status.runs == 3
    assert status.green == 2
    assert status.failures == 1
    # persisted + reloadable (evidence accrues across restarts)
    status2 = manager(tmp_path).canary_status()
    assert (status2.runs, status2.green, status2.failures) == (3, 2, 1)


def test_canary_green_over_n_advances(tmp_path):
    from pxx.improve.channels import CANARY_ADVANCE_RUNS

    m = manager(tmp_path)
    m.activate(Channel.CANARY, "agent-canary-1")
    for i in range(CANARY_ADVANCE_RUNS - 1):
        m.record_canary_outcome(f"run-{i}", "COMPLETED")
    assert not m.canary_status().eligible_to_advance
    m.record_canary_outcome("run-final", "COMPLETED")
    assert m.canary_status().eligible_to_advance


def test_canary_one_failure_blocks_advance(tmp_path):
    from pxx.improve.channels import CANARY_ADVANCE_RUNS

    m = manager(tmp_path)
    m.activate(Channel.CANARY, "agent-canary-1")
    for i in range(CANARY_ADVANCE_RUNS):
        m.record_canary_outcome(f"run-{i}", "COMPLETED")
    m.record_canary_outcome("run-bad", "TEST_REGRESSION")
    assert not m.canary_status().eligible_to_advance


def test_breaker_trip_retires_canary_not_stable(tmp_path):
    m = manager(tmp_path)
    m.activate(Channel.STABLE, "agent-stable")
    m.activate(Channel.CANARY, "agent-canary-1")
    tripped = evaluate_candidate_run(
        m, CandidateRunSignals(scope_violation=True), channel=Channel.CANARY
    )
    assert tripped == (Breaker.SCOPE_VIOLATION,)
    assert m.current(Channel.CANARY) is None
    assert "agent-canary-1" in m.retired()
    assert m.current(Channel.STABLE) == "agent-stable"  # production untouched


# --- B7.2: the three missing circuit breakers ---------------------------------------


def test_approval_rate_drop_breaker():
    from pxx.improve.channels import APPROVAL_DROP_MAX_DELTA

    tripped = tripped_breakers(CandidateRunSignals(approval_rate=0.6, baseline_approval_rate=0.9))
    assert Breaker.APPROVAL_RATE_DROP in tripped
    healthy = tripped_breakers(
        CandidateRunSignals(
            approval_rate=0.9 - APPROVAL_DROP_MAX_DELTA + 0.01,
            baseline_approval_rate=0.9,
        )
    )
    assert Breaker.APPROVAL_RATE_DROP not in healthy
    # no baseline -> cannot judge -> no trip
    assert tripped_breakers(CandidateRunSignals(approval_rate=0.1)) == ()


def test_human_correction_spike_breaker():
    from pxx.improve.channels import CORRECTION_SPIKE_THRESHOLD

    tripped = tripped_breakers(CandidateRunSignals(human_corrections=CORRECTION_SPIKE_THRESHOLD))
    assert Breaker.HUMAN_CORRECTION_SPIKE in tripped
    healthy = tripped_breakers(
        CandidateRunSignals(human_corrections=CORRECTION_SPIKE_THRESHOLD - 1)
    )
    assert Breaker.HUMAN_CORRECTION_SPIKE not in healthy


def test_reviewer_availability_drop_breaker():
    from pxx.improve.channels import MIN_REVIEWER_AVAILABILITY

    tripped = tripped_breakers(
        CandidateRunSignals(reviewer_availability=MIN_REVIEWER_AVAILABILITY - 0.1)
    )
    assert Breaker.REVIEWER_AVAILABILITY_DROP in tripped
    healthy = tripped_breakers(CandidateRunSignals(reviewer_availability=MIN_REVIEWER_AVAILABILITY))
    assert Breaker.REVIEWER_AVAILABILITY_DROP not in healthy
    # unknown availability -> no trip
    assert tripped_breakers(CandidateRunSignals()) == ()


def test_all_seven_breakers_present():
    assert len(set(Breaker)) == 7
