"""Tests for pxx.verify: VerificationPacket projection + formatting."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pxx.errors import PxxError
from pxx.verify import VerificationPacket, format_packet, packet_for_run

RUN_ID = "20260701T000000Z-cafebabe"


def _event(kind: str, data: dict, seq: int, session_id: str = "sess-1") -> dict:
    return {"kind": kind, "data": data, "session_id": session_id, "ts": 1.0, "seq": seq}


def _seed_run_dir(state_dir: Path) -> Path:
    run_dir = state_dir / "runs" / RUN_ID
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps({"agent_version_id": "vid-123", "backend": "mock", "model": "m1"})
    )
    (run_dir / "outcome.json").write_text(
        json.dumps(
            {
                "run_id": RUN_ID,
                "session_id": "sess-1",
                "code": "DIFF_CAP",
                "rounds": 3,
                "tokens": 42,
                "diff_lines": 500,
                "cost_usd": 0.0,
            }
        )
    )
    events = [
        _event("session_start", {"run_id": RUN_ID, "agent_version_id": "vid-123"}, 1),
        _event("gate_decision", {"gate": "scope_recheck", "allowed": True}, 2),
        _event(
            "gate_decision",
            {"gate": "diff_budget", "allowed": False, "diff_lines": 500, "limit": 400},
            3,
        ),
        _event(
            "session_end",
            {
                "run_id": RUN_ID,
                "code": "DIFF_CAP",
                "rounds": 3,
                "budgets": {
                    "rounds": 3,
                    "tokens": 42,
                    "diff_lines": 500,
                    "remaining_seconds": 1200.0,
                },
            },
            4,
        ),
    ]
    (run_dir / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    return run_dir


def test_packet_projects_run_dir(tmp_path: Path) -> None:
    state = tmp_path / "state"
    _seed_run_dir(state)
    packet = packet_for_run(state, RUN_ID)
    assert packet.run_id == RUN_ID
    assert packet.agent_version_id == "vid-123"
    assert packet.code == "DIFF_CAP"
    assert packet.rounds == 3
    assert packet.session_id == "sess-1"
    # guard snapshot from session_end is authoritative
    assert packet.budgets["rounds"] == 3
    assert packet.budgets["diff_lines"] == 500
    assert packet.budgets["remaining_seconds"] == 1200.0


def test_packet_collects_gates_with_decisions(tmp_path: Path) -> None:
    state = tmp_path / "state"
    _seed_run_dir(state)
    packet = packet_for_run(state, RUN_ID)
    assert len(packet.gates) == 2
    scope, diff = packet.gates
    assert scope.gate == "scope_recheck" and scope.allowed
    assert diff.gate == "diff_budget" and not diff.allowed
    assert diff.data == {"diff_lines": 500, "limit": 400}  # metadata-only detail
    assert "gate_denied:diff_budget" in packet.risks
    assert "terminal:DIFF_CAP" in packet.risks


def test_packet_falls_back_to_audit_stream(tmp_path: Path) -> None:
    """No run dir: project from state_dir/audit/*.jsonl filtered by run_id."""
    state = tmp_path / "state"
    audit_dir = state / "audit"
    audit_dir.mkdir(parents=True)
    records = []
    for i, event in enumerate(
        [
            _event("session_start", {"run_id": RUN_ID, "agent_version_id": "vid-x"}, 1),
            _event("gate_decision", {"gate": "hook", "allowed": False, "reason": "exit 2"}, 2),
            _event(
                "session_end",
                {"run_id": RUN_ID, "code": "HOOK_DENIED", "rounds": 1, "budgets": {"rounds": 1}},
                3,
            ),
            _event("session_start", {"run_id": "other-run"}, 4),
        ]
    ):
        records.append({"event": event, "prev_hash": "0" * 64, "hash": f"h{i}"})
    (audit_dir / "2026-07-01.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
    packet = packet_for_run(state, RUN_ID)
    assert packet.agent_version_id == "vid-x"
    assert packet.code == "HOOK_DENIED"
    assert packet.rounds == 1
    assert [g.gate for g in packet.gates] == ["hook"]  # other run excluded
    assert "gate_denied:hook" in packet.risks


def test_packet_unknown_run_raises(tmp_path: Path) -> None:
    with pytest.raises(PxxError):
        packet_for_run(tmp_path / "state", "no-such-run")


def test_packet_clean_run_has_no_risks(tmp_path: Path) -> None:
    state = tmp_path / "state"
    run_dir = state / "runs" / RUN_ID
    run_dir.mkdir(parents=True)
    (run_dir / "outcome.json").write_text(
        json.dumps({"run_id": RUN_ID, "code": "COMPLETED", "rounds": 1})
    )
    packet = packet_for_run(state, RUN_ID)
    assert packet.risks == ()
    assert packet.code == "COMPLETED"


def test_format_packet_renders_sections(tmp_path: Path) -> None:
    state = tmp_path / "state"
    _seed_run_dir(state)
    text = format_packet(packet_for_run(state, RUN_ID))
    assert f"run: {RUN_ID}" in text
    assert "agent_version_id: vid-123" in text
    assert "terminal: DIFF_CAP (rounds=3)" in text
    assert "budgets:" in text
    assert "[allow] scope_recheck" in text
    assert "[DENY ] diff_budget" in text
    assert "- gate_denied:diff_budget" in text
    assert "- terminal:DIFF_CAP" in text


def test_format_packet_minimal() -> None:
    packet = VerificationPacket(
        run_id="r",
        agent_version_id="",
        code="",
        budgets={},
        gates=(),
        rounds=0,
        risks=("terminal:UNKNOWN",),
    )
    text = format_packet(packet)
    assert "gates: none fired" in text
    assert "budgets: none recorded" in text
    assert "terminal: UNKNOWN" in text


# --- M0 regression: C1 (malformed rounds field degrades to neutral) -------------


def test_packet_malformed_rounds_degrades_neutral(tmp_path):
    """C1: a non-numeric rounds field must not crash the packet projection."""
    run_dir = _seed_run_dir(tmp_path)
    outcome = json.loads((run_dir / "outcome.json").read_text())
    outcome["rounds"] = "oops"
    (run_dir / "outcome.json").write_text(json.dumps(outcome))
    packet = packet_for_run(tmp_path, RUN_ID)
    assert packet.rounds == 0
