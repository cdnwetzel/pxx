"""End-to-end tests for the pxx PreToolUse HITL gate <-> broker bridge (roadmap P4).

These do NOT test pure helpers. They run `docs/examples/hitl/hitl_gate.py` as a real
subprocess, fed on stdin exactly the way pxx invokes a PreToolUse hook, and assert its
EXIT CODE — which is the whole safety contract (0 = allow the tool call, 2 = deny it).

A stub broker stands in for the Slack Socket Mode broker. It speaks the same wire
contract (`POST {nonce, summary, origin, ...}` -> post a card -> a human's tap writes
`{nonce}.decision`) but skips Slack itself, which is already proven live by R-044/R-045.
What is under test here is the BRIDGE: that the gate's own nonce is what threads through,
that the post is non-blocking, and above all that every failure path denies.

The negative controls are the point. A gate that cannot fail is worth nothing, so most of
these assert a DENY: wrong nonce, no broker, no decision, aborted, malformed.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

GATE = Path(__file__).resolve().parent.parent / "docs" / "examples" / "hitl" / "hitl_gate.py"
BROKER = (
    Path(__file__).resolve().parent.parent / "docs" / "examples" / "hitl" / "slack_hitl_broker.py"
)

ALLOW, DENY = 0, 2


def _load_broker():
    """Import the broker module for its real `write_decision` / `sanitize_nonce`, so the
    stub uses the SHIPPED decision writer rather than a reimplementation of it."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("slack_hitl_broker", BROKER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class StubBroker:
    """A non-blocking stand-in for the Slack broker's `/post-approval`.

    `mode` picks what the simulated human does:
      "approve" / "abort" — write that decision for the caller's nonce
      "wrong_nonce"       — write an approve for a DIFFERENT nonce (must not release)
      "silent"            — post the card and never decide (deadline must deny)
      "malformed"         — write unparseable JSON to the decision path
    """

    def __init__(self, hitl_dir: Path, mode: str):
        self.hitl_dir, self.mode = hitl_dir, mode
        self.requests: list[dict] = []
        self.post_durations: list[float] = []
        broker = _load_broker()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # keep pytest output clean
                pass

            def do_POST(self):
                started = time.time()
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                try:
                    payload = json.loads(body)
                except Exception:
                    payload = {}
                outer.requests.append(payload)
                # Use the SHIPPED resolution logic, not a copy of it -- otherwise these
                # tests pass against a broker that ignores the caller's nonce, which is
                # precisely the pre-bridge bug they exist to catch.
                nonce, err = broker.resolve_nonce(payload, lambda: "0" * 16)
                if err:
                    self.send_response(400)
                    self.end_headers()
                    outer.post_durations.append(time.time() - started)
                    return
                if nonce:
                    if outer.mode in ("approve", "abort"):
                        broker.write_decision(outer.hitl_dir, nonce, outer.mode, "tester")
                    elif outer.mode == "wrong_nonce":
                        broker.write_decision(outer.hitl_dir, "f" * 16, "approve", "tester")
                    elif outer.mode == "malformed":
                        (outer.hitl_dir / f"{nonce}.decision").write_text("{not json")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"nonce": nonce, "posted": True}).encode())
                outer.post_durations.append(time.time() - started)

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/post-approval"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


def run_gate(hitl_dir: Path, notify: str, *, deadline: float = 4.0, tool: str = "edit_file"):
    """Invoke the gate the way pxx does: JSON on stdin, meaning in the exit code."""
    env = {
        "PATH": "/usr/bin:/bin",
        "HITL_SECRET": "test-secret",
        "HITL_DIR": str(hitl_dir),
        "HITL_NOTIFY": notify,
        "HITL_DEADLINE": str(deadline),
        "HITL_ORIGIN": "pytest-bridge",
    }
    return subprocess.run(
        [sys.executable, str(GATE)],
        input=json.dumps({"tool": tool, "args": {"path": "mathlib.py"}}),
        capture_output=True,
        text=True,
        env=env,
        timeout=deadline + 30,
    )


def decisions(hitl_dir: Path) -> list[dict]:
    log = hitl_dir / "decisions.jsonl"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]


# ---- the allow path -----------------------------------------------------------------


