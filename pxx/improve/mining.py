"""Phase 15: deterministic clustering over run outcomes.

The optimizer plane's read side: group past runs by stable dimensions
(terminal code, backend, model, memory presence, round-count bucket) and
turn failure clusters into *proposals* — never applied changes.

Causal guardrail: every :class:`Cluster` and :class:`Proposal` is labeled
``correlation``. Mining observes co-occurrence; it can never establish
causation, and the label is pinned by tests so it cannot drift.

Runs arrive as plain mappings or dataclass-like objects (``pxx.runs`` is
built concurrently — do not import it); they are coerced into
:class:`MinedRun`. Everything here is a pure function: no I/O, no clock,
no randomness, so identical input always yields identical output.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

#: The only label mining may ever emit. Correlation, NEVER causation.
CORRELATION_LABEL = "correlation"


@dataclass(frozen=True)
class MinedRun:
    """The minimal run projection mining needs."""

    run_id: str
    terminal_code: str
    backend: str = ""
    model: str = ""
    agent_version_id: str = ""
    rounds: int = 0
    memory_used: bool = False
    stage: str = ""  # failing leg: edit|test|review|lint ("" = unknown)
    task_category: str = ""
    scope_type: str = ""  # "" | "repo" | "scoped"
    severity: str = ""  # worst finding severity ("" = none)
    retried: bool = False  # the run was a retry of an earlier attempt
    contributing_codes: tuple[str, ...] = ()
    files_changed: int = 0


_MINED_FIELDS = {f.name for f in dataclasses.fields(MinedRun)}


def _coerce(run: Any) -> MinedRun:
    """Coerce a mapping or dataclass-like object into a :class:`MinedRun`.

    Duck-typed: any object with the right attributes (or mapping keys)
    works; extra attributes are ignored.
    """
    if isinstance(run, MinedRun):
        return run
    if isinstance(run, Mapping):
        data = {k: v for k, v in run.items() if k in _MINED_FIELDS}
    elif dataclasses.is_dataclass(run) and not isinstance(run, type):
        data = {
            f.name: getattr(run, f.name) for f in dataclasses.fields(run) if f.name in _MINED_FIELDS
        }
    else:
        data = {k: getattr(run, k) for k in _MINED_FIELDS if hasattr(run, k)}
    data["terminal_code"] = str(data["terminal_code"])
    if "contributing_codes" in data:
        data["contributing_codes"] = tuple(str(c) for c in data["contributing_codes"])
    return MinedRun(**data)


def _rounds_bucket(rounds: int) -> str:
    """Deterministic round-count bucket."""
    if rounds <= 0:
        return "0"
    if rounds <= 3:
        return "1-3"
    if rounds <= 10:
        return "4-10"
    return "11+"


@dataclass(frozen=True)
class Cluster:
    """A deterministic group of runs sharing one signature.

    ``label`` is always :data:`CORRELATION_LABEL` — the causal guardrail.
    ``run_ids`` is sorted so identical input yields identical output.
    """

    terminal_code: str
    backend: str
    model: str
    memory_used: bool
    rounds_bucket: str
    run_ids: tuple[str, ...]
    stage: str = ""
    task_category: str = ""
    scope_type: str = ""
    severity: str = ""
    retried: bool = False
    label: str = CORRELATION_LABEL

    @property
    def size(self) -> int:
        return len(self.run_ids)

    @property
    def key(self) -> tuple:
        return (
            self.terminal_code,
            self.backend,
            self.model,
            self.memory_used,
            self.rounds_bucket,
            self.stage,
            self.task_category,
            self.scope_type,
            self.severity,
            self.retried,
        )


def cluster_outcomes(runs: Any) -> list[Cluster]:
    """Group runs by (terminal code, backend, model, memory, rounds bucket,
    stage, task category, scope type, severity, retry behavior).

    Runs lacking the richer dimensions cluster together on the empty
    defaults. Deterministic: clusters are sorted by key and ``run_ids``
    sorted within each cluster, independent of input ordering.
    """
    groups: dict[tuple, list[str]] = {}
    for raw in runs:
        run = _coerce(raw)
        key = (
            run.terminal_code,
            run.backend,
            run.model,
            run.memory_used,
            _rounds_bucket(run.rounds),
            run.stage,
            run.task_category,
            run.scope_type,
            run.severity,
            run.retried,
        )
        groups.setdefault(key, []).append(run.run_id)
    return [
        Cluster(
            terminal_code=key[0],
            backend=key[1],
            model=key[2],
            memory_used=key[3],
            rounds_bucket=key[4],
            run_ids=tuple(sorted(ids)),
            stage=key[5],
            task_category=key[6],
            scope_type=key[7],
            severity=key[8],
            retried=key[9],
        )
        for key, ids in sorted(groups.items())
    ]


class RootCause(StrEnum):
    """Harness-first root-cause classes (Phase 15 amend).

    An agent struggling is evidence the ENVIRONMENT may be missing
    context/tools/checks — not automatically a prompt problem. Proposals
    must say which lever they pull and why a prompt change is insufficient.
    """

    AMBIGUOUS_REQUIREMENTS = "AMBIGUOUS_REQUIREMENTS"
    CONTEXT_MISSING = "CONTEXT_MISSING"
    MODEL_CAPABILITY = "MODEL_CAPABILITY"
    PROMPT_DEFECT = "PROMPT_DEFECT"
    TOOLING = "TOOLING"
    EVALUATOR_DEFECT = "EVALUATOR_DEFECT"


@dataclass(frozen=True)
class Proposal:
    """A proposed change — PROPOSAL ONLY. Nothing here is ever applied by
    mining; promotion is a separate, human-gated plane."""

    target: str
    operation: str
    evidence: tuple[str, ...]  # run_ids backing this proposal
    hypothesis: str
    expected_movement: str
    risk: str  # "low" | "medium" | "high"
    confidence: float  # 0.0 .. 0.9, derived from evidence count
    basis: str = CORRELATION_LABEL
    root_cause: str = ""
    reason_prompt_change_is_insufficient: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "operation": self.operation,
            "evidence": list(self.evidence),
            "hypothesis": self.hypothesis,
            "expected_movement": self.expected_movement,
            "risk": self.risk,
            "confidence": self.confidence,
            "basis": self.basis,
            "root_cause": self.root_cause,
            "reason_prompt_change_is_insufficient": (self.reason_prompt_change_is_insufficient),
        }


@dataclass(frozen=True)
class _Rule:
    target: str
    operation: str
    risk: str
    hypothesis: str
    expected_movement: str
    root_cause: str = str(RootCause.PROMPT_DEFECT)
    reason_prompt_change_is_insufficient: str = ""


#: Deterministic failure-code -> proposal rules. Targets are restricted to
#: the change classes candidates.py permits (prompts, tighten-only budgets,
#: model, memory retrieval limits).
_RULES: dict[str, _Rule] = {
    "ROUND_CAP": _Rule(
        target="pxx/prompts/native_system.md",
        operation="adjust_prompt",
        risk="medium",
        hypothesis=(
            "ROUND_CAP terminations correlate with prompt wording that "
            "under-emphasizes converging within the round budget"
        ),
        expected_movement="fewer ROUND_CAP terminations",
        root_cause=str(RootCause.PROMPT_DEFECT),
        reason_prompt_change_is_insufficient=(
            "the loop converges in most runs; the failure mode is wording, "
            "not missing context or capability"
        ),
    ),
    "NO_TEST_PROGRESS": _Rule(
        target="model",
        operation="switch_model",
        risk="medium",
        hypothesis=(
            "NO_TEST_PROGRESS terminations correlate with the model being "
            "unable to produce a progressing edit — a capability limit, not "
            "a wording defect"
        ),
        expected_movement="fewer NO_TEST_PROGRESS terminations",
        root_cause=str(RootCause.MODEL_CAPABILITY),
        reason_prompt_change_is_insufficient=(
            "the agent attempted edits and none improved the failing set; "
            "better wording cannot supply the missing capability"
        ),
    ),
    "REVIEW_UNPARSEABLE": _Rule(
        target="pxx/prompts/review.md",
        operation="adjust_prompt",
        risk="medium",
        hypothesis=(
            "REVIEW_UNPARSEABLE terminations correlate with reviewer output "
            "contract wording that the reviewer model fails to follow"
        ),
        expected_movement="fewer REVIEW_UNPARSEABLE terminations",
        root_cause=str(RootCause.PROMPT_DEFECT),
        reason_prompt_change_is_insufficient=(
            "the reviewer produces text, just not the contract shape; the "
            "output contract wording is the lever"
        ),
    ),
    "BUDGET_EXCEEDED": _Rule(
        target="budgets",
        operation="tighten_budget",
        risk="low",
        hypothesis=(
            "BUDGET_EXCEEDED terminations correlate with token budgets that "
            "permit runaway spend before termination"
        ),
        expected_movement="lower worst-case token spend per failed run",
        root_cause=str(RootCause.TOOLING),
        reason_prompt_change_is_insufficient=(
            "spend is unbounded; only a tighter deterministic budget stops it"
        ),
    ),
    "MODEL_UNAVAILABLE": _Rule(
        target="model",
        operation="switch_model",
        risk="medium",
        hypothesis=(
            "MODEL_UNAVAILABLE terminations correlate with the current model or provider endpoint"
        ),
        expected_movement="fewer MODEL_UNAVAILABLE terminations",
        root_cause=str(RootCause.TOOLING),
        reason_prompt_change_is_insufficient=(
            "the endpoint is unreachable; no prompt change fixes connectivity"
        ),
    ),
}

_MEMORY_RULE = _Rule(
    target="memory_retrieval_limit",
    operation="adjust_memory",
    risk="low",
    hypothesis="failures correlate with absent memory context in these runs",
    expected_movement="higher completion rate on recurring task shapes",
    root_cause=str(RootCause.CONTEXT_MISSING),
    reason_prompt_change_is_insufficient=(
        "the runs lacked relevant repository context; retrieval limits are the lever, not wording"
    ),
)


def _confidence(evidence_count: int) -> float:
    """Deterministic confidence from evidence volume; capped below 1.0
    because correlation is never proof."""
    return min(0.9, round(0.2 + 0.1 * evidence_count, 2))


def propose_from_clusters(clusters: list[Cluster]) -> list[Proposal]:
    """Turn failure clusters into proposals.

    Rules are keyed by terminal code; a failing cluster without memory
    context also yields a memory proposal. Proposals sharing
    ``(target, operation)`` are merged (evidence unioned, confidence
    recomputed) and the result is sorted — fully deterministic.
    COMPLETED clusters yield nothing.
    """
    merged: dict[tuple[str, str], tuple[_Rule, set[str]]] = {}
    for cluster in clusters:
        rules: list[_Rule] = []
        rule = _RULES.get(cluster.terminal_code)
        if rule is not None:
            rules.append(rule)
            if not cluster.memory_used:
                rules.append(_MEMORY_RULE)
        for r in rules:
            key = (r.target, r.operation)
            if key not in merged:
                merged[key] = (r, set())
            merged[key][1].update(cluster.run_ids)
    proposals = [
        Proposal(
            target=r.target,
            operation=r.operation,
            evidence=tuple(sorted(evidence)),
            hypothesis=r.hypothesis,
            expected_movement=r.expected_movement,
            risk=r.risk,
            confidence=_confidence(len(evidence)),
            root_cause=r.root_cause,
            reason_prompt_change_is_insufficient=(r.reason_prompt_change_is_insufficient),
        )
        for (target, op), (r, evidence) in sorted(merged.items())
    ]
    return proposals


# --- recurring-pattern detectors (Phase 15.2) --------------------------------------


@dataclass(frozen=True)
class Pattern:
    """A recurring cross-run pattern. Always correlation, never causation."""

    name: str
    run_ids: tuple[str, ...]
    detail: str
    label: str = CORRELATION_LABEL


def detect_patterns(runs: Any) -> list[Pattern]:
    """Deterministic recurring-pattern detectors over the run stream.

    Each detector fires only when its evidence threshold is met and cites
    the exact run_ids. Patterns are sorted by name — identical input always
    yields identical output.
    """
    mined = [_coerce(r) for r in runs]
    patterns: list[Pattern] = []

    unparseable = sorted(
        r.run_id
        for r in mined
        if r.terminal_code == "REVIEW_UNPARSEABLE" or "REVIEW_UNPARSEABLE" in r.contributing_codes
    )
    if len(unparseable) >= 2:
        patterns.append(
            Pattern(
                name="unparseable_review",
                run_ids=tuple(unparseable),
                detail=f"{len(unparseable)} runs with unparseable reviewer output",
            )
        )

    timeouts = sorted(
        r.run_id for r in mined if r.terminal_code in ("EDIT_TIMEOUT", "BUDGET_EXCEEDED")
    )
    if len(timeouts) >= 2:
        patterns.append(
            Pattern(
                name="timeout_cluster",
                run_ids=tuple(timeouts),
                detail=f"{len(timeouts)} runs died on time/token budgets",
            )
        )

    lint = sorted(r.run_id for r in mined if r.terminal_code == "LINT_BLOCKED")
    if lint:
        patterns.append(
            Pattern(
                name="lint_blocked",
                run_ids=tuple(lint),
                detail=f"{len(lint)} runs blocked by the lint gate",
            )
        )

    with_mem = [r for r in mined if r.memory_used]
    without_mem = [r for r in mined if not r.memory_used]
    if with_mem and without_mem:
        avg_with = sum(r.files_changed for r in with_mem) / len(with_mem)
        avg_without = sum(r.files_changed for r in without_mem) / len(without_mem)
        if avg_with > avg_without * 1.5 and avg_with > 0:
            ids = tuple(sorted(r.run_id for r in with_mem))
            patterns.append(
                Pattern(
                    name="memory_diff_size_correlation",
                    run_ids=ids,
                    detail=(
                        f"memory-injected runs average {avg_with:.1f} files changed "
                        f"vs {avg_without:.1f} without (correlation)"
                    ),
                )
            )

    by_model: dict[str, list[MinedRun]] = {}
    for r in mined:
        if r.model:
            by_model.setdefault(r.model, []).append(r)
    failing_models = sorted(
        model
        for model, group in by_model.items()
        if group and all(r.terminal_code != "COMPLETED" for r in group)
    )
    passing_models = [
        model
        for model, group in by_model.items()
        if any(r.terminal_code == "COMPLETED" for r in group)
    ]
    if failing_models and passing_models:
        ids = tuple(sorted(r.run_id for m in failing_models for r in by_model[m]))
        patterns.append(
            Pattern(
                name="model_failure_disparity",
                run_ids=ids,
                detail=(
                    f"models {failing_models} fail every run while {passing_models} complete some"
                ),
            )
        )

    return sorted(patterns, key=lambda p: p.name)
