"""Router tests — all HTTP via httpx.MockTransport, no real sockets."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from pxx import router
from pxx.config import ModelRef, Settings
from pxx.errors import BackendUnavailable
from pxx.router import Endpoint, context_window, probe_endpoints, resolve_model


def install_transport(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    """Route all probe traffic through a MockTransport handler."""

    def factory(timeout: float = 1.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=timeout)

    monkeypatch.setattr(router, "_client_factory", factory)


def test_probe_ollama_lists_models(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(
            200, json={"models": [{"name": "qwen2.5-coder:7b"}, {"name": "llama3.1:8b"}]}
        )

    install_transport(monkeypatch, handler)
    spec = ModelRef(provider="ollama", model="qwen2.5-coder:7b")
    endpoints = asyncio.run(probe_endpoints([spec]))
    assert endpoints == [
        Endpoint(
            provider="ollama",
            base_url="http://localhost:11434",
            models=("qwen2.5-coder:7b", "llama3.1:8b"),
            reachable=True,
        )
    ]


def test_probe_openai_sends_auth_header(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_auth: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        seen_auth.append(request.headers.get("authorization", ""))
        return httpx.Response(200, json={"data": [{"id": "gpt-4o-mini"}]})

    install_transport(monkeypatch, handler)
    spec = ModelRef(
        provider="openai",
        model="gpt-4o-mini",
        base_url="https://api.openai.com",
        api_key="sk-test",
    )
    (endpoint,) = asyncio.run(probe_endpoints([spec]))
    assert endpoint.reachable
    assert endpoint.models == ("gpt-4o-mini",)
    assert seen_auth == ["Bearer sk-test"]


def test_probe_unreachable_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    install_transport(monkeypatch, handler)
    (endpoint,) = asyncio.run(probe_endpoints([ModelRef(provider="ollama")]))
    assert not endpoint.reachable
    assert endpoint.models == ()


def test_probe_http_error_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    install_transport(monkeypatch, handler)
    (endpoint,) = asyncio.run(probe_endpoints([ModelRef(provider="vllm")]))
    assert not endpoint.reachable


def test_context_window_prefix_match() -> None:
    assert context_window("qwen2.5-coder:7b") == 32768
    assert context_window("devstral:latest") == 131072
    assert context_window("llama3.1:70b") == 131072
    assert context_window("gemma3:27b") == 131072
    assert context_window("deepseek-coder-v2:16b") == 16384
    assert context_window("mistral:7b") == 32768
    assert context_window("phi4:14b") == 16384


def test_context_window_longest_prefix_and_default() -> None:
    assert context_window("qwen2.5-coder-extra:1b") == 32768
    assert context_window("totally-unknown-model:9b") == 8192
    assert context_window("") == 8192


def test_model_registry_probed_wins_over_default() -> None:
    registry = router.ModelRegistry()
    assert registry.context_window("custom:1b") == 8192
    registry.register(["qwen2.5-coder:14b", "custom:1b"])
    assert registry.context_window("qwen2.5-coder:14b") == 32768
    assert registry.context_window("custom:1b") == 8192


def _settings(**kwargs) -> Settings:
    return Settings(**kwargs)


def test_resolve_model_primary_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "qwen2.5-coder:7b"}]})

    install_transport(monkeypatch, handler)
    ref = ModelRef(provider="ollama", model="qwen2.5-coder:7b")
    resolved = asyncio.run(resolve_model(_settings(model=ref)))
    assert resolved == ref


def test_resolve_model_corrects_to_single_model(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "devstral:latest"}]})

    install_transport(monkeypatch, handler)
    ref = ModelRef(provider="ollama", model="qwen2.5-coder:7b")
    resolved = asyncio.run(resolve_model(_settings(model=ref)))
    assert resolved.model == "devstral:latest"
    assert resolved.provider == "ollama"


def test_resolve_model_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.port == 11434:
            raise httpx.ConnectError("down", request=request)
        return httpx.Response(200, json={"data": [{"id": "served-model"}]})

    install_transport(monkeypatch, handler)
    primary = ModelRef(provider="ollama", model="qwen2.5-coder:7b")
    fallback = ModelRef(provider="vllm", model="served-model", base_url="http://127.0.0.1:8000")
    resolved = asyncio.run(resolve_model(_settings(model=primary, fallback_models=(fallback,))))
    assert resolved == fallback


def test_resolve_model_skips_reachable_but_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": []})
        return httpx.Response(200, json={"data": [{"id": "served-model"}]})

    install_transport(monkeypatch, handler)
    primary = ModelRef(provider="ollama", model="qwen2.5-coder:7b")
    fallback = ModelRef(provider="vllm", model="served-model", base_url="http://127.0.0.1:8000")
    resolved = asyncio.run(resolve_model(_settings(model=primary, fallback_models=(fallback,))))
    assert resolved == fallback


def test_resolve_model_none_reachable_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    install_transport(monkeypatch, handler)
    settings = _settings(
        model=ModelRef(provider="ollama", model="a"),
        fallback_models=(ModelRef(provider="vllm", model="b"),),
    )
    with pytest.raises(BackendUnavailable) as excinfo:
        asyncio.run(resolve_model(settings))
    message = str(excinfo.value)
    assert "ollama" in message and "vllm" in message  # actionable: what was tried
