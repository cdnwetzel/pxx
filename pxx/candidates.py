"""Constrained candidate generation — roadmap Phase 16, minimum slice.

A *candidate* is a declarative delta on an ALLOWLISTED behavior field — never
a source edit. The behavior surface is exactly the AgentManifest's tunable
fields (budgets, review mode, reviewer model/prompt, retry counts), so a
candidate is materialized as an environment overlay and evaluated by running
the existing eval harness with it — no code is ever patched by the optimizer.

Three hard rules, enforced by ``validate_candidate`` before anything runs:

1. **Allowlist only.** A candidate may set only fields in ``ALLOWED_FIELDS``.
   Everything structural — source, evaluators, governance, gates, fixtures —
   is off-limits (the ``docs/TRUST_BOUNDARY.md`` set, which ``.aiderignore``
   now also enforces at the editor level). The candidate generator cannot
   touch its own grader; this is the code path that makes that true.
2. **One variable per candidate.** Multi-field deltas make attribution
   impossible — the roadmap's explicit rule.
3. **No permission or budget *increase*.** A candidate may tighten a budget
   (fewer rounds, less time, smaller diff) but never loosen one; loosening is
   a human decision, never an optimizer's.

Phase 16 stops here by design: candidates are *proposed and validated*, then
handed to the eval/compare chain and a human. Nothing auto-applies.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

# The single authoritative protected set. Re-exported so callers/tests can
# import it from here, but pxx/protected_paths.py is the one place it's defined.
from pxx.protected_paths import PROTECTED_PREFIXES as PROTECTED_PREFIXES
from pxx.protected_paths import is_protected_path

# The only fields a candidate may set — each maps to a pxx env var, so a
# candidate materializes as an overlay with zero source contact. Budgets are
# "tighten only" (see MONOTONE_BUDGETS); the rest are free-choice within type.
ALLOWED_FIELDS: dict[str, str] = {
    "max_rounds": "PXX_MAX_ROUNDS",  # (loop --max-rounds today; env is the candidate seam)
    "diff_budget": "PXX_DIFF_CAP",
    "review_mode": "PXX_REVIEW_MODE",
    "reviewer_model": "PXX_REVIEW_MODEL",
    "reviewer_url": "PXX_REVIEW_URL",
    "edit_retries": "PXX_EDIT_RETRIES",
}

# Budgets a candidate may only *lower* — loosening a safety bound is a human
# call, never an optimizer's (roadmap 16.4).
MONOTONE_BUDGETS: dict[str, str] = {
    "max_rounds": "<=",
    "diff_budget": "<=",
    "edit_retries": "<=",
}

# Structural targets no candidate may name — the SINGLE authoritative list
# lives in pxx/protected_paths.py (imported at module top); the validator and
# the eval content-check both consult is_protected_path().


@dataclass(frozen=True)
class Candidate:
    """A declarative, single-variable behavior proposal (never a code patch)."""

    candidate_id: str
    field: str  # must be in ALLOWED_FIELDS
    value: str
    baseline_value: str | None
    rationale: str
    from_observation: str  # the mined weakness this answers (Phase 15 evidence)
    protected_targets_touched: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)


def _as_int(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def validate_candidate(c: Candidate) -> ValidationResult:
    """The integrity gate (roadmap 16.4). Fail closed: any doubt → reject."""
    reasons: list[str] = []

    if c.protected_targets_touched:
        reasons.append(
            f"names protected target(s): {', '.join(c.protected_targets_touched)}"
        )
    # A candidate whose declared field looks like a path into protected space
    # is rejected regardless of the allowlist (defense in depth) — via the
    # single shared decision, the same one the eval content-check will use.
    if is_protected_path(c.field):
        reasons.append(f"field {c.field!r} targets protected space")

    if c.field not in ALLOWED_FIELDS:
        reasons.append(
            f"field {c.field!r} is not allowlisted "
            f"(permitted: {', '.join(sorted(ALLOWED_FIELDS))})"
        )
        return ValidationResult(ok=False, reasons=tuple(reasons))

    # Budget monotonicity: tighten-only, and fail CLOSED when it can't be
    # verified. The check cannot run without a numeric baseline, so a missing
    # or non-integer baseline_value must REJECT — not skip. Otherwise a
    # hand-edited candidate that nulls baseline_value (load_candidate reads it
    # straight from JSON) sidesteps the tighten-only rule entirely and runs
    # the candidate arm with a loosened budget, inflating the eval signal.
    if c.field in MONOTONE_BUDGETS:
        new = _as_int(c.value)
        base = _as_int(c.baseline_value) if c.baseline_value is not None else None
        if new is None:
            reasons.append(f"{c.field} value {c.value!r} is not an integer")
        elif base is None:
            reasons.append(
                f"{c.field} is a tighten-only budget — a numeric baseline_value "
                "is required to prove it is not a loosening (fail closed)"
            )
        elif new > base:
            reasons.append(
                f"{c.field} may only be lowered ({base} → {new} is a loosening; "
                "budget increases are a human decision)"
            )

    if c.field == "review_mode" and c.value not in ("blocking", "advisory"):
        reasons.append(f"review_mode must be blocking|advisory, got {c.value!r}")

    if not c.rationale.strip():
        reasons.append("rationale is required (a candidate must justify itself)")
    if not c.from_observation.strip():
        reasons.append("from_observation is required (candidates trace to evidence)")

    return ValidationResult(ok=not reasons, reasons=tuple(reasons))


def env_overlay(c: Candidate) -> dict[str, str]:
    """The candidate as an environment overlay — how it's applied to an eval
    run without touching a line of source. Only produced for a valid field."""
    return {ALLOWED_FIELDS[c.field]: c.value}


def candidate_dir(root: Path, candidate_id: str) -> Path:
    return root / ".pxx" / "candidates" / candidate_id


def load_candidate(root: Path, candidate_id: str) -> Candidate | None:
    """Round-trip a persisted candidate by id, or None if absent."""
    f = candidate_dir(root, candidate_id) / "candidate.json"
    if not f.exists():
        return None
    d = json.loads(f.read_text(encoding="utf-8"))
    return Candidate(
        candidate_id=d["candidate_id"],
        field=d["field"],
        value=d["value"],
        baseline_value=d.get("baseline_value"),
        rationale=d.get("rationale", ""),
        from_observation=d.get("from_observation", ""),
        protected_targets_touched=tuple(d.get("protected_targets_touched", ())),
    )


def save_candidate(root: Path, c: Candidate) -> Path:
    """Persist a declarative candidate. `.pxx/` is gitignored — candidates are
    local proposals, not committed artifacts."""
    d = candidate_dir(root, c.candidate_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "candidate.json").write_text(
        json.dumps(
            {
                "candidate_id": c.candidate_id,
                "field": c.field,
                "value": c.value,
                "baseline_value": c.baseline_value,
                "rationale": c.rationale,
                "from_observation": c.from_observation,
                "protected_targets_touched": list(c.protected_targets_touched),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return d
