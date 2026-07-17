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
