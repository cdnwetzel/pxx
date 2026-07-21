"""Detect which Ollama or vLLM endpoint to use.

Priority: explicit override > vLLM > Ollama (LAN/local) > Ollama (remote).
First reachable wins, 1-second timeout per probe.

Endpoints are configured via environment variables (all optional):

- ``PXX_OLLAMA_BASE``    — hard override; skip detection and use this URL.
- ``PXX_STUDIO_LAN_URL`` — primary Ollama URL (default ``http://localhost:11434``).
- ``PXX_STUDIO_REMOTE_URL`` — optional second Ollama URL (e.g. a remote host
  reachable over VPN); empty/unset is skipped.
- ``PXX_VLLM_URL``      — optional OpenAI-compatible vLLM endpoint(s)
  (default ``http://127.0.0.1:8003``). Comma-separated list allowed; probed
  in order, first reachable wins. ``PXX_VLLM_MODEL`` may be a matching
  comma-separated list to pair each URL with its served model id.

The "studio" endpoint name is historical; functionally it is just "primary
Ollama" (localhost by default, or PXX_STUDIO_LAN_URL). The old hardcoded
localhost candidate was retired — see _ollama_candidates.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

PROBE_TIMEOUT_SEC = 1.0
PROBE_RETRIES = 3  # a single missed probe on a busy vLLM must not drop the endpoint

# Default Ollama endpoint — localhost works whether Ollama runs on this machine
# or you point PXX_STUDIO_LAN_URL / PXX_OLLAMA_BASE at another host (e.g. a LAN
# box by hostname). Read via PXX_STUDIO_LAN_URL in _ollama_candidates().
DEFAULT_STUDIO_LAN = "http://localhost:11434"
DEFAULT_VLLM = "http://127.0.0.1:8003"  # optional vLLM endpoint (PXX_VLLM_URL)


@dataclass(frozen=True)
class Endpoint:
    name: str
    url: str
    backend: str = "ollama"  # "ollama" | "vllm"
    tensor_parallel: bool = False  # informational; True for vLLM TP-2 endpoints
    model: str | None = None  # per-endpoint served model id; None = backend default


def _probe_ollama(url: str) -> bool:
    """Return True iff `url` responds to /api/tags with an Ollama-shaped payload."""
    if not url:
        return False

    secret_path = Path.home() / ".config/pxx/studio-secret"
    secret = None
    if secret_path.exists():
        import contextlib

        with contextlib.suppress(OSError, ValueError):
            secret = secret_path.read_text().strip()

    def _try_probe(auth_header: str | None = None) -> bool:
        probe_url = f"{url}/api/tags"
        req = urllib.request.Request(probe_url)
        if auth_header:
            req.add_header("Authorization", auth_header)
        try:
            with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT_SEC) as resp:
                data = json.load(resp)
                return isinstance(data, dict) and "models" in data
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            return False

    # Try with auth if secret available, then fall back to unauthenticated
    return _try_probe(f"Bearer {secret}") if secret else _try_probe()


def _probe_vllm(url: str) -> bool:
    """Return True iff `url` responds to /v1/models with a vLLM/OpenAI models-list.

    Retried: a loaded vLLM (mid-inference, a scheduler/GC pause) can miss the 1s
    probe intermittently even when healthy. A single miss must NOT silently drop
    the edit endpoint and fall through to a wrong/absent Ollama — that exact race
    caused --loop edit rounds to misroute during live runs.
    """
    if not url:
        return False
    for attempt in range(PROBE_RETRIES):
        try:
            with urllib.request.urlopen(
                f"{url}/v1/models", timeout=PROBE_TIMEOUT_SEC
            ) as resp:
                data = json.load(resp)
                if isinstance(data, dict) and isinstance(data.get("data"), list):
                    return True
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            pass
        if attempt < PROBE_RETRIES - 1:
            time.sleep(0.2)
    return False


_probe = _probe_ollama  # backward-compat alias for test monkeypatches


def _ollama_candidates() -> list[Endpoint]:
    # The legacy hardcoded localhost:11434 candidate was removed: the local box
    # is primary now, and a hardcoded localhost probe hijacked edit detection
    # whenever anything unrelated (e.g. the local review model) listened on
    # :11434. Localhost Ollama is still reachable via PXX_STUDIO_LAN_URL's default.
    return [
        Endpoint(
            "studio_lan", os.environ.get("PXX_STUDIO_LAN_URL", DEFAULT_STUDIO_LAN)
        ),
        Endpoint("studio_remote", os.environ.get("PXX_STUDIO_REMOTE_URL", "")),
    ]


_candidates = _ollama_candidates  # backward-compat alias


def _vllm_candidates() -> list[Endpoint]:
    """Ordered vLLM candidates from PXX_VLLM_URL (comma-separated, first wins).

    PXX_VLLM_MODEL pairs positionally: entry N names the model served by URL N.
    A missing/empty entry means "use the VLLM_DEFAULT model" (Endpoint.model
    stays None). Single-URL configs keep the historical "m1_vllm" name so
    audit logs and banners stay comparable across the fleet.
    """
    urls = [
        u.strip()
        for u in os.environ.get("PXX_VLLM_URL", DEFAULT_VLLM).split(",")
        if u.strip()
    ]
    models = [m.strip() for m in os.environ.get("PXX_VLLM_MODEL", "").split(",")]
    named = [m for m in models if m]
    if named and len(named) != len(urls):
        unpaired = [u for i, u in enumerate(urls) if i >= len(models) or not models[i]]
        if unpaired:
            print(
                f"pxx: PXX_VLLM_MODEL names {len(named)} model(s) for "
                f"{len(urls)} vLLM URL(s) — default model will be used for: "
                f"{', '.join(unpaired)}",
                file=sys.stderr,
            )
    candidates = []
    for i, url in enumerate(urls):
        model = models[i] if i < len(models) and models[i] else None
        host = urllib.parse.urlsplit(url).hostname or f"vllm{i}"
        name = "m1_vllm" if len(urls) == 1 else f"vllm_{host.replace('.', '_')}"
        candidates.append(
            Endpoint(name, url, backend="vllm", tensor_parallel=True, model=model)
        )
    return candidates


def detect_endpoint(preferred_backend: str | None = None) -> Endpoint:
    override = os.environ.get("PXX_OLLAMA_BASE")
    if override:
        return Endpoint("override", override)

    if preferred_backend == "ollama":
        probe_pairs = [
            (_probe_ollama, _ollama_candidates()),
            (_probe_vllm, _vllm_candidates()),
        ]
    else:
        # Default: try vLLM first, then Ollama
        probe_pairs = [
            (_probe_vllm, _vllm_candidates()),
            (_probe_ollama, _ollama_candidates()),
        ]

    debug = os.environ.get("PXX_DEBUG") == "1"
    tried: list[str] = []
    for probe_fn, candidates in probe_pairs:
        for ep in candidates:
            if not ep.url:
                continue
            if probe_fn(ep.url):
                return ep
            tried.append(f"{ep.name} ({ep.url})")
            if debug:
                print(f"pxx: probe failed {ep.name} {ep.url}", file=sys.stderr)

    raise RuntimeError(
        "No Ollama or vLLM endpoint reachable. "
        f"Tried: {'; '.join(tried)}. Start Ollama locally "
        "(`ollama serve`), or set PXX_OLLAMA_BASE to your Ollama URL "
        "(e.g. http://localhost:11434)."
    )