def test_approve_releases_the_gate_and_writes_a_receipt(tmp_path):
    broker = StubBroker(tmp_path, "approve")
    try:
        result = run_gate(tmp_path, broker.url)
    finally:
        broker.stop()
    assert result.returncode == ALLOW

    # the gate's OWN nonce is what reached the broker -- this is the bridge
    assert len(broker.requests) == 1
    sent = broker.requests[0]
    assert sent["nonce"] and len(sent["nonce"]) == 16
    assert sent["origin"] == "pytest-bridge"  # source label rides along
    assert sent["tool"] == "edit_file"

    # a receipt persisted BEFORE the allow, naming that same nonce
    recs = decisions(tmp_path)
    assert len(recs) == 1
    assert recs[0]["decision"] == "approve" and recs[0]["nonce"] == sent["nonce"]


def test_the_post_is_non_blocking(tmp_path):
    """P4's core requirement. `/request-approval` blocks for its own deadline; the gate
    POSTs with an 8s timeout and ignores the result, so a blocking endpoint would be
    abandoned mid-request every time. The bridge endpoint must return at once.

    Scope note: this measures the STUB, which is non-blocking by construction, so it
    cannot catch `/post-approval` itself regressing to a blocking implementation. The
    shipped endpoint is guarded separately by
    `test_slack_hitl_broker.py::test_only_the_blocking_endpoint_waits`. (Review on PR #80
    caught that this test alone was not enough.)
    """
    broker = StubBroker(tmp_path, "approve")
    try:
        run_gate(tmp_path, broker.url, deadline=4.0)
    finally:
        broker.stop()
    assert broker.post_durations and max(broker.post_durations) < 1.0


# ---- negative controls: every one of these MUST deny ---------------------------------


def test_abort_denies(tmp_path):
    broker = StubBroker(tmp_path, "abort")
    try:
        result = run_gate(tmp_path, broker.url)
    finally:
        broker.stop()
    assert result.returncode == DENY
    assert decisions(tmp_path)[0]["result"] == "deny"


def test_decision_for_a_different_nonce_does_not_release_the_gate(tmp_path):
    """The nonce is the binding. An approval minted for some OTHER request -- which is
    exactly what a broker that mints its own nonce produces -- must never release this
    call. Before the bridge, this was the default behaviour of every gated call."""
    broker = StubBroker(tmp_path, "wrong_nonce")
    try:
        result = run_gate(tmp_path, broker.url, deadline=3.0)
    finally:
        broker.stop()
    assert result.returncode == DENY
    assert decisions(tmp_path)[0]["decision"] == "timeout"  # it waited, then denied
    # the stray approval is still sitting there, unused
    assert (tmp_path / ("f" * 16 + ".decision")).exists()


def test_no_decision_within_the_deadline_denies(tmp_path):
    broker = StubBroker(tmp_path, "silent")
    try:
        result = run_gate(tmp_path, broker.url, deadline=3.0)
    finally:
        broker.stop()
    assert result.returncode == DENY
    assert decisions(tmp_path)[0]["decision"] == "timeout"


def test_unreachable_broker_denies_rather_than_failing_open(tmp_path):
    """Routing is best-effort by design (a transport outage must not fail OPEN). Prove
    the 'best-effort' half cannot become 'skip the gate': with nothing listening, the
    gate still blocks and still denies."""
    result = run_gate(tmp_path, "http://127.0.0.1:9/post-approval", deadline=3.0)
    assert result.returncode == DENY
    assert decisions(tmp_path)[0]["decision"] == "timeout"


def test_no_transport_configured_still_denies(tmp_path):
    """HITL_NOTIFY empty = no card is ever posted. The gate must not treat 'nobody was
    asked' as 'nobody objected'."""
    result = run_gate(tmp_path, "", deadline=3.0)
    assert result.returncode == DENY


def test_malformed_decision_denies(tmp_path):
    broker = StubBroker(tmp_path, "malformed")
    try:
        result = run_gate(tmp_path, broker.url)
    finally:
        broker.stop()
    assert result.returncode == DENY
    assert decisions(tmp_path)[0]["decision"] == "unreadable"


def test_unreadable_stdin_denies(tmp_path):
    """pxx hands the hook JSON; anything else means the gate cannot know what it is
    being asked to approve, so it must refuse."""
    result = subprocess.run(
        [sys.executable, str(GATE)],
        input="not json at all",
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin",
            "HITL_SECRET": "s",
            "HITL_DIR": str(tmp_path),
            "HITL_DEADLINE": "2",
        },
        timeout=30,
    )
    assert result.returncode == DENY


