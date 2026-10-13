"""Run-intent marker: a declared launcher intent, recorded metadata-only.

One ``(run_intent, run_intent_provenance)`` pair is computed at session
construction and recorded identically in ``task.json`` and the
``session_start`` event data (RUN-INTENT-SPEC-20261012). The marker makes
future experiment/eval/probe traffic distinguishable from operational runs
in recorded run metadata; existing unmarked history reads as ``unknown``.

Hard invariant: intent is NEVER consulted by any gate, budget, hook, route,
or outcome — a run cannot alter its own gating by declaring an intent
(metadata-only, AGENTS.md rule 3). Provenance is assigned by pxx from the
call site; callers never supply it.
"""

from __future__ import annotations

from enum import StrEnum


class RunIntent(StrEnum):
    """The declarable intent values plus the undeclared default."""

    OPERATIONAL = "operational"
    PROBE = "probe"
    EVAL = "eval"
    DRILL = "drill"
    UNKNOWN = "unknown"


#: Values a launcher may DECLARE via ``--intent`` or the ``run_intent``
#: config key. ``unknown`` is the absence of a declaration — never
#: declarable, and never implicitly operational.
DECLARED_INTENTS = frozenset(
    {RunIntent.OPERATIONAL, RunIntent.PROBE, RunIntent.EVAL, RunIntent.DRILL}
)

#: Provenance levels. ``first-party``: a pxx-internal call site set the value
#: programmatically at session construction. ``declared``: an external caller
#: declared it via CLI/config (unverified). There is no ``inferred`` level —
#: pxx applies no heuristics.
FIRST_PARTY = "first-party"
DECLARED = "declared"


def resolve_run_intent(declared: object, internal: RunIntent | None) -> tuple[str, str | None]:
    """Compute the one ``(run_intent, run_intent_provenance)`` pair for a session.

    ``internal`` is the pxx-internal call-site seam (``Session(run_intent=
    ...)``) — it wins over any config declaration and records
    ``first-party``. A recognized CLI/config ``declared`` value records
    ``declared``. Absent or unrecognized values record ``unknown`` with NO
    provenance (fail-safe, forward-compatible: an unrecognized recorded
    value reads as unknown, never guessed, never implicitly operational).
    An invalid ``internal`` value is a programming error and raises.
    """
    if internal is not None:
        return (str(RunIntent(internal)), FIRST_PARTY)
    if isinstance(declared, str) and declared in DECLARED_INTENTS:
        return (declared, DECLARED)
    return (str(RunIntent.UNKNOWN), None)


__all__ = ["DECLARED", "DECLARED_INTENTS", "FIRST_PARTY", "RunIntent", "resolve_run_intent"]
