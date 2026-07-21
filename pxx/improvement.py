"""Experience mining — roadmap Phase 15, minimum slice.

Deterministic clustering of the run-outcome stream (pxx/outcomes.py) into
structured observations about recurring weakness. No model is consulted:
15.1 mandates deterministic grouping first; semantic clustering waits until
free-text traces demand it.

Causal guardrail (15.3): every observation is labeled with its evidence
strength — ``correlation`` here, because clustering shows association, not
cause. Nothing in this module proposes or applies a change; Phase 15 stops
before candidate generation by design. It answers "what should we look at",
never "what should we do".
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from pxx import outcomes
from pxx.outcomes import RunOutcome

# A failure rate above this, over enough runs, is worth surfacing.
_MIN_RUNS_FOR_AGENT_SIGNAL = 3
_ELEVATED_FAILURE_RATE = 0.34


@dataclass(frozen=True)
class Observation:
    """One mined pattern. ``evidence`` is the run_ids behind it — an
    observation you cannot trace to runs is not an observation."""

    kind: str  # "dominant-failure" | "agent-failure-rate" | "agent-regression"
    summary: str
    evidence_strength: str  # always "correlation" in the deterministic slice
    metric: float
    evidence: tuple[str, ...]


def _failed(o: RunOutcome) -> bool:
    return not o.accepted


def analyze(runs: list[RunOutcome]) -> list[Observation]:
    """Cluster runs into weakness observations, most-signal first."""
    obs: list[Observation] = []
    if not runs:
        return obs

    # 1. Dominant failure modes across the whole population.
    failed = [o for o in runs if _failed(o)]
    if failed:
        codes = Counter(o.terminal_code for o in failed)
        top_code, n = codes.most_common(1)[0]
        obs.append(
            Observation(
                kind="dominant-failure",
                summary=(
                    f"{top_code} is the most common failure "
                    f"({n}/{len(failed)} failed runs, {len(runs)} total)"
                ),
                evidence_strength="correlation",
                metric=n / len(failed),
                evidence=tuple(o.run_id for o in failed if o.terminal_code == top_code),
            )
        )

    # 2. Per-agent failure rate — the signal that flags a bad behavior version
    #    (this is how the rejected reviewer candidate shows up next to the
    #    baseline that passes).
    by_agent: dict[str, list[RunOutcome]] = defaultdict(list)
    for o in runs:
        if o.agent_version_id:
            by_agent[o.agent_version_id].append(o)
    for agent, agent_runs in by_agent.items():
        if len(agent_runs) < _MIN_RUNS_FOR_AGENT_SIGNAL:
            continue
        fails = [o for o in agent_runs if _failed(o)]
        rate = len(fails) / len(agent_runs)
        if rate >= _ELEVATED_FAILURE_RATE:
            obs.append(
                Observation(
                    kind="agent-failure-rate",
                    summary=(
                        f"{agent} fails {len(fails)}/{len(agent_runs)} runs "
                        f"({rate:.0%}) — elevated"
                    ),
                    evidence_strength="correlation",
                    metric=rate,
                    evidence=tuple(o.run_id for o in fails),
                )
            )

    # 3. Cross-agent regression: an agent notably worse than the best-observed
    #    peer over a comparable run count — the candidate-1 lesson, mined.
    rates = {
        a: sum(1 for o in r if _failed(o)) / len(r)
        for a, r in by_agent.items()
        if len(r) >= _MIN_RUNS_FOR_AGENT_SIGNAL
    }
    if len(rates) >= 2:
        best = min(rates.values())
        for agent, rate in rates.items():
            if rate - best >= 0.5:
                obs.append(
                    Observation(
                        kind="agent-regression",
                        summary=(
                            f"{agent} fails {rate:.0%} vs the best peer's "
                            f"{best:.0%} — likely a behavior regression"
                        ),
                        evidence_strength="correlation",
                        metric=rate - best,
                        evidence=tuple(o.run_id for o in by_agent[agent] if _failed(o)),
                    )
                )

    obs.sort(key=lambda o: o.metric, reverse=True)
    return obs


def analyze_recent(limit: int = 200) -> list[Observation]:
    return analyze(outcomes.recent_outcomes(limit=limit))


# --- Auto-generation: observation -> validated candidate (Phase 16 link) -----
#
# Deterministic, evidence-backed rules mapping a mined weakness to a
# constrained candidate. Every rule is grounded in something this project
# actually measured — no speculative tuning. Each proposal is run through the
# candidate integrity validator before being returned, so the invariant holds:
# propose_from_observations NEVER emits an invalid candidate. Rules produce
# proposals; a human still promotes (Phase 16 stops before auto-apply).

# Failure codes whose root cause this session traced to the reviewer blocking
# on false positives — advisory mode is the measured fix (r5 flipped live).
_REVIEWER_BLOCKING_FAILURES = frozenset({"NO_TEST_PROGRESS", "REVIEW_REJECTED"})


def propose_from_observations(
    observations: list[Observation], current_review_mode: str
) -> list:
    """Map observations to VALIDATED candidates. Returns candidates.Candidate
    objects (imported lazily to keep the analyzer dependency-light)."""
    from pxx import candidates

    proposals: list = []

    for obs in observations:
        # Rule 1: a dominant reviewer-blocking failure under a BLOCKING
        # reviewer → propose advisory. Grounded: this session measured that
        # the local reviewer's false positives spin the heal loop
        # (NO_TEST_PROGRESS) or hard-reject correct code (REVIEW_REJECTED),
        # and advisory mode removes the reviewer from the enforcement path
        # while the deterministic gates still hold.
        if (
            obs.kind == "dominant-failure"
            and current_review_mode == "blocking"
            and any(code in obs.summary for code in _REVIEWER_BLOCKING_FAILURES)
        ):
            cand = candidates.Candidate(
                candidate_id="cand-auto-advisory-review",
                field="review_mode",
                value="advisory",
                baseline_value="blocking",
                rationale=(
                    "Dominant failures are reviewer-blocking codes; advisory "
                    "mode keeps the reviewer's findings as advice while the "
                    "deterministic gates enforce (measured to fix the r5 "
                    "false-positive heal-spin)."
                ),
                from_observation=obs.summary,
            )
            if candidates.validate_candidate(cand).ok:
                proposals.append(cand)

    # Never emit duplicates for the same field (one variable per candidate,
    # and re-running shouldn't stack).
    seen: set[str] = set()
    unique = []
    for c in proposals:
        if c.field not in seen:
            seen.add(c.field)
            unique.append(c)
    return unique
