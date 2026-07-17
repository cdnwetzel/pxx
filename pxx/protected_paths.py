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

import posixpath

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


def _canonical(path: str) -> str | None:
    """Normalize to a repo-relative, forward-slash, casefolded path — or None
    if it cannot be safely classified (empty, absolute, or escaping the repo).
    None means the caller must fail closed: a boundary that can't classify an
    input must treat it as protected, not wave it through."""
    if not isinstance(path, str) or not path.strip():
        return None
    p = path.strip().replace("\\", "/")  # windows-style diff paths
    if p.startswith("/"):
        return None  # absolute — not a repo-relative target
    if p.startswith("./"):
        p = p[2:]  # PREFIX strip, never lstrip('./') (that eats leading dots)
    norm = posixpath.normpath(p)  # collapses a/../b and ./
    if norm == ".." or norm.startswith("../"):
        return None  # escapes the repo root
    # casefold so a case-insensitive filesystem (macOS default) can't dodge
    # the check via PXX/EVALUATION.PY — over-protecting a case-variant that
    # doesn't exist costs nothing; under-protecting is the security hole.
    return norm.casefold()


def is_protected_path(path: str) -> bool:
    """True if ``path`` is (or is inside) a protected target. The single
    decision both the candidate validator and the eval content-check use.

    Fails CLOSED: normalizes the diff-path shapes a candidate can carry
    (``a/``|``b/`` git prefixes, ``..`` traversal, backslashes, case, ``./``)
    and returns True for anything it cannot cleanly classify — an unrecognized
    path is the one case a boundary must never allow. Normalization lives HERE,
    not in callers, so the config validator and the content-check can't drift
    into normalizing differently."""
    if not isinstance(path, str) or not path.strip():
        return True  # unclassifiable → protected

    # Consider the path AND its git-diff-prefix-stripped form: raw `git diff`
    # prefixes every path with a/ or b/. Stripping can only ADD protection
    # (we protect if EITHER form resolves into protected space), never remove.
    raw = path.strip().replace("\\", "/")
    forms = [raw]
    for pre in ("a/", "b/"):
        if raw.startswith(pre):
            forms.append(raw[len(pre) :])

    prefixes = [pre.casefold() for pre in PROTECTED_PREFIXES]
    for form in forms:
        c = _canonical(form)
        if c is None:
            return True  # a form we can't classify → fail closed
        for pre in prefixes:
            if c == pre.rstrip("/") or c.startswith(pre):
                return True
    return False
