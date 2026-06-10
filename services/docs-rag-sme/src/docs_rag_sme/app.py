"""Transparent OpenAI-API forwarding proxy (T0 spike).

Every request is relayed verbatim to the configured local upstream and the
response is streamed back unchanged — works identically for streaming SSE and
plain JSON. `/v1/chat/completions` is broken out explicitly so the retrieval
hook (T2+) has a home; today that hook is a no-op and the body is forwarded
byte-for-byte.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from .config import Settings

# Hop-by-hop headers must not be forwarded across a proxy boundary, and
# content-length/host are recomputed by httpx/Starlette per leg.
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
    try:
        yield
    finally:
        await app.state.client.aclose()


def augment_chat_request(raw_body: bytes) -> bytes:
    """Retrieval injection point. T0: identity — return the body untouched.

    T2+ will parse the JSON, run hybrid retrieval on the last user turn, and
    inject version-filtered doc chunks as a late message. Keeping it a pure
    bytes->bytes function preserves the verbatim guarantee until that lands.
    """
    return raw_body


async def _forward(app: FastAPI, request: Request, path: str, body: bytes) -> Response:
    client: httpx.AsyncClient = app.state.client
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _DROP_REQUEST_HEADERS}

    upstream_req = client.build_request(
        request.method,
        f"/{path}",
        params=request.query_params,
        content=body,
        headers=headers,
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
    app = FastAPI(title="docs-rag-sme (T0 forwarder)", lifespan=_lifespan)
    app.state.settings = settings

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "upstream": settings.upstream}

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Response:
        body = augment_chat_request(await request.body())
        return await _forward(app, request, "v1/chat/completions", body)

    # Verbatim catch-all for everything else Aider/OpenAI clients touch
    # (/v1/models, /v1/completions, /v1/embeddings, ...).
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
