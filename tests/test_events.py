"""Event bus, scrubbing, and hash-chained audit log tests."""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from pxx.events import EVENT_KINDS, AuditLog, EventBus, scrub


def run(coro):
    return asyncio.run(coro)


def test_emit_assigns_monotonic_seq_and_records_history():
    async def scenario():
        bus = EventBus()
        e1 = await bus.emit("session_start", {}, session_id="s")
        e2 = await bus.emit("session_end", {}, session_id="s")
        assert (e1.seq, e2.seq) == (1, 2)
        assert bus.history == [e1, e2]

    run(scenario())


def test_unknown_kind_rejected():
    bus = EventBus()
    with pytest.raises(ValueError):
        run(bus.emit("nope", {}))


def test_all_documented_kinds_emit():
    async def scenario():
        bus = EventBus()
        for kind in EVENT_KINDS:
            await bus.emit(kind, {})

    run(scenario())


def test_subscriber_failure_does_not_propagate():
    async def scenario():
        bus = EventBus()

        async def bad(_event):
            raise RuntimeError("boom")

        seen = []

        async def good(event):
            seen.append(event.kind)

        bus.subscribe(bad)
        bus.subscribe(good)
        await bus.emit("session_start", {})
        assert seen == ["session_start"]

    run(scenario())


def test_scrub_strips_url_credentials():
    assert scrub("http://user:pass@host:11434/api") == "http://***@host:11434/api"
    assert scrub({"a": ["https://token@x/y"]}) == {"a": ["https://***@x/y"]}
    assert scrub(42) == 42


def test_audit_roundtrip_and_verify(tmp_path):
    async def scenario():
        bus = EventBus()
        AuditLog(tmp_path, "s1").subscribe_to(bus)
        await bus.emit("session_start", {"model": "m"}, session_id="s1")
        await bus.emit("gate_decision", {"url": "http://u:p@h/"}, session_id="s1")
        await bus.emit("session_end", {"code": "COMPLETED"}, session_id="s1")

    run(scenario())
    day = time.strftime("%Y-%m-%d")
    path = tmp_path / "audit" / f"{day}.jsonl"
    assert AuditLog.verify(path)
    lines = [json.loads(raw) for raw in path.read_text().splitlines()]
    assert lines[0]["prev_hash"] == AuditLog.GENESIS
    # credentials scrubbed before hitting disk
    assert "u:p@" not in path.read_text()


def test_audit_verify_detects_tampering(tmp_path):
    async def scenario():
        bus = EventBus()
        AuditLog(tmp_path, "s1").subscribe_to(bus)
        await bus.emit("session_start", {}, session_id="s1")
        await bus.emit("session_end", {"code": "COMPLETED"}, session_id="s1")

    run(scenario())
    day = time.strftime("%Y-%m-%d")
    path = tmp_path / "audit" / f"{day}.jsonl"
    lines = path.read_text().splitlines()
    rec = json.loads(lines[0])
    rec["event"]["data"]["model"] = "tampered"
    lines[0] = json.dumps(rec)
    path.write_text("\n".join(lines) + "\n")
    assert not AuditLog.verify(path)


def test_audit_chain_resumes_across_instances(tmp_path):
    """A second AuditLog on the same day-file must continue the chain."""

    async def scenario():
        bus1 = EventBus()
        AuditLog(tmp_path, "s1").subscribe_to(bus1)
        await bus1.emit("session_start", {"backend": "native"}, session_id="s1")
        await bus1.emit("session_end", {"code": "COMPLETED"}, session_id="s1")
        # new instance, same state dir (simulates a new process same day)
        bus2 = EventBus()
        AuditLog(tmp_path, "s2").subscribe_to(bus2)
        await bus2.emit("session_start", {"backend": "native"}, session_id="s2")
        await bus2.emit("session_end", {"code": "COMPLETED"}, session_id="s2")

    run(scenario())
    day = time.strftime("%Y-%m-%d")
    assert AuditLog.verify(tmp_path / "audit" / f"{day}.jsonl")


# --- M0 regression: I1 (truncation anchor) + I2 (corrupt-tail rotation) ---------


