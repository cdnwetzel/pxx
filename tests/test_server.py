"""Tests for pxx.server — fastapi TestClient, no network, no real backends."""

from __future__ import annotations

import asyncio
import time
import uuid

import pytest
from fastapi.testclient import TestClient

import pxx.server as server
from pxx import __version__
from pxx.config import Settings
from pxx.events import EventBus
from pxx.outcome import RunOutcome, TerminalCode
from pxx.safety import PermissionMode


class FakeBackend:
    def __init__(self):
        self.cancelled = False

    @property
    def name(self):
        return "fake"

    async def cancel(self):
        self.cancelled = True


class QuickFakeSession:
    """Emits a full event sequence and completes immediately."""

    def __init__(self, settings, backend, *, cwd=None, bus=None):
        self.settings = settings
        self.backend = backend
        self.bus = bus or EventBus()
        self.session_id = uuid.uuid4().hex[:12]

    async def run(self, task: str) -> RunOutcome:
        await self.bus.emit("session_start", {"backend": "fake"}, session_id=self.session_id)
        await self.bus.emit("model_response", {"text": "pong"}, session_id=self.session_id)
        outcome = RunOutcome(
            code=TerminalCode.COMPLETED, summary=f"did: {task}", session_id=self.session_id
        )
        await self.bus.emit("session_end", {"code": "COMPLETED"}, session_id=self.session_id)
        return outcome


class SlowFakeSession(QuickFakeSession):
    """Blocks until cancelled (for the cancel endpoint test)."""

    async def run(self, task: str) -> RunOutcome:
        await self.bus.emit("session_start", {"backend": "fake"}, session_id=self.session_id)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await self.bus.emit("session_end", {"code": "INTERRUPTED"}, session_id=self.session_id)
            return RunOutcome(
                code=TerminalCode.INTERRUPTED, summary="cancelled", session_id=self.session_id
            )
        raise AssertionError("unreachable")


@pytest.fixture
def settings(tmp_path):
    return Settings(memory_enabled=False, memory_dir=tmp_path / "mem", state_dir=tmp_path / "st")


def _client(monkeypatch, settings, session_cls=QuickFakeSession):
    monkeypatch.setattr(server, "Session", session_cls)
    monkeypatch.setattr(server, "_make_backend", lambda s, name: FakeBackend())
    monkeypatch.delenv("PXX_SERVER_TOKEN", raising=False)
    return TestClient(server.create_app(settings))


