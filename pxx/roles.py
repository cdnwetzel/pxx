"""Phase 22 amend: specialized boundary role agents + typed handoffs.

Specialized agents exist ONLY at isolation/authority boundaries (per the
roadmap): a Reproducer at task intake, a Boundary-Reviewer at high-risk
broker decisions, an Artifact-Reviewer at the goal integration boundary.
Every handoff between nodes is a TYPED artifact (schema-versioned,
fail-closed on malformed) — never free text. Planner skills are versioned
and loadable from the B5 skill layer.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol

from .errors import PxxError

log = logging.getLogger("pxx.roles")

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class HandoffArtifact:
    """A typed, schema-versioned handoff between roles/nodes."""

    kind: str  # "reproduction" | "boundary_review" | "artifact_review"
    payload: dict[str, Any]
    schema_version: int = SCHEMA_VERSION

    def to_json(self) -> str:
        return json.dumps(
            {
                "schema_version": self.schema_version,
                "kind": self.kind,
                "payload": self.payload,
            },
            sort_keys=True,
        )

    @staticmethod
    def from_json(text: str) -> HandoffArtifact:
        """Fail-closed parse: malformed artifacts raise PxxError."""
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PxxError(f"malformed handoff artifact: {exc}") from exc
        if not isinstance(data, dict):
            raise PxxError("handoff artifact must be an object")
        if data.get("schema_version") != SCHEMA_VERSION:
            raise PxxError(f"unsupported artifact schema_version: {data.get('schema_version')!r}")
        kind = data.get("kind")
        payload = data.get("payload")
        if kind not in ("reproduction", "boundary_review", "artifact_review"):
            raise PxxError(f"unknown artifact kind: {kind!r}")
        if not isinstance(payload, dict):
            raise PxxError("handoff artifact payload must be an object")
        return HandoffArtifact(kind=kind, payload=payload)


class Reproducer(Protocol):
    """Task-intake boundary: turn a bug report into a failing reproduction."""

    async def reproduce(self, report: str) -> HandoffArtifact:
        """payload: {failing_command, expected, observed}"""
        ...


class BoundaryReviewer(Protocol):
    """Authority boundary: review a HIGH-tier broker action before it runs."""

    async def review_action(self, action: dict[str, Any]) -> HandoffArtifact:
        """payload: {action_class, tool, decision: allow|deny, rationale}"""
        ...


class ArtifactReviewer(Protocol):
    """Integration boundary: review the merged artifact before it ships."""

    async def review_artifact(self, diff: str, artifact_id: str) -> HandoffArtifact:
        """payload: {artifact_id, ok, issues: [str]}"""
        ...


@dataclass(frozen=True)
class DeterministicBoundaryReviewer:
    """A boundary reviewer that needs no model: HIGH-tier actions are
    recorded and denied unless the profile explicitly allows them (the
    broker already decided; this role produces the auditable artifact)."""

    async def review_action(self, action: dict[str, Any]) -> HandoffArtifact:
        allowed = bool(action.get("allowed"))
        return HandoffArtifact(
            kind="boundary_review",
            payload={
                "action_class": str(action.get("action_class", "")),
                "tool": str(action.get("tool", "")),
                "decision": "allow" if allowed else "deny",
                "rationale": str(action.get("reason", "profile decision")),
            },
        )


@dataclass(frozen=True)
class DeterministicArtifactReviewer:
    """An artifact reviewer that needs no model: rejects artifacts that
    touch protected paths, approves everything else (with an audit trail)."""

    async def review_artifact(self, diff: str, artifact_id: str) -> HandoffArtifact:
        from .protected_paths import is_protected_path

        issues: list[str] = []
        for line in diff.splitlines():
            if line.startswith(("+++ b/", "--- a/")):
                rel = line[6:].strip()
                if is_protected_path(rel):
                    issues.append(f"touches protected path: {rel}")
        return HandoffArtifact(
            kind="artifact_review",
            payload={
                "artifact_id": artifact_id,
                "ok": not issues,
                "issues": issues,
            },
        )


# --- versioned planner skills (B5 skill layer) -------------------------------------


@dataclass(frozen=True)
class PlannerSkill:
    """A versioned planner skill loaded from the memory skill layer."""

    name: str
    version: str
    content: str


def load_planner_skill(store: Any, project: str, name: str) -> PlannerSkill:
    """Load the newest version of a planner skill from the B5 skill layer.

    Skills are skill-layer observations whose content carries a
    ``version: X`` marker. Fail-closed: unknown skills or malformed content
    raise PxxError.
    """
    candidates = [
        obs
        for obs in store.list(project, layer="skill")
        if obs.content.startswith(f"name: {name}\n")
    ]
    if not candidates:
        raise PxxError(f"no planner skill named {name!r} in the skill layer")
    obs = max(candidates, key=lambda o: o.created_at)
    lines = obs.content.splitlines()
    version = ""
    for line in lines:
        if line.startswith("version: "):
            version = line.split(":", 1)[1].strip()
            break
    if not version:
        raise PxxError(f"planner skill {name!r} has no version marker")
    return PlannerSkill(name=name, version=version, content=obs.content)


__all__ = [
    "SCHEMA_VERSION",
    "ArtifactReviewer",
    "BoundaryReviewer",
    "DeterministicArtifactReviewer",
    "DeterministicBoundaryReviewer",
    "HandoffArtifact",
    "PlannerSkill",
    "Reproducer",
    "load_planner_skill",
]
