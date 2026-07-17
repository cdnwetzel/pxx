"""The single authoritative protected-path set (roadmap trust boundary).

This is the ONE list of paths the optimizer/candidate generator must never
target — the evaluator, the gates, the hidden checks, and the config that
governs them. Before this module existed the set was expressed three times
(candidates.PROTECTED_PREFIXES, .aiderignore, docs/TRUST_BOUNDARY.md), hand-
synced, only partially test-pinned — a drift hazard that config candidates
never exercised but *content* candidates (prompt/skill rewrites, which mutate
files) would cross on day one. Now:

- ``is_protected_path`` is the one decision function; the candidate validator
  and the eval content-check both call it — no scattered prefix logic.
- ``.aiderignore`` and ``docs/TRUST_BOUNDARY.md`` are static mirrors (aider
  can't import Python; the doc is prose), held to this list by tests. They may
  be supersets (``.aiderignore`` also lists ordinary config guardrails), but
  every path here MUST appear in both — the tests fail on drift in that
  direction.

This module is itself protected (a candidate rewriting the protected list
would defeat the whole boundary).
"""

from __future__ import annotations

# Prefix-matched. A path is protected if it equals an entry or starts with one
# (so "evals/" covers the whole tree, "pxx/loop.py" covers exactly that file).
PROTECTED_PREFIXES: tuple[str, ...] = (
    # Gates, evaluator, and the self-improvement machinery — a candidate must
    # not edit anything that grades, projects, proposes, or identifies it.
    "pxx/safety.py",
    "pxx/scope.py",
    "pxx/governance.py",
    "pxx/review_gate.py",
    "pxx/loop.py",
    "pxx/evaluation.py",
    "pxx/calibration.py",
    "pxx/promotion.py",
    "pxx/candidates.py",
    "pxx/candidate_eval.py",
    "pxx/improvement.py",
    "pxx/protected_paths.py",
    # Fixtures + hidden checks.
    "evals/",
    # Release path and the guardrail config that governs behavior.
    ".github/workflows/",
    ".aiderignore",
    "config/",
    "pyproject.toml",
)


def is_protected_path(path: str) -> bool:
    """True if ``path`` is (or is inside) a protected target. The single
    decision both the candidate validator and the eval content-check use.

    NB: strip a leading ``./`` as a PREFIX, not via ``lstrip("./")`` — the
    latter is a char-set strip that would eat the leading dot of ``.github``
    and ``.aiderignore`` and silently unprotect them."""
    p = path[2:] if path.startswith("./") else path
    return any(p == pre.rstrip("/") or p.startswith(pre) for pre in PROTECTED_PREFIXES)
