"""Phase 12.3: verification packets — an inspectable evidence chain per run.

Projects one run's manifest, outcome, and event stream (run-dir
``events.jsonl``, falling back to the hash-chained audit stream under
``state_dir/audit/``) into a :class:`VerificationPacket`: who ran
(agent_version_id), how it ended (terminal code), what it consumed
(budgets), which deterministic gates fired and how they decided, and the
derived risks. Pure projection — no I/O at import time.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import PxxError

log = logging.getLogger("pxx.verify")


@dataclass(frozen=True)
class GateRecord:
    """One fired gate and its deterministic decision (metadata-only)."""

    gate: str
    allowed: bool
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VerificationPacket:
    run_id: str
    agent_version_id: str
    code: str  # terminal code; "" when unknown
    budgets: dict[str, Any]  # budgets consumed (guard snapshot / outcome)
    gates: tuple[GateRecord, ...]
    rounds: int
    risks: tuple[str, ...]
    session_id: str = ""


def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return
    for raw in lines:
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            yield data


def _run_dir_events(run_dir: Path) -> list[dict[str, Any]]:
    return list(_iter_jsonl(run_dir / "events.jsonl"))


def _audit_events(state_dir: Path, run_id: str) -> list[dict[str, Any]]:
    """Fall back to the audit stream for one run.

    Only session_start/session_end carry ``run_id`` in their data, so first
    resolve the session id(s) tagged with the run, then collect every event
    of those sessions in stream order.
    """
    audit_dir = Path(state_dir) / "audit"
    try:
        files = sorted(audit_dir.glob("*.jsonl"))
    except OSError:
        return []
    all_events: list[dict[str, Any]] = []
    for path in files:
        for record in _iter_jsonl(path):
            event = record.get("event")
            if isinstance(event, dict):
                all_events.append(event)
    session_ids = {
        str(event.get("session_id"))
        for event in all_events
        if isinstance(event.get("data"), dict) and event["data"].get("run_id") == run_id
    }
    session_ids.discard("")
    if not session_ids:
        return []
    return [e for e in all_events if str(e.get("session_id")) in session_ids]


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _first(events: Iterable[dict[str, Any]], kind: str) -> dict[str, Any]:
    for event in events:
        if event.get("kind") == kind:
            return event
    return {}


def _last(events: Iterable[dict[str, Any]], kind: str) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for event in events:
        if event.get("kind") == kind:
            found = event
    return found


def _derive_risks(code: str, gates: tuple[GateRecord, ...]) -> tuple[str, ...]:
    risks: list[str] = []
    for gate in gates:
        if not gate.allowed:
            risks.append(f"gate_denied:{gate.gate}")
    if code and code != "COMPLETED":
        risks.append(f"terminal:{code}")
    if not code:
        risks.append("terminal:UNKNOWN")
    return tuple(risks)


def packet_for_run(state_dir: Path, run_id: str) -> VerificationPacket:
    """Project the verification packet for one run.

    Primary source: ``state_dir/runs/<run_id>/`` (manifest.json,
    outcome.json, events.jsonl). When the run dir is absent or empty, falls
    back to the audit stream (``state_dir/audit/*.jsonl``) filtered by
    run_id. Raises :class:`PxxError` when no evidence exists (fail-closed).
    """
    state_dir = Path(state_dir)
    run_dir = state_dir / "runs" / run_id
    manifest = _read_json(run_dir / "manifest.json")
    outcome = _read_json(run_dir / "outcome.json")
    events = _run_dir_events(run_dir)
    if not events:
        events = _audit_events(state_dir, run_id)
    if not (manifest or outcome or events):
        raise PxxError(f"no evidence for run: {run_id}")

    start = _first(events, "session_start")
    end = _last(events, "session_end")
    start_data = start.get("data", {}) if start else {}
    end_data = end.get("data", {}) if end else {}

    agent_version_id = str(
        manifest.get("agent_version_id")
        or outcome.get("agent_version_id")
        or start_data.get("agent_version_id")
        or ""
    )
    code = str(outcome.get("code") or end_data.get("code") or "")
    session_id = str(
        outcome.get("session_id") or start.get("session_id") or end.get("session_id") or ""
    )
    try:
        rounds = int(outcome.get("rounds") or end_data.get("rounds") or 0)
    except (TypeError, ValueError):
        rounds = 0  # malformed field degrades to neutral, never a crash

    budgets: dict[str, Any] = {}
    for key in ("rounds", "tokens", "diff_lines", "cost_usd"):
        if key in outcome:
            budgets[key] = outcome[key]
    snapshot = end_data.get("budgets")
    if isinstance(snapshot, dict):
        budgets.update(snapshot)  # guard snapshot is authoritative

    gates = tuple(
        GateRecord(
            gate=str(data.get("gate") or "unknown"),
            allowed=bool(data.get("allowed", True)),
            data={k: v for k, v in data.items() if k not in ("gate", "allowed")},
        )
        for event in events
        if event.get("kind") == "gate_decision"
        for data in [event.get("data", {})]
        if isinstance(data, dict)
    )

    return VerificationPacket(
        run_id=run_id,
        agent_version_id=agent_version_id,
        code=code,
        budgets=budgets,
        gates=gates,
        rounds=rounds,
        risks=_derive_risks(code, gates),
        session_id=session_id,
    )


def format_packet(packet: VerificationPacket) -> str:
    """Human-readable rendering of a verification packet."""
    lines = [
        f"run: {packet.run_id}",
        f"agent_version_id: {packet.agent_version_id or 'unknown'}",
        f"session: {packet.session_id or 'unknown'}",
        f"terminal: {packet.code or 'UNKNOWN'} (rounds={packet.rounds})",
    ]
    if packet.budgets:
        consumed = " ".join(f"{k}={v}" for k, v in sorted(packet.budgets.items()))
        lines.append(f"budgets: {consumed}")
    else:
        lines.append("budgets: none recorded")
    if packet.gates:
        lines.append("gates:")
        for gate in packet.gates:
            verdict = "allow" if gate.allowed else "DENY"
            detail = ""
            if gate.data:
                detail = " " + json.dumps(gate.data, sort_keys=True, default=str)
            lines.append(f"  [{verdict:5}] {gate.gate}{detail}")
    else:
        lines.append("gates: none fired")
    if packet.risks:
        lines.append("risks:")
        lines.extend(f"  - {risk}" for risk in packet.risks)
    else:
        lines.append("risks: none")
    return "\n".join(lines)
