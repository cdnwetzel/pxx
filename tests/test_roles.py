"""Tests for pxx.roles: boundary role agents + typed handoffs (B10.2)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pxx.errors import PxxError
from pxx.memory.store import MemoryStore
from pxx.roles import (
    DeterministicArtifactReviewer,
    DeterministicBoundaryReviewer,
    HandoffArtifact,
    load_planner_skill,
)


def run(coro):
    return asyncio.run(coro)


def test_handoff_artifact_roundtrip():
    artifact = HandoffArtifact(kind="reproduction", payload={"failing_command": "pytest"})
    parsed = HandoffArtifact.from_json(artifact.to_json())
    assert parsed.kind == "reproduction"
    assert parsed.payload == {"failing_command": "pytest"}
    assert parsed.schema_version == 1


def test_handoff_artifact_fail_closed():
    with pytest.raises(PxxError, match="malformed"):
        HandoffArtifact.from_json("{not json")
    with pytest.raises(PxxError, match="schema_version"):
        HandoffArtifact.from_json('{"schema_version": 99, "kind": "reproduction", "payload": {}}')
    with pytest.raises(PxxError, match="unknown artifact kind"):
        HandoffArtifact.from_json('{"schema_version": 1, "kind": "mystery", "payload": {}}')
    with pytest.raises(PxxError, match="payload"):
        HandoffArtifact.from_json('{"schema_version": 1, "kind": "reproduction", "payload": []}')


def test_artifact_reviewer_rejects_protected_content():
    reviewer = DeterministicArtifactReviewer()
    diff = "--- a/pxx/safety.py\n+++ b/pxx/safety.py\n@@ -1 +1 @@\n-x\n+y\n"
    artifact = run(reviewer.review_artifact(diff, "art-1"))
    assert artifact.payload["ok"] is False
    assert any("protected" in i for i in artifact.payload["issues"])
    clean = run(
        reviewer.review_artifact("--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-x\n+y\n", "art-2")
    )
    assert clean.payload["ok"] is True


def test_boundary_reviewer_produces_typed_artifact():
    reviewer = DeterministicBoundaryReviewer()
    artifact = run(
        reviewer.review_action(
            {
                "tool": "run_shell",
                "action_class": "shell",
                "allowed": True,
                "reason": "profile green",
            }
        )
    )
    assert artifact.kind == "boundary_review"
    assert artifact.payload["decision"] == "allow"
    assert artifact.payload["tool"] == "run_shell"


def test_broker_high_tier_invokes_boundary_reviewer(tmp_path):
    from pxx.broker import ActionBroker, PermissionProfile, classify
    from pxx.events import EventBus
    from pxx.safety import HookRunner, PermissionMode, ScopeGate
    from pxx.tools import ToolContext, ToolSpec

    bus = EventBus()
    broker = ActionBroker(
        PermissionProfile.defaults(),
        boundary_reviewer=DeterministicBoundaryReviewer(),
    )
    ctx = ToolContext(
        scope=ScopeGate(tmp_path),
        hooks=HookRunner(()),
        permission=PermissionMode.AUTO,
        bus=bus,
        cwd=tmp_path,
    )
    spec = ToolSpec(name="run_shell", description="", parameters={}, mutating=True)
    action = classify("run_shell", spec, {"command": "ls"})
    decision = run(broker.authorize(action, ctx))
    assert decision.allowed
    boundary_events = [
        e
        for e in bus.history
        if e.kind == "observation" and e.data.get("source") == "boundary_reviewer"
    ]
    assert boundary_events, "HIGH-tier action produced no boundary-review artifact"
    assert boundary_events[0].data["artifact_kind"] == "boundary_review"


def test_load_planner_skill_from_skill_layer(tmp_path: Path):
    store = MemoryStore(tmp_path / "mem.db")
    run(
        store.add(
            "proj",
            "skill",
            "name: goal-planner\nversion: 2\nplan carefully, then execute",
            layer="skill",
        )
    )
    skill = load_planner_skill(store, "proj", "goal-planner")
    assert skill.version == "2"
    assert "plan carefully" in skill.content
    store.close()


def test_load_planner_skill_missing_or_unversioned(tmp_path: Path):
    store = MemoryStore(tmp_path / "mem.db")
    with pytest.raises(PxxError, match="no planner skill"):
        load_planner_skill(store, "proj", "ghost")
    run(store.add("proj", "skill", "name: badskill\nno version here", layer="skill"))
    with pytest.raises(PxxError, match="no version marker"):
        load_planner_skill(store, "proj", "badskill")
    store.close()