@pytest.mark.parametrize("bad", ["../../../tmp/pwn", "a/b", "with space", ""])
def test_broker_refuses_a_traversal_nonce_so_no_decision_is_written(tmp_path, bad):
    """The gate always sends a clean nonce, so this guards the endpoint against a
    DIFFERENT caller: a malicious POST must not make the broker write outside HITL_DIR."""
    broker_mod = _load_broker()
    assert broker_mod.sanitize_nonce(bad) is None
    assert broker_mod.write_decision(tmp_path, bad, "approve", "attacker") is False
    assert not list(tmp_path.glob("**/*.decision"))


# ---- a REAL pxx session, gated by the real hook path --------------------------------
#
# Everything above drives the gate directly. These drive a real `pxx` session: the real
# ToolRegistry, the real HookRunner dispatching PreToolUse, the real write_file tool. The
# backend is scripted (MockBackend) so the run is deterministic and needs no model -- the
# LLM is not what is under test, the gate is.


def _run_gated_session(tmp_path, broker_url, deadline=4.0):
    """Run a real `pxx` Session whose write_file call is gated by hitl_gate.py.

    This goes through `pxx.session.Session`, not the backend alone, so the whole real
    path is exercised: Session builds the SessionContext, the real HookRunner dispatches
    PreToolUse to the real gate subprocess, the real ToolRegistry performs the write, and
    a HookDenied is mapped to a terminal HOOK_DENIED outcome rather than escaping. Only
    the model is scripted.
    """
    import asyncio as _asyncio
    from shlex import quote

    from pxx.backends.mock import MockBackend
    from pxx.config import Settings
    from pxx.safety import Hook, PermissionMode
    from pxx.session import Session

    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    spool = tmp_path  # gate + stub broker share this, as the README requires
    # HookRunner shlex-splits the command, so every interpolated path must be quoted:
    # a space in the repo path or in pytest's tmp dir would otherwise split into the
    # wrong argv and fail the test for a reason unrelated to the gate.
    env_prefix = (
        f"HITL_SECRET=test-secret HITL_DIR={quote(str(spool))} "
        f"HITL_NOTIFY={quote(str(broker_url))} "
        f"HITL_DEADLINE={deadline} HITL_ORIGIN=pxx-run-test"
    )
    # `env` so the hook subprocess carries its config the way a real deployment would
    command = f"/usr/bin/env {env_prefix} {quote(sys.executable)} {quote(str(GATE))}"
    settings = Settings(
        permission=PermissionMode.EDIT,
        hooks=(Hook(event="PreToolUse", command=command, matcher="write_file", timeout=120),),
        memory_enabled=False,
        safety_net=False,
        memory_dir=tmp_path / "mem",
        state_dir=tmp_path / "state",
    )
    backend = MockBackend(
        [
            {"tool": "write_file", "args": {"path": "gated.py", "content": "print('shipped')\n"}},
            {"done": "wrote it"},
        ]
    )
    session = Session(settings, backend, cwd=work, safety_net=False)
    outcome = _asyncio.run(session.run("write gated.py"))
    return outcome, work / "gated.py"


def test_real_session_proceeds_when_the_gate_is_approved(tmp_path):
    """The allow path, end to end: a real pxx tool call pauses on the PreToolUse gate,
    the (stubbed) human approves, and the tool actually runs."""
    broker = StubBroker(tmp_path, "approve")
    try:
        outcome, target = _run_gated_session(tmp_path, broker.url)
    finally:
        broker.stop()

    assert target.exists(), "approved write_file must actually have written the file"
    assert target.read_text() == "print('shipped')\n"
    assert outcome.code.name == "COMPLETED"
    # the card carried the gate's nonce and the run's origin label
    assert broker.requests and broker.requests[0]["origin"] == "pxx-run-test"
    assert decisions(tmp_path)[0]["decision"] == "approve"


def test_real_session_is_blocked_when_the_gate_is_aborted(tmp_path):
    """The negative control that matters most: a denied gate must stop the real tool
    call, not merely record a disapproving note next to a file that got written anyway."""
    broker = StubBroker(tmp_path, "abort")
    try:
        outcome, target = _run_gated_session(tmp_path, broker.url)
    finally:
        broker.stop()

    assert not target.exists(), "aborted write_file must NOT have written the file"
    assert outcome.code.name == "HOOK_DENIED"


def test_real_session_is_blocked_when_the_broker_never_answers(tmp_path):
    """No human, no transport answer -- the run must stop rather than proceed."""
    broker = StubBroker(tmp_path, "silent")
    try:
        outcome, target = _run_gated_session(tmp_path, broker.url, deadline=3.0)
    finally:
        broker.stop()

    assert not target.exists()
    assert outcome.code.name == "HOOK_DENIED"
