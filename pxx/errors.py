"""pxx error hierarchy.

Gates raise; telemetry suppresses. Anything that stops work derives from
``GateError`` so the session layer can map it to a ``TerminalCode``.
"""

from __future__ import annotations


class PxxError(Exception):
    """Base class for all pxx errors."""


class ConfigError(PxxError):
    """Invalid configuration (unknown key, bad value, conflicting layers)."""


class GateError(PxxError):
    """A deterministic gate denied an action. Fail-closed; never overridable
    by model judgment."""


class ScopeViolation(GateError):
    """A path escaped the session scope (after canonicalization)."""


class HookDenied(GateError):
    """A PreToolUse/PostToolUse hook exited with code 2."""


class HooksMissing(GateError):
    """A hook is required for this action but none is configured."""


class ShellUnguarded(HooksMissing):
    """run_shell in a write-capable mode with NO shell safeguard configured
    (no covering PreToolUse hook, no available sandbox, no opt-in).

    A subclass so existing ``except HooksMissing`` handling (the
    HOOKS_MISSING terminal) is unchanged under ``shell_unguarded = "abort"``.
    Distinct from a *broken* safeguard (``sandbox_shell`` with no sandboxer),
    which stays a plain ``HooksMissing``: that is a setup error the operator
    must see, never something to refuse back to the model.
    """


class BudgetExceeded(GateError):
    """A configured budget (rounds/tokens/cost/diff/wall-clock) tripped."""

    def __init__(self, budget: str, limit: str) -> None:
        self.budget = budget
        super().__init__(f"budget exceeded: {budget} (limit {limit})")


class GateFailed(GateError):
    """A post-hoc verification gate (tests, lint, review) failed."""


class BackendUnavailable(PxxError):
    """The selected backend cannot run (missing binary, unreachable endpoint)."""


class BackendError(PxxError):
    """The backend failed mid-run.

    ``code`` (optional) lets a backend name its specific terminal cause
    (e.g. TerminalCode.EDIT_FAILED); the session maps it when present.
    """

    def __init__(self, message: str, *, code: object = None) -> None:
        super().__init__(message)
        self.code = code


class CandidateInvalid(PxxError):
    """A declarative improvement candidate failed integrity/policy validation
    (non-allowlisted target, protected path, budget increase, missing
    rationale/evidence, unclassifiable path, hash mismatch, overwrite)."""
