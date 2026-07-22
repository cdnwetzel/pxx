"""Tests for pxx.broker: the single authorization authority."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pxx.broker import (
    ActionBroker,
    ActionClass,
    PermissionProfile,
    RiskTier,
    ToolAction,
    classify,
    resolve_profile,
)
from pxx.errors import ScopeViolation
from pxx.events import EventBus
from pxx.safety import PermissionMode
from pxx.tools import ToolContext, ToolRegistry, ToolSpec


def run(coro):
    return asyncio.run(coro)


def make_ctx(
    tmp_path: Path,
    permission: PermissionMode = PermissionMode.ASK,
    *,
    bus: EventBus | None = None,
    profile: PermissionProfile | None = None,
) -> ToolContext:
    from pxx.safety import HookRunner, ScopeGate

    return ToolContext(
        scope=ScopeGate(tmp_path),
        hooks=HookRunner(()),
        permission=permission,
        bus=bus or EventBus(),
        cwd=tmp_path,
        profile=profile,
    )


def _action(tool: str, cls: ActionClass, *, targets=(), mutating=False) -> ToolAction:
    return ToolAction(
        tool_name=tool,
        args={},
        targets=targets,
        action_class=cls,
        mutating=mutating,
        risk_tier=RiskTier.LOW,
    )


# --- classification -----------------------------------------------------------


def test_classify_builtin_tools() -> None:
    spec = ToolSpec(name="write_file", description="", parameters={}, mutating=True)
    action = classify("write_file", spec, {"path": "src/a.py", "content": "x"})
    assert action.action_class is ActionClass.WRITE
    assert action.targets == ("src/a.py",)
    assert action.risk_tier is RiskTier.MEDIUM


def test_classify_protected_target_is_high_risk() -> None:
    spec = ToolSpec(name="write_file", description="", parameters={}, mutating=True)
    action = classify("write_file", spec, {"path": "pxx/safety.py", "content": "x"})
    assert action.risk_tier is RiskTier.HIGH


def test_classify_mcp_tool_is_network() -> None:
    spec = ToolSpec(name="mcp__srv__ping", description="", parameters={}, mutating=False)
    action = classify("mcp__srv__ping", spec, {})
    assert action.action_class is ActionClass.NETWORK
    assert action.risk_tier is RiskTier.HIGH


def test_classify_extension_tool_falls_back_on_spec() -> None:
    spec = ToolSpec(name="custom", description="", parameters={}, mutating=True)
    assert classify("custom", spec, {}).action_class is ActionClass.WRITE
    spec_ro = ToolSpec(name="custom", description="", parameters={}, mutating=False)
    assert classify("custom", spec_ro, {}).action_class is ActionClass.READ


def test_classify_unclassifiable_fails_closed() -> None:
    class BadSpec:
        mutating = "yes"  # not a bool: garbage spec

    with pytest.raises(ScopeViolation, match="not classifiable"):
        classify("custom", BadSpec(), {})


# --- authorization matrix (defaults profile) -----------------------------------


_MATRIX = [
    # (action class, mode, allowed?)
    (ActionClass.READ, PermissionMode.ASK, True),
    (ActionClass.READ, PermissionMode.PLAN, True),
    (ActionClass.READ, PermissionMode.EDIT, True),
    (ActionClass.READ, PermissionMode.AUTO, True),
    (ActionClass.MEMORY, PermissionMode.ASK, True),
    (ActionClass.WRITE, PermissionMode.ASK, False),
    (ActionClass.WRITE, PermissionMode.PLAN, False),
    (ActionClass.WRITE, PermissionMode.EDIT, True),
    (ActionClass.WRITE, PermissionMode.AUTO, True),
    (ActionClass.SHELL, PermissionMode.ASK, False),
    (ActionClass.SHELL, PermissionMode.PLAN, False),
    (ActionClass.SHELL, PermissionMode.EDIT, True),
    (ActionClass.SHELL, PermissionMode.AUTO, True),
    (ActionClass.NETWORK, PermissionMode.ASK, False),
    (ActionClass.NETWORK, PermissionMode.EDIT, False),
    (ActionClass.NETWORK, PermissionMode.AUTO, True),
    (ActionClass.DELETE, PermissionMode.EDIT, False),
    (ActionClass.DELETE, PermissionMode.AUTO, True),
]


@pytest.mark.parametrize("cls,mode,allowed", _MATRIX)
def test_authorization_matrix(tmp_path: Path, cls, mode, allowed) -> None:
    broker = ActionBroker(PermissionProfile.defaults())
    action = _action("t", cls, mutating=cls is not ActionClass.READ)
    ctx = make_ctx(tmp_path, mode)
    if allowed:
        decision = run(broker.authorize(action, ctx))
        assert decision.allowed
    else:
        with pytest.raises(ScopeViolation, match="not permitted"):
            run(broker.authorize(action, ctx))


def test_denied_action_still_emits_both_events(tmp_path: Path) -> None:
    bus = EventBus()
    broker = ActionBroker(PermissionProfile.defaults())
    action = _action("write_file", ActionClass.WRITE, mutating=True)
    with pytest.raises(ScopeViolation):
        run(broker.authorize(action, make_ctx(tmp_path, PermissionMode.ASK, bus=bus)))
    kinds = [e.kind for e in bus.history]
    assert kinds == ["tool_action_proposed", "policy_decision"]
    assert bus.history[-1].data["allowed"] is False


def test_allowed_action_emits_review_tier(tmp_path: Path) -> None:
    bus = EventBus()
    broker = ActionBroker(PermissionProfile.defaults())
    action = _action("run_shell", ActionClass.SHELL, mutating=True)
    action = ToolAction(
        tool_name=action.tool_name,
        args={},
        targets=(),
        action_class=action.action_class,
        mutating=True,
        risk_tier=RiskTier.HIGH,
    )
    run(broker.authorize(action, make_ctx(tmp_path, PermissionMode.AUTO, bus=bus)))
    decision = bus.history[-1]
    assert decision.data["allowed"] is True
    assert decision.data["review_tier"] == "high"


def test_unknown_mode_allows_nothing(tmp_path: Path) -> None:
    broker = ActionBroker(PermissionProfile({"ask": frozenset({"read"})}))
    action = _action("read_file", ActionClass.READ)
    with pytest.raises(ScopeViolation, match="not permitted"):
        run(broker.authorize(action, make_ctx(tmp_path, PermissionMode.AUTO)))


# --- choke point: no bypass -----------------------------------------------------


def test_every_registry_call_goes_through_the_broker(tmp_path: Path, monkeypatch) -> None:
    """A spy on ActionBroker.authorize must see EVERY executed tool call."""

    calls: list[str] = []
    original = ActionBroker.authorize

    async def spy(self, action, ctx):
        calls.append(action.tool_name)
        return await original(self, action, ctx)

    monkeypatch.setattr(ActionBroker, "authorize", spy)

    class Echo:
        spec = ToolSpec(name="read_file", description="", parameters={}, mutating=False)

        async def run(self, args, ctx):
            return "ok"

    reg = ToolRegistry()
    reg.register(Echo())
    ctx = make_ctx(tmp_path, PermissionMode.AUTO)
    run(reg.call("read_file", {"path": "x.txt"}, ctx))
    run(reg.call("read_file", {"path": "y.txt"}, ctx))
    assert calls == ["read_file", "read_file"]


def test_resolve_profile_defaults_without_workflow(tmp_path: Path) -> None:
    profile = resolve_profile(tmp_path)
    assert profile.allowed(PermissionMode.ASK) == frozenset({"read", "memory"})


def test_resolve_profile_reads_workflow(tmp_path: Path) -> None:
    (tmp_path / "WORKFLOW.md").write_text(
        "# c\n\n```toml\nschema_version = 1\nhooks = []\n"
        '[states]\ninitial = "i"\nnames = ["i"]\nterminal = ["i"]\n'
        "[budgets]\n[commands]\n"
        '[permissions]\nask = ["read", "network"]\n'
        "[protected_paths]\npaths = []\n```\n"
    )
    profile = resolve_profile(tmp_path)
    assert profile.allowed(PermissionMode.ASK) == frozenset({"read", "network"})


# --- A0: protected paths are human-only — write denied in every mode --------------


def test_protected_write_denied_in_all_permission_modes(tmp_path: Path) -> None:
    """A0: the trusted control plane is human-only. A label is not a gate."""
    from pxx.broker import ActionClass, RiskTier, ToolAction

    broker = ActionBroker(PermissionProfile.defaults())
    action = ToolAction(
        tool_name="write_file",
        args={"path": "pxx/safety.py", "content": "x"},
        targets=("pxx/safety.py",),
        action_class=ActionClass.WRITE,
        mutating=True,
        risk_tier=RiskTier.HIGH,
    )
    for mode in (PermissionMode.ASK, PermissionMode.PLAN):
        # read-only modes: the write-class denial fires before the protected
        # check — also ScopeViolation, different reason
        with pytest.raises(ScopeViolation, match="not permitted"):
            run(broker.authorize(action, make_ctx(tmp_path, mode)))
    for mode in (PermissionMode.EDIT, PermissionMode.AUTO):
        # write-capable modes: the PROTECTED-PATH gate is what stops it
        bus = EventBus()
        with pytest.raises(ScopeViolation, match="protected path"):
            run(broker.authorize(action, make_ctx(tmp_path, mode, bus=bus)))
        denial = bus.history[-1]
        assert denial.kind == "policy_decision"
        assert denial.data["allowed"] is False


def test_evidence_plane_write_denied(tmp_path: Path) -> None:
    """The .pxx evidence plane (promotion records, config) is protected too."""
    from pxx.broker import ActionClass, RiskTier, ToolAction

    broker = ActionBroker(PermissionProfile.defaults())
    for target in ("pxx.toml", ".pxx/config.toml", ".pxx/promotions/x.json"):
        action = ToolAction(
            tool_name="write_file",
            args={"path": target, "content": "x"},
            targets=(target,),
            action_class=ActionClass.WRITE,
            mutating=True,
            risk_tier=RiskTier.HIGH,
        )
        with pytest.raises(ScopeViolation, match="protected path"):
            run(broker.authorize(action, make_ctx(tmp_path, PermissionMode.AUTO)))


def test_optimizer_work_products_stay_writable(tmp_path: Path) -> None:
    """Candidate content and skills are the optimizer's own plane — allowed."""
    from pxx.broker import ActionClass, RiskTier, ToolAction

    broker = ActionBroker(PermissionProfile.defaults())
    action = ToolAction(
        tool_name="write_file",
        args={"path": ".pxx/skills/notes.md", "content": "x"},
        targets=(".pxx/skills/notes.md",),
        action_class=ActionClass.WRITE,
        mutating=True,
        risk_tier=RiskTier.MEDIUM,
    )
    decision = run(broker.authorize(action, make_ctx(tmp_path, PermissionMode.AUTO)))
    assert decision.allowed