def test_audit_verify_detects_trailing_truncation(tmp_path):
    """I1: dropping the newest records must NOT verify clean."""

    async def scenario():
        bus = EventBus()
        AuditLog(tmp_path, "s1").subscribe_to(bus)
        for i in range(3):
            await bus.emit("observation", {"i": i}, session_id="s1")

    run(scenario())
    day = time.strftime("%Y-%m-%d")
    path = tmp_path / "audit" / f"{day}.jsonl"
    assert AuditLog.verify(path)
    lines = path.read_text().splitlines()
    path.write_text("\n".join(lines[:-1]) + "\n")  # drop the newest record
    assert not AuditLog.verify(path)


def test_audit_verify_fails_closed_without_head_anchor(tmp_path):
    """I1: a chain with no .head sidecar cannot prove it wasn't truncated."""
    import hashlib

    day = time.strftime("%Y-%m-%d")
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    path = audit_dir / f"{day}.jsonl"
    event = {"kind": "observation", "data": {}, "session_id": "s", "ts": 1.0, "seq": 1}
    payload = json.dumps(event, sort_keys=True)
    digest = hashlib.sha256((AuditLog.GENESIS + payload).encode()).hexdigest()
    path.write_text(
        json.dumps(
            {"event": event, "prev_hash": AuditLog.GENESIS, "hash": digest},
            sort_keys=True,
        )
        + "\n"
    )
    assert not AuditLog.verify(path)  # no anchor -> fail closed


def test_audit_corrupt_tail_rotates_and_fresh_chain_verifies(tmp_path, caplog):
    """I2: an unparseable tail must not silently reseed GENESIS mid-chain —
    the damaged file rotates aside (loudly) and a fresh chain starts."""

    async def scenario():
        bus = EventBus()
        AuditLog(tmp_path, "s1").subscribe_to(bus)
        await bus.emit("observation", {"ok": True}, session_id="s1")

    run(scenario())
    day = time.strftime("%Y-%m-%d")
    path = tmp_path / "audit" / f"{day}.jsonl"
    assert AuditLog.verify(path)
    with path.open("a") as fh:
        fh.write('{"event": torn-write\n')  # simulate a partial final line

    with caplog.at_level("WARNING", logger="pxx.events"):
        run(scenario())  # a new AuditLog instance resumes the chain
    assert any("unparseable tail" in r.message for r in caplog.records)
    rotated = list((tmp_path / "audit").glob("*.corrupt-*.jsonl"))
    assert len(rotated) == 1
    assert AuditLog.verify(path)  # fresh chain in place, anchored


# --- B10.3: the complete typed vocabulary is emitted at its real sites ---------------


def test_vocabulary_enforcement_unknown_kind_rejected():
    import pytest as _pytest

    bus = EventBus()
    with _pytest.raises(ValueError, match="unknown event kind"):
        run(bus.emit("definitely_not_a_kind", {}))


def test_new_vocabulary_kinds_registered():
    from pxx.events import EVENT_KINDS

    for kind in (
        "run_created",
        "prompt_rendered",
        "tool_action_proposed",
        "policy_decision",
        "checkpoint_created",
        "run_paused",
        "resumed",
        "evaluation_completed",
    ):
        assert kind in EVENT_KINDS


def test_checkpoint_and_resume_emit_paused_resumed(tmp_path):
    from pxx.resume import checkpoint_now

    async def scenario():
        bus = EventBus()
        AuditLog(tmp_path, "s1").subscribe_to(bus)
        await bus.emit("observation", {"x": 1}, session_id="s1")
        run_dir = tmp_path / "runs" / "r1"
        run_dir.mkdir(parents=True)
        (run_dir / "events.jsonl").write_text('{"kind": "observation", "data": {}}\n')
        bus2 = EventBus()
        await checkpoint_now(tmp_path, "r1", bus=bus2, session_id="s1")
        return bus2

    bus2 = run(scenario())
    kinds = [e.kind for e in bus2.history]
    assert "checkpoint_created" in kinds
    assert "run_paused" in kinds
