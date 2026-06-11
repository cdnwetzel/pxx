"""Tests for pxx.loop — the bounded autonomy driver and its guards (#009)."""

from __future__ import annotations

from pathlib import Path

from pxx import loop
from pxx.review_gate import Finding


def _p1(i: int = 1) -> Finding:
    return Finding(f"F-00{i}", "P1", "open", "x.py", "fix me")


def _unparseable() -> Finding:
    return Finding("F-009", "UNPARSEABLE", "open", "", "bad header")


class _Harness:
    """Monkeypatched seams: scripted per-round verdicts and failing sets."""

    def __init__(self, monkeypatch, tmp_path, verdicts, failings, diff_lines=0):
        self.edits: list[str] = []
        self._verdicts = list(verdicts)
        self._failings = list(failings)
        self.tmp_path = tmp_path

        monkeypatch.setattr(loop, "_head_sha", lambda root: "base")
        monkeypatch.setattr(loop, "_diff_lines_since", lambda root, sha: diff_lines)
        monkeypatch.setattr(
            loop,
            "_run_edit_round",
            lambda root, msg, scope, timeout=None: self.edits.append(msg) or 0,
        )
        monkeypatch.setattr(loop, "_failing_tests", lambda root: self._failings.pop(0))
        monkeypatch.setattr(
            loop,
            "_review_verdict",
            lambda root: self._verdicts.pop(0),
        )
        monkeypatch.setattr("pxx.self_modes.self_lint", lambda root: 0)
        monkeypatch.setattr(
            "pxx.audit.write_session_start",
            lambda record, log_path=None: Path("/dev/null"),
        )

    def run(self, **kw):
        return loop.run_loop(self.tmp_path, "task", "pxx/", **kw)


class TestRunLoopGuards:
    def test_approve_first_round_exits_0(self, monkeypatch, tmp_path):
        h = _Harness(
            monkeypatch,
            tmp_path,
            verdicts=[loop.RoundResult("APPROVE", [])],
            failings=[set(), set()],  # baseline, round 1
        )
        assert h.run() == 0
        assert len(h.edits) == 1

    def test_round_cap_stops_persistent_revise(self, monkeypatch, tmp_path):
        revise = lambda: loop.RoundResult("REVISE", [_p1()])  # noqa: E731
        h = _Harness(
            monkeypatch,
            tmp_path,
            verdicts=[revise(), revise(), revise()],
            # baseline 3 failures, strictly shrinking so the progress guard
            # never fires — only the cap stops it.
            failings=[{"a", "b", "c"}, {"a", "b"}, {"a"}, set()],
        )
        assert h.run() == 1
        assert len(h.edits) == 3

    def test_no_progress_on_baseline_set_aborts(self, monkeypatch, tmp_path):
        revise = lambda: loop.RoundResult("REVISE", [_p1()])  # noqa: E731
        h = _Harness(
            monkeypatch,
            tmp_path,
            verdicts=[revise(), revise()],
            failings=[{"t1"}, {"t1"}, {"t1"}],  # never shrinks
        )
        assert h.run() == 1
        assert len(h.edits) == 2  # aborted after round 2's measurement

    def test_cumulative_diff_budget_aborts(self, monkeypatch, tmp_path):
        h = _Harness(
            monkeypatch,
            tmp_path,
            verdicts=[loop.RoundResult("REVISE", [_p1()])],
            failings=[set(), set()],
            diff_lines=10_000,
        )
        assert h.run() == 1
        assert len(h.edits) == 1

    def test_wall_clock_budget_stops_before_any_edit(self, monkeypatch, tmp_path):
        h = _Harness(
            monkeypatch,
            tmp_path,
            verdicts=[],
            failings=[set()],
        )
        assert h.run(max_seconds=-1.0) == 1
        assert h.edits == []

    def test_reject_stops_immediately(self, monkeypatch, tmp_path):
        h = _Harness(
            monkeypatch,
            tmp_path,
            verdicts=[loop.RoundResult("REJECT", [])],
            failings=[set(), set()],
        )
        assert h.run() == 1
        assert len(h.edits) == 1

    def test_no_review_stops_without_further_rounds(self, monkeypatch, tmp_path):
        h = _Harness(
            monkeypatch,
            tmp_path,
            verdicts=[loop.RoundResult("NO_REVIEW", [])],
            failings=[set(), set()],
        )
        assert h.run() == 1
        assert len(h.edits) == 1

    def test_approve_blocked_by_baseline_failures_keeps_going(
        self, monkeypatch, tmp_path
    ):
        # Verdict APPROVE but a baseline test still fails -> not done yet;
        # next round clears it -> success with two edits.
        h = _Harness(
            monkeypatch,
            tmp_path,
            verdicts=[
                loop.RoundResult("APPROVE", []),
                loop.RoundResult("APPROVE", []),
            ],
            failings=[{"t1"}, {"t1"}, set()],
        )
        assert h.run() == 0
        assert len(h.edits) == 2

    def test_unmeasurable_baseline_refuses_to_start(self, monkeypatch, tmp_path):
        h = _Harness(monkeypatch, tmp_path, verdicts=[], failings=[None])
        assert h.run() == 1
        assert h.edits == []

    def test_healing_message_carries_findings_and_failures(self, monkeypatch, tmp_path):
        h = _Harness(
            monkeypatch,
            tmp_path,
            verdicts=[
                loop.RoundResult("REVISE", [_p1()]),
                loop.RoundResult("APPROVE", []),
            ],
            failings=[{"t1", "t2"}, {"t1"}, set()],
        )
        assert h.run() == 0
        # round 2's message includes the gate's finding and the live failures
        assert "F-001" in h.edits[1]
        assert "t1" in h.edits[1]


