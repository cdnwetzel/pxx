"""T0 smoke tests: the forwarder relays verbatim and streams, using a fake
in-process upstream so no real vLLM is needed."""

from __future__ import annotations

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from docs_rag_sme.app import create_app
from docs_rag_sme.config import Settings


def _fake_upstream() -> Starlette:
    async def models(_request):
        return JSONResponse({"object": "list", "data": [{"id": "qwen2.5-coder-14b"}]})

    async def chat(request):
        body = await request.json()
        # Echo back what we received so the test can assert verbatim forwarding.
        return JSONResponse({"echo": body, "headers_seen": dict(request.headers)})

    async def stream(_request):
        async def gen():
            for i in range(3):
                yield f"data: chunk{i}\n\n".encode()
            yield b"data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    return Starlette(
        routes=[
            Route("/v1/models", models, methods=["GET"]),
            Route("/v1/chat/completions", chat, methods=["POST"]),
            Route("/v1/stream", stream, methods=["GET"]),
        ]
    )


@pytest.fixture
def proxy_client() -> httpx.AsyncClient:
    upstream = _fake_upstream()
    upstream_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=upstream), base_url="http://up")

    settings = Settings(
        upstream="http://up", host="127.0.0.1", port=8004, timeout=30.0, retrieval=False
    )
    proxy = create_app(settings)
    # Inject the in-process upstream client instead of opening a real socket.
    proxy.state.client = upstream_client
    proxy.state.retriever = None
    proxy.state.session_version = None
    proxy.router.lifespan_context = _noop_lifespan
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy), base_url="http://proxy")


from contextlib import asynccontextmanager  # noqa: E402


@asynccontextmanager
async def _noop_lifespan(_app):
    yield


@pytest.mark.asyncio
async def test_get_models_forwarded(proxy_client):
    async with proxy_client as c:
        r = await c.get("/v1/models")
    assert r.status_code == 200
    assert r.json()["data"][0]["id"] == "qwen2.5-coder-14b"


@pytest.mark.asyncio
async def test_chat_body_forwarded_verbatim(proxy_client):
    payload = {"model": "m", "messages": [{"role": "user", "content": "hi"}], "temperature": 0.1}
    async with proxy_client as c:
        r = await c.post("/v1/chat/completions", json=payload)
    assert r.status_code == 200
    assert r.json()["echo"] == payload


@pytest.mark.asyncio
async def test_streaming_passthrough(proxy_client):
    async with proxy_client as c:
        r = await c.get("/v1/stream")
    assert r.status_code == 200
    assert "data: chunk0" in r.text
    assert "[DONE]" in r.text


@pytest.mark.asyncio
async def test_healthz_is_local(proxy_client):
    async with proxy_client as c:
        r = await c.get("/healthz")
    assert r.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_control_context_sets_session_version(proxy_client):
    async with proxy_client as c:
        assert (await c.get("/control/context")).json()["python_version"] is None
        r = await c.post("/control/context", json={"python_version": "3.12"})
        assert r.json()["python_version"] == "3.12"
        assert (await c.get("/control/context")).json()["python_version"] == "3.12"
        # Empty clears it.
        r = await c.post("/control/context", json={"python_version": ""})
        assert r.json()["python_version"] is None
