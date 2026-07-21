"""OpenAI-API proxy with optional docs-RAG augmentation.

Non-chat traffic and unaugmentable chat requests are forwarded verbatim and
streamed back unchanged. For chat completions, if a retriever is available and
the request looks doc-relevant, retrieved context is injected *late* in the
message list (preserving the cached system+repo-map prefix — plan Decision B)
before forwarding. Augmentation never breaks a request: any failure, a downed
store, or `DOCS_SME_RETRIEVAL=off` all degrade to plain verbatim forwarding.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from .config import Settings
from .retrieve import Retriever, augment_messages

log = logging.getLogger("docs_rag_sme")

_DROP_REQUEST_HEADERS = {"host", "content-length"}
_DROP_RESPONSE_HEADERS = {
    "content-length",
    "content-encoding",
    "transfer-encoding",
    "connection",
    "keep-alive",
}


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    app.state.client = httpx.AsyncClient(
        base_url=settings.upstream,
        timeout=httpx.Timeout(settings.timeout, connect=5.0),
    )
    app.state.retriever = None
    # Current session's Python version for version-aware retrieval (T3). Set by
    # `pxx --with-docs` via POST /control/context; single active project assumed.
    app.state.session_version = os.environ.get("DOCS_SME_PY_VERSION") or None
    if settings.retrieval:
        try:
            # I/O at startup (DB connect + embedder) — a real failure mode, not
            # control flow. On failure the proxy still forwards verbatim.
            app.state.retriever = await run_in_threadpool(Retriever)
            log.info("docs-sme: retrieval augmentation ON")
        except Exception as exc:  # noqa: BLE001 - degrade, never fail to start
            log.warning("docs-sme: retrieval disabled (store/embedder unavailable): %s", exc)
    try:
        yield
    finally:
        await app.state.client.aclose()
        if app.state.retriever is not None:
            await run_in_threadpool(app.state.retriever.close)


def augment_chat_body(
    raw_body: bytes, retriever: Retriever, python_version: str | None = None
) -> tuple[bytes, int]:
    """Parse, augment the message list, re-serialise. Returns (body, n_injected).
    Any failure returns the original body untouched — augmentation is best-effort
    and must never break a chat request."""
    try:
        payload = json.loads(raw_body)
        messages = payload.get("messages")
        if not isinstance(messages, list):
            return raw_body, 0
        new_messages, n = augment_messages(messages, retriever, python_version=python_version)
        if n == 0:
            return raw_body, 0
        payload["messages"] = new_messages
        return json.dumps(payload).encode(), n
    except Exception as exc:  # noqa: BLE001 - never break the request on augment failure
        log.warning("docs-sme: augmentation skipped (%s)", exc)
        return raw_body, 0


async def _forward(app: FastAPI, request: Request, path: str, body: bytes) -> Response:
    client: httpx.AsyncClient = app.state.client
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _DROP_REQUEST_HEADERS}

    upstream_req = client.build_request(
        request.method, f"/{path}", params=request.query_params, content=body, headers=headers
    )
    try:
        upstream_resp = await client.send(upstream_req, stream=True)
    except httpx.ConnectError as exc:
        return JSONResponse(
            status_code=502,
            content={
                "error": {
                    "message": f"docs-sme: upstream unreachable at {app.state.settings.upstream}: {exc}",
                    "type": "upstream_connect_error",
                }
            },
        )

    resp_headers = {
        k: v for k, v in upstream_resp.headers.items() if k.lower() not in _DROP_RESPONSE_HEADERS
    }
    return StreamingResponse(
        upstream_resp.aiter_raw(),
        status_code=upstream_resp.status_code,
        headers=resp_headers,
        background=BackgroundTask(upstream_resp.aclose),
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(title="docs-rag-sme", lifespan=_lifespan)
    app.state.settings = settings

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        return {
            "status": "ok",
            "upstream": settings.upstream,
            "retrieval": app.state.retriever is not None,
            "session_version": app.state.session_version,
        }

    @app.get("/control/context")
    async def get_context() -> dict[str, object]:
        return {"python_version": app.state.session_version}

    @app.post("/control/context")
    async def set_context(request: Request) -> dict[str, object]:
        body = await request.json()
        # Empty/null clears the filter (retrieve across versions).
        app.state.session_version = (body or {}).get("python_version") or None
        log.info("docs-sme: session python_version=%s", app.state.session_version)
        return {"python_version": app.state.session_version}

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Response:
        raw = await request.body()
        injected = 0
        retriever: Retriever | None = app.state.retriever
        if retriever is not None:
            raw, injected = await run_in_threadpool(
                augment_chat_body, raw, retriever, app.state.session_version
            )
        resp = await _forward(app, request, "v1/chat/completions", raw)
        resp.headers["X-Docs-SME-Injected"] = str(injected)
        return resp

    @app.api_route(
        "/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def catch_all(request: Request, path: str) -> Response:
        return await _forward(app, request, path, await request.body())

    return app


def run() -> None:
    import uvicorn

    settings = Settings.from_env()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="info")


app = create_app()