class TestReviewVerdictClassification:
    def _arrange(self, monkeypatch, findings, evidence=True, pass_rc=0):
        monkeypatch.setattr("pxx.review_gate.run_review_pass", lambda root: pass_rc)
        monkeypatch.setattr(
            "pxx.review_gate.has_review_evidence", lambda root: evidence
        )
        monkeypatch.setattr(
            "pxx.review_gate.collect_active_findings", lambda root: findings
        )

    def test_all_unparseable_maps_to_no_review(self, monkeypatch, tmp_path):
        self._arrange(monkeypatch, [_unparseable()])
        assert loop._review_verdict(tmp_path).verdict == "NO_REVIEW"

    def test_unparseable_plus_p1_still_heals(self, monkeypatch, tmp_path):
        self._arrange(monkeypatch, [_unparseable(), _p1()])
        result = loop._review_verdict(tmp_path)
        assert result.verdict == "REVISE"
        assert [f.id for f in result.healable] == ["F-001"]

    def test_failed_review_pass_is_no_review(self, monkeypatch, tmp_path):
        self._arrange(monkeypatch, [], pass_rc=1)
        assert loop._review_verdict(tmp_path).verdict == "NO_REVIEW"

    def test_missing_evidence_is_no_review(self, monkeypatch, tmp_path):
        self._arrange(monkeypatch, [], evidence=False)
        assert loop._review_verdict(tmp_path).verdict == "NO_REVIEW"


class TestHealOnce:
    def _arrange(self, monkeypatch, findings, evidence=True):
        self.edits = []
        monkeypatch.setattr(
            "pxx.review_gate.has_review_evidence", lambda root: evidence
        )
        monkeypatch.setattr(
            "pxx.review_gate.collect_active_findings", lambda root: findings
        )
        monkeypatch.setattr(loop, "_failing_tests", lambda root: set())
        monkeypatch.setattr(
            loop,
            "_run_edit_round",
            lambda root, msg, scope, timeout=None: self.edits.append(msg) or 0,
        )
        monkeypatch.setattr(
            loop,
            "_review_verdict",
            lambda root: loop.RoundResult("APPROVE", []),
        )

    def test_no_evidence_refuses_without_editing(self, monkeypatch, tmp_path):
        self._arrange(monkeypatch, [], evidence=False)
        assert loop.heal_once(tmp_path, "pxx/") == 1
        assert self.edits == []

    def test_approve_is_a_noop_success(self, monkeypatch, tmp_path):
        self._arrange(monkeypatch, [])
        assert loop.heal_once(tmp_path, "pxx/") == 0
        assert self.edits == []

    def test_reject_refuses_p0_for_humans(self, monkeypatch, tmp_path):
        self._arrange(monkeypatch, [Finding("F-001", "P0", "open", "x", "crit")])
        assert loop.heal_once(tmp_path, "pxx/") == 1
        assert self.edits == []

    def test_all_unparseable_refuses_without_editing(self, monkeypatch, tmp_path):
        self._arrange(monkeypatch, [_unparseable()])
        assert loop.heal_once(tmp_path, "pxx/") == 1
        assert self.edits == []

    def test_healable_revise_runs_exactly_one_round(self, monkeypatch, tmp_path):
        self._arrange(monkeypatch, [_p1()])
        assert loop.heal_once(tmp_path, "pxx/") == 0
        assert len(self.edits) == 1
        assert "F-001" in self.edits[0]


