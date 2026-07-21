"""Endpoint probing and model resolution.

pxx is local-first: at session start we probe the configured endpoints
(Ollama native or OpenAI-compatible) to find a reachable model. Probing
never raises — an unreachable endpoint is data, not an exception. Only
:func:`resolve_model` raises (:class:`BackendUnavailable`) when the whole
fallback chain is exhausted.

All HTTP goes through the module-level :func:`_client_factory` so tests can
inject ``httpx.MockTransport`` without touching real sockets.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace

import httpx

from .config import ModelRef, Settings
from .errors import BackendUnavailable

log = logging.getLogger("pxx.router")

#: Context windows (tokens) for common local models, keyed by name prefix.
#: Longest prefix wins; unknown models fall back to ``DEFAULT_CONTEXT_WINDOW``.
MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "qwen2.5-coder": 32768,
    "qwen3": 32768,
    "devstral": 131072,
    "llama3.1": 131072,
    "llama3.2": 131072,
    "llama3.3": 131072,
    "gemma3": 131072,
    "gemma2": 8192,
    "deepseek-coder": 16384,
    "deepseek-v3": 65536,
    "mistral": 32768,
    "mixtral": 32768,
    "codellama": 16384,
    "starcoder2": 16384,
    "phi4": 16384,
    "phi3": 131072,
    "command-r": 131072,
}

DEFAULT_CONTEXT_WINDOW = 8192

_OPENAI_COMPAT_PROVIDERS = ("openai", "vllm", "openai-compatible")


def _client_factory(timeout: float) -> httpx.AsyncClient:
    """Build the probe client. Monkeypatched by tests to use MockTransport."""
    return httpx.AsyncClient(timeout=timeout)


@dataclass(frozen=True)
class Endpoint:
    """The result of probing one endpoint."""

    provider: str
    base_url: str
    models: tuple[str, ...] = ()
    reachable: bool = False


def context_window(model: str) -> int:
    """Known context window for ``model`` (longest prefix match), else 8192."""
    name = model.lower()
    best_prefix = ""
    best_window = DEFAULT_CONTEXT_WINDOW
    for prefix, window in MODEL_CONTEXT_WINDOWS.items():
        if name.startswith(prefix) and len(prefix) > len(best_prefix):
            best_prefix = prefix
            best_window = window
    return best_window


class ModelRegistry:
    """Context-window lookup: probed metadata > table > 8192 default."""

    def __init__(self) -> None:
        self._probed: dict[str, int] = {}

    def register(self, models: Endpoint | list[str] | tuple[str, ...]) -> None:
        """Record probed models; window comes from the table (or default)."""
        names = models.models if isinstance(models, Endpoint) else models
        for name in names:
            self._probed.setdefault(name, context_window(name))

    def context_window(self, model: str) -> int:
        if model in self._probed:
            return self._probed[model]
        return context_window(model)


async def _probe_one(client: httpx.AsyncClient, spec: ModelRef) -> Endpoint:
    """Probe a single endpoint. Never raises."""
    base = spec.endpoint
    headers = {"Authorization": f"Bearer {spec.api_key}"} if spec.api_key else {}
    if spec.provider == "ollama":
        url, extract = f"{base}/api/tags", _extract_ollama_models
    elif spec.provider in _OPENAI_COMPAT_PROVIDERS:
        url, extract = f"{base}/v1/models", _extract_openai_models
    else:
        log.warning("unknown provider %r; treating as openai-compatible", spec.provider)
        url, extract = f"{base}/v1/models", _extract_openai_models
    try:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        models = extract(resp.json())
        return Endpoint(provider=spec.provider, base_url=base, models=models, reachable=True)
    except Exception as exc:
        log.info("endpoint %s unreachable: %s", base, exc)
        return Endpoint(provider=spec.provider, base_url=base, reachable=False)


def _extract_ollama_models(payload: dict) -> tuple[str, ...]:
    return tuple(str(m["name"]) for m in payload.get("models", []) if "name" in m)


def _extract_openai_models(payload: dict) -> tuple[str, ...]:
    return tuple(str(m["id"]) for m in payload.get("data", []) if "id" in m)


async def probe_endpoints(
    specs: list[ModelRef],
    timeout: float = 1.0,  # noqa: ASYNC109 - httpx probe timeout, not asyncio scope
) -> list[Endpoint]:
    """Probe each endpoint in ``specs`` concurrently. Never raises."""
    async with _client_factory(timeout) as client:
        return list(await asyncio.gather(*(_probe_one(client, s) for s in specs)))


async def resolve_model(
    settings: Settings,
    timeout: float = 1.0,  # noqa: ASYNC109 - probe timeout, not asyncio scope
) -> ModelRef:
    """Pick the first reachable model: ``settings.model``, then fallbacks.

    If the requested model id is absent from a reachable endpoint that serves
    exactly one model, the ref is corrected to that model. Raises
    :class:`BackendUnavailable` when no endpoint in the chain is usable.
    """
    chain = [settings.model, *settings.fallback_models]
    endpoints = await probe_endpoints(chain, timeout=timeout)
    for spec, endpoint in zip(chain, endpoints, strict=True):
        if not endpoint.reachable or not endpoint.models:
            continue
        if spec.model in endpoint.models or len(endpoint.models) != 1:
            return spec
        corrected = endpoint.models[0]
        log.info(
            "model %r absent on %s; using its only model %r",
            spec.model,
            endpoint.base_url,
            corrected,
        )
        return replace(spec, model=corrected)
    tried = ", ".join(f"{s.provider}:{s.model} ({s.endpoint})" for s in chain)
    raise BackendUnavailable(
        f"no reachable model endpoint (tried: {tried}). "
        "Start Ollama (`ollama serve`) or a vLLM/OpenAI-compatible server, "
        "or point --base-url / PXX_BASE_URL at a running endpoint."
    )
