"""``pxx serve`` — headless HTTP API (optional ``server`` extra).

FastAPI is imported inside :func:`create_app` so importing this module never
requires the extra. Endpoints:

- ``GET  /v1/health``                     — liveness + version (no auth)
- ``POST /v1/sessions``                   — start a session, returns session_id
- ``GET  /v1/sessions/{id}/events``       — SSE stream from the session bus
- ``POST /v1/sessions/{id}/cancel``       — cooperative cancellation
- ``GET  /v1/sessions``                   — list tracked sessions + status
- ``GET  /v1/memory/search`` / ``POST /v1/memory/add`` — MemoryStore proxy

Auth: when the ``PXX_SERVER_TOKEN`` env var is set, every endpoint except
``/v1/health`` requires ``Authorization: Bearer <token>``.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
from dataclasses import asdict, dataclass, is_dataclass, replace
from pathlib import Path
from typing import Any

from . import __version__
from .config import Settings
from .errors import PxxError
from .events import Event
from .outcome import RunOutcome
from .safety import PermissionMode
from .session import Session

log = logging.getLogger("pxx.server")

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


@dataclass
class TrackedSession:
    session: Any
    task: asyncio.Task | None = None
    status: str = "running"
    outcome: RunOutcome | None = None


def _make_backend(settings: Settings, name: str):
    """Instantiate a backend by name (lazy imports: optional deps)."""
    try:
        if name == "aider":
            from .backends.aider import AiderBackend

            return AiderBackend()
        from .backends.native import NativeBackend

        return NativeBackend()
    except ImportError as exc:
        raise PxxError(f"backend '{name}' unavailable: {exc}") from exc


async def _await_if_needed(value):
    if inspect.isawaitable(value):
        return await value
    return value


def _to_dict(obj) -> dict:
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    if hasattr(obj, "__dict__"):
        return dict(vars(obj))
    return {"value": str(obj)}


def _sse(event: Event) -> str:
    return f"data: {event.to_json()}\n\n"


def create_app(settings: Settings):
    """Build the FastAPI app. Imports fastapi lazily (optional extra)."""
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import JSONResponse, StreamingResponse

    # PEP 563: endpoint annotations are strings resolved against module
    # globals at decoration time; fastapi is imported locally, so expose
    # Request there for the duration of app construction.
    globals().setdefault("Request", Request)

    app = FastAPI(title="pxx", version=__version__)
    sessions: dict[str, TrackedSession] = {}
    app.state.sessions = sessions
    project = Path.cwd().name

    memory = None
    if settings.memory_enabled:
        try:
            from .memory.store import MemoryStore

            memory = MemoryStore(settings.memory_dir / "memory.db")
        except Exception:
            log.exception("memory store unavailable; /v1/memory/* will return 503")
            memory = None
    app.state.memory = memory

    @app.middleware("http")
    async def _auth(request: Request, call_next):
        token = os.environ.get("PXX_SERVER_TOKEN")
        if token and request.url.path != "/v1/health":
            if request.headers.get("authorization") != f"Bearer {token}":
                return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)

    @app.get("/v1/health")
    async def health() -> dict:
        return {"status": "ok", "version": __version__}

    @app.post("/v1/sessions", status_code=201)
    async def start_session(request: Request) -> dict:
        body = await request.json()
        task = body.get("task")
        if not task or not isinstance(task, str):
            raise HTTPException(422, "'task' (string) is required")
        effective = settings
        if permission := body.get("permission"):
            try:
                effective = replace(effective, permission=PermissionMode(permission))
            except ValueError as exc:
                raise HTTPException(400, f"invalid permission: {permission}") from exc
        try:
            backend = _make_backend(effective, body.get("backend") or "native")
        except PxxError as exc:
            raise HTTPException(503, str(exc)) from exc
        session = Session(effective, backend)
        tracked = TrackedSession(session=session)
        sessions[session.session_id] = tracked

        async def runner() -> None:
            try:
                outcome = await session.run(task)
                tracked.outcome = outcome
                tracked.status = str(outcome.code)
            except Exception:
                tracked.status = "error"
                log.exception("session %s crashed", session.session_id)

        tracked.task = asyncio.create_task(runner())
        return {"session_id": session.session_id}

    @app.get("/v1/sessions")
    async def list_sessions() -> dict:
        return {
            "sessions": [
                {
                    "session_id": sid,
                    "status": tracked.status,
                    "summary": tracked.outcome.summary[:200] if tracked.outcome else "",
                }
                for sid, tracked in sessions.items()
            ]
        }

    @app.get("/v1/sessions/{session_id}/events")
    async def session_events(session_id: str):
        tracked = sessions.get(session_id)
        if tracked is None:
            raise HTTPException(404, "unknown session")
        bus = tracked.session.bus
        queue: asyncio.Queue[Event] = asyncio.Queue()

        async def _enqueue(event: Event) -> None:
            queue.put_nowait(event)

        bus.subscribe(_enqueue)
        history = list(bus.history)
        last_seq = history[-1].seq if history else 0

        async def stream():
            for event in history:
                yield _sse(event)
                if event.kind == "session_end":
                    return
            while True:
                event = await queue.get()
                if event.seq <= last_seq:
                    continue  # already replayed from history
                yield _sse(event)
                if event.kind == "session_end":
                    return

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.post("/v1/sessions/{session_id}/cancel")
    async def cancel_session(session_id: str) -> dict:
        tracked = sessions.get(session_id)
        if tracked is None:
            raise HTTPException(404, "unknown session")
        try:
            await tracked.session.backend.cancel()
        except Exception:
            log.exception("backend cancel failed for %s", session_id)
        if tracked.task is not None and not tracked.task.done():
            tracked.task.cancel()
        return {"session_id": session_id, "status": "cancelling"}

    @app.get("/v1/memory/search")
    async def memory_search(q: str, k: int = 8) -> dict:
        if memory is None:
            raise HTTPException(503, "memory store unavailable")
        rows = await _await_if_needed(memory.search(project, q, k=k))
        return {"results": [_to_dict(row) for row in rows]}

    @app.post("/v1/memory/add", status_code=201)
    async def memory_add(request: Request) -> dict:
        if memory is None:
            raise HTTPException(503, "memory store unavailable")
        body = await request.json()
        content = body.get("content")
        if not content or not isinstance(content, str):
            raise HTTPException(422, "'content' (string) is required")
        tags = body.get("tags") or []
        obs_id = await _await_if_needed(
            memory.add(project, "note", content, tags=list(tags), source="server")
        )
        return {"id": obs_id}

    return app


def run_server(settings: Settings, host: str = "127.0.0.1", port: int = 8400) -> None:
    """Run the API via uvicorn (blocking)."""
    import uvicorn

    if host not in LOOPBACK_HOSTS and not os.environ.get("PXX_SERVER_TOKEN"):
        log.warning(
            "SECURITY: binding pxx server to %s without PXX_SERVER_TOKEN — "
            "the API is UNAUTHENTICATED on the network.",
            host,
        )
    uvicorn.run(create_app(settings), host=host, port=port)
