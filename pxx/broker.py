"""Phase 14: the action broker — the single authorization authority.

Every tool call flows through :class:`ActionBroker.authorize` (wired at the
``ToolRegistry.call`` choke point — one enforcement authority, no parallel
path; the F2/F5 lesson). The broker normalizes a proposed call into a typed
:class:`ToolAction`, checks its action class against the active
:class:`PermissionProfile` (per-class authorization, replacing the coarse
``can_write`` binary), enforces scope, and runs the PreToolUse hooks as the
deny substrate. Every decision emits ``tool_action_proposed`` +
``policy_decision`` events (these also feed the B10 vocabulary).

Fail closed: an unclassifiable action or a class with no profile entry is
denied; gate denials raise (``ScopeViolation`` / ``HookDenied``) and
propagate — they are never swallowed into model data.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .errors import ScopeViolation
from .safety import PermissionMode
from .workflow import ACTION_CLASSES, WORKFLOW_FILENAME, Workflow, load_workflow

if TYPE_CHECKING:
    from .tools import ToolContext, ToolSpec

log = logging.getLogger("pxx.broker")


class ActionClass(StrEnum):
    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    SHELL = "shell"
    NETWORK = "network"
    MEMORY = "memory"


class RiskTier(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class ToolAction:
    """A proposed tool call, normalized into a typed action."""

    tool_name: str
    args: dict[str, Any]
    targets: tuple[str, ...]  # affected paths (repo-relative or absolute)
    action_class: ActionClass
    mutating: bool
    risk_tier: RiskTier


@dataclass(frozen=True)
class Decision:
    """The broker's authorization result."""

    allowed: bool
    reason: str = ""
    review_tier: RiskTier | None = None  # boundary tier when not LOW


#: Built-in tool name -> (action class, target-extraction). Everything not
#: listed here and not an MCP tool is unclassifiable -> denied (fail closed).
def _targets_path(args: dict[str, Any]) -> tuple[str, ...]:
    path = args.get("path")
    return (str(path),) if path else ()


def _targets_optional_path(args: dict[str, Any]) -> tuple[str, ...]:
    return (str(args.get("path", ".")),)


_TOOL_CLASSES: dict[str, tuple[ActionClass, Any]] = {
    "read_file": (ActionClass.READ, _targets_path),
    "list_files": (ActionClass.READ, _targets_optional_path),
    "search_files": (ActionClass.READ, _targets_optional_path),
    "write_file": (ActionClass.WRITE, _targets_path),
    "edit_file": (ActionClass.WRITE, _targets_path),
    "run_shell": (ActionClass.SHELL, lambda args: ()),
    "recall_memory": (ActionClass.READ, lambda args: ()),
    "remember": (ActionClass.MEMORY, lambda args: ()),
}


def _risk_tier(action_class: ActionClass, mutating: bool, targets: tuple[str, ...]) -> RiskTier:
    if action_class in (ActionClass.SHELL, ActionClass.NETWORK, ActionClass.DELETE):
        return RiskTier.HIGH
    if action_class is ActionClass.WRITE:
        from .protected_paths import is_protected_path

        if any(is_protected_path(t) for t in targets):
            return RiskTier.HIGH
        return RiskTier.MEDIUM
    return RiskTier.LOW


def classify(tool_name: str, spec: ToolSpec, args: dict[str, Any]) -> ToolAction:
    """Normalize a proposed call into a ToolAction. Fail closed on an
    unclassifiable tool (raise ScopeViolation)."""
    entry = _TOOL_CLASSES.get(tool_name)
    if entry is not None:
        action_class, extract = entry
        targets = tuple(extract(args))
    elif tool_name.startswith("mcp__"):
        action_class = ActionClass.NETWORK  # remote server, off-machine
        targets = ()
    elif isinstance(spec.mutating, bool):
        # Extension tools (registered outside the built-in surface) classify
        # by their declared spec: mutating -> WRITE (still gated per-class).
        action_class = ActionClass.WRITE if spec.mutating else ActionClass.READ
        targets = tuple(_targets_path(args))
    else:
        raise ScopeViolation(
            f"tool {tool_name!r} is not classifiable into an action class (fail-closed: denied)"
        )
    mutating = bool(spec.mutating)
    return ToolAction(
        tool_name=tool_name,
        args=dict(args),
        targets=targets,
        action_class=action_class,
        mutating=mutating,
        risk_tier=_risk_tier(action_class, mutating, targets),
    )


