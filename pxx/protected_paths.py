"""The ONE authoritative optimizer-protected path set.

Phase 0.3/16: everything in :data:`PROTECTED_PREFIXES` belongs to the trusted
control plane — the optimizer plane (candidates, autopromote, mining) may
never write to it. ``docs/TRUST_BOUNDARY.md`` mirrors this set exactly and a
test pins the two together in both directions.

Fail-closed: any path that cannot be cleanly classified as repo-relative and
unprotected is treated as protected. Paths are normalized (backslashes ->
slashes, a single leading ``./`` stripped — never ``lstrip`` of a character
set, which was the 1.x bug that silently unprotected ``.github/``) and
anchored at the repo root before matching.
"""

from __future__ import annotations

import posixpath
import re
from pathlib import Path

#: Repo-relative protected set. Entries ending in ``/`` are directory
#: prefixes (everything beneath them is protected); all other entries are
#: exact file matches.
PROTECTED_PREFIXES: tuple[str, ...] = (
    # Trusted control plane: gates, broker, workflow contract, governance.
    "pxx/safety.py",
    "pxx/errors.py",
    "pxx/broker.py",
    "pxx/workflow.py",
    "pxx/clarify.py",
    "pxx/governance.py",
    "pxx/protected_paths.py",
    # The repository-owned workflow contract itself.
    "WORKFLOW.md",
    # Evaluation plane: evaluators, cases, hidden checks.
    "pxx/eval/",
    # Improvement plane policy: candidate validation, promotion, autopromote.
    "pxx/improve/",
    # The eval corpus itself (including hidden/adversarial cases).
    "evals/",
    # CI/release automation.
    ".github/",
    # The human-readable trust-boundary contract.
    "docs/TRUST_BOUNDARY.md",
    # The tests that pin the control plane itself.
    "tests/test_safety.py",
    "tests/test_governance.py",
    "tests/test_protected_paths.py",
    "tests/test_broker.py",
    "tests/test_workflow.py",
    "tests/test_clarify.py",
    # Release smoke gate.
    "scripts/smoke-package.sh",
)

_DRIVE_RE = re.compile(r"^[A-Za-z]:/")


def is_protected_path(path: str | Path) -> bool:
    """Return True if ``path`` names a protected file or lives under one.

    ``path`` is interpreted relative to the repository root. Normalization:
    backslashes become slashes, a single leading ``./`` is stripped, and
    ``.``/``..`` segments are resolved lexically. Empty, absolute,
    tilde-prefixed, drive-letter, NUL-containing, or root-escaping paths are
    unclassifiable and therefore protected (fail-closed).
    """
    raw = str(path)
    if not raw or not raw.strip() or "\x00" in raw:
        return True
    norm = raw.replace("\\", "/")
    if norm.startswith(("/", "~")) or _DRIVE_RE.match(norm):
        return True
    norm = norm.removeprefix("./")
    norm = posixpath.normpath(norm)
    if norm in ("", ".", "..") or norm.startswith(("../", "/")):
        return True
    for prefix in PROTECTED_PREFIXES:
        if prefix.endswith("/"):
            if norm == prefix[:-1] or norm.startswith(prefix):
                return True
        elif norm == prefix:
            return True
    return False
