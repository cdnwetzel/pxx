"""Tests for pxx.improvement — deterministic experience mining (#015)."""

from __future__ import annotations

from pxx.improvement import analyze
from pxx.outcomes import RunOutcome


def _run(run_id, agent, code):
    return RunOutcome(
        run_id=run_id,
        agent_version_id=agent,
        terminal_code=code,
        accepted=(code == "APPROVED"),
        rounds=1,
        edit_seconds=1,
        test_seconds=1,
        review_seconds=0,
        diff_lines=1,
        baseline_failing=0,
        introduced_failing=0,
        findings_p0=0,
        findings_p1=0,
        findings_p2=0,
        findings_unparseable=0,
        verdicts=(),
        start_sha="a",
        end_sha="b",
    )


class TestAnalyze:
    def test_empty_is_empty(self):
        assert analyze([]) == []

    def test_dominant_failure_surfaced_with_evidence(self):
        runs = [
            _run("r1", "agentA", "NO_TEST_PROGRESS"),
            _run("r2", "agentA", "NO_TEST_PROGRESS"),
            _run("r3", "agentA", "OUT_OF_SCOPE"),
            _run("r4", "agentA", "APPROVED"),
        ]
        obs = analyze(runs)
        dom = [o for o in obs if o.kind == "dominant-failure"]
        assert dom and "NO_TEST_PROGRESS" in dom[0].summary
        assert set(dom[0].evidence) == {"r1", "r2"}  # traceable to the runs

    def test_elevated_agent_failure_rate_flagged(self):
        runs = [_run(f"r{i}", "bad", "NO_TEST_PROGRESS") for i in range(4)]
        runs += [_run("g1", "bad", "APPROVED")]
        obs = analyze(runs)
        rate = [o for o in obs if o.kind == "agent-failure-rate"]
        assert rate and "bad" in rate[0].summary

    def test_healthy_agent_not_flagged(self):
        runs = [_run(f"r{i}", "good", "APPROVED") for i in range(5)]
        assert not any(o.kind == "agent-failure-rate" for o in analyze(runs))

    def test_regression_between_agents(self):
        # Mirrors the real candidate-1 lesson: one agent much worse than a peer.
        runs = [_run(f"b{i}", "baseline", "APPROVED") for i in range(5)]
        runs += [_run(f"c{i}", "candidate", "NO_TEST_PROGRESS") for i in range(5)]
        obs = analyze(runs)
        reg = [o for o in obs if o.kind == "agent-regression"]
        assert reg and "candidate" in reg[0].summary

    def test_small_sample_agent_not_flagged(self):
        # Below the min-runs threshold, no agent-level claim.
        runs = [_run("r1", "sparse", "NO_TEST_PROGRESS")]
        assert not any(o.kind.startswith("agent") for o in analyze(runs))

    def test_all_observations_are_correlation_strength(self):
        runs = [_run(f"r{i}", "a", "NO_TEST_PROGRESS") for i in range(4)]
        assert all(o.evidence_strength == "correlation" for o in analyze(runs))


class TestProposeFromObservations:
    """observation -> validated candidate (Phase 16 auto-generation link).
    Invariant: every emitted candidate passes the integrity validator."""

    def _dominant(self, code):
        from pxx.improvement import Observation

        return Observation(
            kind="dominant-failure",
            summary=f"{code} is the most common failure (8/10 failed runs, 20 total)",
            evidence_strength="correlation",
            metric=0.8,
            evidence=("r1", "r2"),
        )

    def test_no_test_progress_under_blocking_proposes_advisory(self):
        from pxx.improvement import propose_from_observations

        cands = propose_from_observations(
            [self._dominant("NO_TEST_PROGRESS")], current_review_mode="blocking"
        )
        assert len(cands) == 1
        assert cands[0].field == "review_mode" and cands[0].value == "advisory"
        assert "NO_TEST_PROGRESS" in cands[0].from_observation

    def test_already_advisory_proposes_nothing(self):
        from pxx.improvement import propose_from_observations

        assert (
            propose_from_observations(
                [self._dominant("NO_TEST_PROGRESS")], current_review_mode="advisory"
            )
            == []
        )

    def test_unrelated_failure_proposes_nothing(self):
        from pxx.improvement import propose_from_observations

        assert (
            propose_from_observations(
                [self._dominant("EDIT_FAILED")], current_review_mode="blocking"
            )
            == []
        )

    def test_every_emitted_candidate_is_valid(self):
        # The invariant: propose_from_observations never emits an invalid
        # candidate — the validator is the gate even for auto-generated ones.
        from pxx import candidates
        from pxx.improvement import propose_from_observations

        for code in ("NO_TEST_PROGRESS", "REVIEW_REJECTED"):
            for c in propose_from_observations(
                [self._dominant(code)], current_review_mode="blocking"
            ):
                assert candidates.validate_candidate(c).ok

    def test_no_duplicate_field_proposals(self):
        from pxx.improvement import propose_from_observations

        obs = [self._dominant("NO_TEST_PROGRESS"), self._dominant("REVIEW_REJECTED")]
        cands = propose_from_observations(obs, current_review_mode="blocking")
        assert len({c.field for c in cands}) == len(cands)
