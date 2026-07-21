"""Terminal codes and run outcomes.

Terminal codes replace 1.x's message-parsing: every run ends with exactly one
machine-readable TERMINAL code (plus optional contributing codes), recorded
in the hash-chained audit log.

Taxonomy (Phase 12.2 — the canonical 18, with repo-specific additions):

- the canonical 18: COMPLETED (≡ APPROVED), EDIT_FAILED, EDIT_TIMEOUT,
  TEST_RUN_FAILED, TEST_REGRESSION, NO_TEST_PROGRESS, LINT_BLOCKED,
  REVIEW_REJECTED, REVIEW_UNAVAILABLE, REVIEW_EMPTY, REVIEW_UNPARSEABLE,
  OUT_OF_SCOPE, DIFF_CAP (≡ DIFF_BUDGET_EXCEEDED), ROUND_CAP
  (≡ ROUND_CAP_EXCEEDED), BUDGET_EXCEEDED (≡ TIME_BUDGET_EXCEEDED),
  HOOKS_MISSING, MODEL_UNAVAILABLE, CONFIGURATION_INVALID;
- plus INTERRUPTED (roadmap lesson #4), CLARIFICATION_REQUIRED (B1.2's
  ambiguity gate), and HOOK_DENIED (a hook actively denied — distinct from
  HOOKS_MISSING, where a required hook is not configured).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class TerminalCode(StrEnum):
    # terminal success / stop codes
    COMPLETED = "COMPLETED"  # ≡ canonical APPROVED
    INTERRUPTED = "INTERRUPTED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"  # ≡ TIME_BUDGET_EXCEEDED
    ROUND_CAP = "ROUND_CAP"  # ≡ ROUND_CAP_EXCEEDED
    DIFF_CAP = "DIFF_CAP"  # ≡ DIFF_BUDGET_EXCEEDED
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    # edit leg
    EDIT_FAILED = "EDIT_FAILED"
    EDIT_TIMEOUT = "EDIT_TIMEOUT"
    # test leg
    TEST_RUN_FAILED = "TEST_RUN_FAILED"  # the suite could not run (infra)
    TEST_REGRESSION = "TEST_REGRESSION"  # new failures beyond baseline
    NO_TEST_PROGRESS = "NO_TEST_PROGRESS"  # failing set stops improving
    LINT_BLOCKED = "LINT_BLOCKED"
    # review leg
    REVIEW_REJECTED = "REVIEW_REJECTED"
    REVIEW_UNAVAILABLE = "REVIEW_UNAVAILABLE"
    REVIEW_EMPTY = "REVIEW_EMPTY"
    REVIEW_UNPARSEABLE = "REVIEW_UNPARSEABLE"
    # boundary / config / model
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    HOOK_DENIED = "HOOK_DENIED"
    HOOKS_MISSING = "HOOKS_MISSING"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    CONFIGURATION_INVALID = "CONFIGURATION_INVALID"
    LOOP_DETECTED = "LOOP_DETECTED"  # semantic oscillation (Phase 15 amend)
    MERGE_CONFLICT = "MERGE_CONFLICT"  # goal integration merge conflict (Phase 22)


@dataclass(frozen=True)
class RunOutcome:
    """The result of one pxx run (session or loop)."""

    code: TerminalCode
    summary: str
    rounds: int = 0
    tokens: int = 0
    diff_lines: int = 0
    cost_usd: float | None = None  # None = unknown/unpriced; never fabricated
    findings: tuple[dict, ...] = field(default_factory=tuple)
    session_id: str = ""
    contributing_codes: tuple[str, ...] = ()  # secondary causes (one terminal)
    # Phase 12.1 field set (per-leg evidence)
    edit_seconds: float = 0.0
    test_seconds: float = 0.0
    review_seconds: float = 0.0
    files_changed: int = 0
    baseline_failures: int = 0
    introduced_failures: int = 0
    terminal_failures: int = 0
    lint_errors: int = 0
    findings_by_severity: dict[str, int] = field(default_factory=dict)
    unparseable_review_count: int = 0
    injected_observation_ids: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.code is TerminalCode.COMPLETED

    @property
    def accepted(self) -> bool:
        """True when the run completed and was accepted (12.1)."""
        return self.code is TerminalCode.COMPLETED