class TestRejectedMessageVerdictAware:
    def test_no_review_remedy_is_review_not_heal(self, tmp_path, capsys):
        from pxx import workflow

        state = workflow.WorkflowState(phase="rejected", review_verdict="NO_REVIEW")
        workflow.save_state(state, tmp_path)
        assert workflow.resume_state(tmp_path) == 1
        err = capsys.readouterr().err
        assert "pxx --review" in err
        assert "--heal" not in err

    def test_revise_remedy_still_offers_heal(self, tmp_path, capsys):
        from pxx import workflow

        state = workflow.WorkflowState(phase="rejected", review_verdict="REVISE")
        workflow.save_state(state, tmp_path)
        assert workflow.resume_state(tmp_path) == 1
        assert "--heal" in capsys.readouterr().err


class TestGreenBaselineProgress:
    """F1: with a green baseline the old rule was degenerate (0 >= 0 always)."""

    def test_green_baseline_runs_past_round_2_while_findings_shrink(
        self, monkeypatch, tmp_path
    ):
        h = _Harness(
            monkeypatch,
            tmp_path,
            verdicts=[
                loop.RoundResult("REVISE", [_p1(1), _p1(2)]),
                loop.RoundResult("REVISE", [_p1(1)]),
                loop.RoundResult("APPROVE", []),
            ],
            failings=[set(), set(), set(), set()],
        )
        assert h.run() == 0
        assert len(h.edits) == 3  # the old bug stopped this at round 2

    def test_green_baseline_stops_when_findings_plateau(
        self, monkeypatch, tmp_path, capsys
    ):
        h = _Harness(
            monkeypatch,
            tmp_path,
            verdicts=[
                loop.RoundResult("REVISE", [_p1(1)]),
                loop.RoundResult("REVISE", [_p1(1)]),
            ],
            failings=[set(), set(), set()],
        )
        assert h.run() == 1
        assert len(h.edits) == 2
        assert "healable findings (1 → 1)" in capsys.readouterr().err


class TestEditRoundFailure:
    """F2: a failed edit round must stop the loop, not burn budget."""

    def test_failed_edit_stops_run_loop_without_review(self, monkeypatch, tmp_path):
        # verdicts=[] proves _review_verdict is never consulted: the scripted
        # pop would raise if it were.
        h = _Harness(monkeypatch, tmp_path, verdicts=[], failings=[set()])
        monkeypatch.setattr(
            loop, "_run_edit_round", lambda root, msg, scope, timeout=None: 2
        )
        assert h.run() == 1

    def test_failed_edit_records_edit_failed_verdict(self, monkeypatch, tmp_path):
        from pxx import workflow

        h = _Harness(monkeypatch, tmp_path, verdicts=[], failings=[set()])
        monkeypatch.setattr(
            loop, "_run_edit_round", lambda root, msg, scope, timeout=None: 2
        )
        h.run()
        state = workflow.load_state(tmp_path)
        assert state.phase == "rejected"
        assert state.review_verdict == "EDIT_FAILED"

    def test_heal_once_stops_before_review_on_failed_edit(self, monkeypatch, tmp_path):
        consulted = []
        monkeypatch.setattr("pxx.review_gate.has_review_evidence", lambda root: True)
        monkeypatch.setattr(
            "pxx.review_gate.collect_active_findings", lambda root: [_p1()]
        )
        monkeypatch.setattr(loop, "_failing_tests", lambda root: set())
        monkeypatch.setattr(
            loop, "_run_edit_round", lambda root, msg, scope, timeout=None: 1
        )
        monkeypatch.setattr(loop, "_review_verdict", lambda root: consulted.append(1))
        assert loop.heal_once(tmp_path, "pxx/") == 1
        assert consulted == []


class TestEditRoundTimeout:
    """F3: a wedged aider can't defeat the wall-clock budget."""

    def test_subprocess_timeout_returns_124(self, monkeypatch, tmp_path):
        import subprocess as sp

        def boom(*a, **k):
            raise sp.TimeoutExpired(cmd="pxx", timeout=1)

        monkeypatch.setattr(loop.subprocess, "run", boom)
        assert loop._run_edit_round(tmp_path, "msg", "pxx/") == 124

    def test_timed_out_round_stops_the_loop(self, monkeypatch, tmp_path, capsys):
        h = _Harness(monkeypatch, tmp_path, verdicts=[], failings=[set()])
        monkeypatch.setattr(
            loop, "_run_edit_round", lambda root, msg, scope, timeout=None: 124
        )
        assert h.run() == 1
        assert "timed out" in capsys.readouterr().err

    def test_remaining_budget_is_passed_as_timeout(self, monkeypatch, tmp_path):
        seen: list[float] = []

        def fake_edit(root, msg, scope, timeout=None):
            seen.append(timeout)
            return 2  # stop after capturing

        h = _Harness(monkeypatch, tmp_path, verdicts=[], failings=[set()])
        monkeypatch.setattr(loop, "_run_edit_round", fake_edit)
        h.run(max_seconds=1800.0)
        assert seen and 60.0 <= seen[0] <= 1800.0
