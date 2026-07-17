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