def test_health(monkeypatch, settings):
    client = _client(monkeypatch, settings)
    resp = client.get("/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "version": __version__}


def test_create_and_list_session(monkeypatch, settings):
    client = _client(monkeypatch, settings)
    resp = client.post("/v1/sessions", json={"task": "ping"})
    assert resp.status_code == 201
    session_id = resp.json()["session_id"]

    deadline = time.monotonic() + 5
    status = ""
    while time.monotonic() < deadline:
        listed = client.get("/v1/sessions").json()["sessions"]
        entry = next(s for s in listed if s["session_id"] == session_id)
        status = entry["status"]
        if status != "running":
            break
        time.sleep(0.05)
    assert status == "COMPLETED"


def test_session_events_sse_replay(monkeypatch, settings):
    client = _client(monkeypatch, settings)
    session_id = client.post("/v1/sessions", json={"task": "ping"}).json()["session_id"]
    # let the session finish, then replay the stream from history
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        listed = client.get("/v1/sessions").json()["sessions"]
        if next(s for s in listed if s["session_id"] == session_id)["status"] != "running":
            break
        time.sleep(0.05)
    resp = client.get(f"/v1/sessions/{session_id}/events")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    body = resp.text
    assert "session_start" in body
    assert "model_response" in body
    assert "pong" in body
    assert "session_end" in body


def test_session_events_live_stream(monkeypatch, settings):
    client = _client(monkeypatch, settings, session_cls=SlowFakeSession)
    session_id = client.post("/v1/sessions", json={"task": "slow"}).json()["session_id"]
    # session is still running; subscribing now should stream live events
    # and terminate when we cancel it from another request is not possible
    # mid-stream with TestClient, so just verify the started event arrives.
    with client.stream("GET", f"/v1/sessions/{session_id}/events") as resp:
        first = next(resp.iter_lines())
        assert "session_start" in first


def test_cancel_session(monkeypatch, settings):
    client = _client(monkeypatch, settings, session_cls=SlowFakeSession)
    session_id = client.post("/v1/sessions", json={"task": "slow"}).json()["session_id"]
    resp = client.post(f"/v1/sessions/{session_id}/cancel")
    assert resp.status_code == 200

    deadline = time.monotonic() + 5
    status = ""
    while time.monotonic() < deadline:
        listed = client.get("/v1/sessions").json()["sessions"]
        status = next(s for s in listed if s["session_id"] == session_id)["status"]
        if status != "running":
            break
        time.sleep(0.05)
    assert status == "INTERRUPTED"


def test_unknown_session_404(monkeypatch, settings):
    client = _client(monkeypatch, settings)
    assert client.get("/v1/sessions/nope/events").status_code == 404
    assert client.post("/v1/sessions/nope/cancel").status_code == 404


def test_invalid_permission_400(monkeypatch, settings):
    client = _client(monkeypatch, settings)
    resp = client.post("/v1/sessions", json={"task": "x", "permission": "yolo"})
    assert resp.status_code == 400


def test_permission_passed_to_session(monkeypatch, settings):
    seen = {}

    class CapturingSession(QuickFakeSession):
        def __init__(self, settings, backend, *, cwd=None, bus=None):
            seen["permission"] = settings.permission
            super().__init__(settings, backend, cwd=cwd, bus=bus)

    client = _client(monkeypatch, settings, session_cls=CapturingSession)
    resp = client.post("/v1/sessions", json={"task": "x", "permission": "auto"})
    assert resp.status_code == 201
    assert seen["permission"] is PermissionMode.AUTO


def test_backend_unavailable_503(monkeypatch, settings):
    from pxx.errors import PxxError

    def boom(s, name):
        raise PxxError("backend 'native' unavailable")

    monkeypatch.setattr(server, "Session", QuickFakeSession)
    monkeypatch.setattr(server, "_make_backend", boom)
    monkeypatch.delenv("PXX_SERVER_TOKEN", raising=False)
    client = TestClient(server.create_app(settings))
    resp = client.post("/v1/sessions", json={"task": "x"})
    assert resp.status_code == 503


def test_auth_required(monkeypatch, settings):
    monkeypatch.setattr(server, "Session", QuickFakeSession)
    monkeypatch.setattr(server, "_make_backend", lambda s, name: FakeBackend())
    monkeypatch.setenv("PXX_SERVER_TOKEN", "sekret")
    client = TestClient(server.create_app(settings))

    assert client.get("/v1/health").status_code == 200  # exempt
    assert client.get("/v1/sessions").status_code == 401
    assert client.post("/v1/sessions", json={"task": "x"}).status_code == 401
    bad = client.get("/v1/sessions", headers={"Authorization": "Bearer wrong"})
    assert bad.status_code == 401
    good = client.get("/v1/sessions", headers={"Authorization": "Bearer sekret"})
    assert good.status_code == 200


def test_memory_endpoints_503_when_disabled(monkeypatch, settings):
    client = _client(monkeypatch, settings)
    assert client.get("/v1/memory/search", params={"q": "x"}).status_code == 503
    assert client.post("/v1/memory/add", json={"content": "x", "tags": []}).status_code == 503


def test_memory_proxy(monkeypatch, tmp_path):
    pytest.importorskip("pxx.memory.store")
    settings = Settings(memory_enabled=True, memory_dir=tmp_path / "mem", state_dir=tmp_path / "st")
    client = _client(monkeypatch, settings)
    resp = client.post("/v1/memory/add", json={"content": "prefers ruff", "tags": ["style"]})
    assert resp.status_code == 201
    results = client.get("/v1/memory/search", params={"q": "ruff"}).json()["results"]
    assert any("ruff" in str(r.get("content", "")) for r in results)


def test_run_server_warns_on_non_loopback(monkeypatch, settings, caplog):
    import types

    calls = {}

    def fake_run(app, host, port):
        calls["host"] = host

    monkeypatch.setitem(__import__("sys").modules, "uvicorn", types.SimpleNamespace(run=fake_run))
    monkeypatch.delenv("PXX_SERVER_TOKEN", raising=False)
    with caplog.at_level("WARNING", logger="pxx.server"):
        server.run_server(settings, host="0.0.0.0", port=9999)
    assert calls["host"] == "0.0.0.0"
    assert any("UNAUTHENTICATED" in r.message for r in caplog.records)


def test_run_server_no_warning_on_loopback(monkeypatch, settings, caplog):
    import types

    monkeypatch.setitem(
        __import__("sys").modules,
        "uvicorn",
        types.SimpleNamespace(run=lambda app, host, port: None),
    )
    monkeypatch.delenv("PXX_SERVER_TOKEN", raising=False)
    with caplog.at_level("WARNING", logger="pxx.server"):
        server.run_server(settings)
    assert not caplog.records