@dataclass(frozen=True)
class PermissionProfile:
    """Permission mode -> allowed action classes (per-class authorization).

    Source of truth is the repo's WORKFLOW.md ``[permissions]`` section;
    :meth:`defaults` reproduces the built-in posture when no contract
    exists. An unknown mode maps to NOTHING (fail closed).
    """

    profiles: dict[str, frozenset[str]]

    @classmethod
    def defaults(cls) -> PermissionProfile:
        return cls(
            {
                "ask": frozenset({"read", "memory"}),
                "plan": frozenset({"read", "memory"}),
                "edit": frozenset({"read", "write", "memory", "shell"}),
                "auto": frozenset(ACTION_CLASSES),
            }
        )

    @classmethod
    def from_workflow(cls, workflow: Workflow) -> PermissionProfile:
        profiles = cls.defaults().profiles
        merged = {**profiles, **workflow.permissions}
        return cls(merged)

    def allowed(self, mode: PermissionMode) -> frozenset[str]:
        return self.profiles.get(str(mode), frozenset())


def resolve_profile(cwd: Path) -> PermissionProfile:
    """Profile for runs in ``cwd``: WORKFLOW.md when present (a malformed
    contract raises ConfigError — no silent defaults), else the built-in
    defaults."""
    if not (Path(cwd) / WORKFLOW_FILENAME).is_file():
        return PermissionProfile.defaults()
    return PermissionProfile.from_workflow(load_workflow(cwd))


class ActionBroker:
    """Deterministic per-action authorization against a permission profile.

    When a ``boundary_reviewer`` role is attached, HIGH-tier decisions are
    handed to it and its typed artifact is recorded on the event stream
    (specialized agents only at boundaries — Phase 22 amend)."""

    def __init__(self, profile: PermissionProfile, boundary_reviewer=None) -> None:
        self._profile = profile
        self._boundary_reviewer = boundary_reviewer

    async def authorize(self, action: ToolAction, ctx: ToolContext) -> Decision:
        """Authorize one action. Raises ScopeViolation/HookDenied on deny;
        always emits tool_action_proposed + policy_decision first."""
        await ctx.bus.emit(
            "tool_action_proposed",
            {
                "tool": action.tool_name,
                "action_class": str(action.action_class),
                "risk_tier": str(action.risk_tier),
                "targets": len(action.targets),
                "mutating": action.mutating,
            },
            session_id=ctx.session_id,
        )

        allowed_classes = self._profile.allowed(ctx.permission)
        if str(action.action_class) not in allowed_classes:
            decision = Decision(
                allowed=False,
                reason=(
                    f"action class '{action.action_class}' (tool {action.tool_name!r}) "
                    f"is not permitted under the '{ctx.permission}' profile"
                ),
                review_tier=action.risk_tier,
            )
            await self._emit_decision(ctx, action, decision)
            raise ScopeViolation(decision.reason)

        # Scope enforcement (raises ScopeViolation on violation).
        for target in action.targets:
            if action.mutating:
                ctx.scope.check_write(target, ctx.permission)
            else:
                ctx.scope.check(target)

        # PreToolUse hooks are the deny substrate (HookDenied propagates).
        await ctx.hooks.run_pre(action.tool_name, action.args)

        # Boundary role: HIGH-tier actions get a typed boundary-review
        # artifact on the stream (auditable; the deterministic decision above
        # stands — the role records, never overrides the gate).
        if action.risk_tier is RiskTier.HIGH and self._boundary_reviewer is not None:
            artifact = await self._boundary_reviewer.review_action(
                {
                    "tool": action.tool_name,
                    "action_class": str(action.action_class),
                    "allowed": True,
                    "reason": "profile + scope + hooks green",
                }
            )
            await ctx.bus.emit(
                "observation",
                {
                    "source": "boundary_reviewer",
                    "artifact_kind": artifact.kind,
                    "decision": artifact.payload.get("decision", ""),
                },
                session_id=ctx.session_id,
            )

        decision = Decision(
            allowed=True,
            review_tier=action.risk_tier if action.risk_tier is not RiskTier.LOW else None,
        )
        await self._emit_decision(ctx, action, decision)
        return decision

    @staticmethod
    async def _emit_decision(ctx: ToolContext, action: ToolAction, decision: Decision) -> None:
        await ctx.bus.emit(
            "policy_decision",
            {
                "tool": action.tool_name,
                "action_class": str(action.action_class),
                "allowed": decision.allowed,
                "reason": decision.reason,
                "review_tier": str(decision.review_tier) if decision.review_tier else "",
            },
            session_id=ctx.session_id,
        )


__all__ = [
    "ActionBroker",
    "ActionClass",
    "Decision",
    "PermissionProfile",
    "RiskTier",
    "ToolAction",
    "classify",
    "resolve_profile",
]
