"""Detect which Ollama or vLLM endpoint to use.

Priority: explicit override > vLLM (T5810) > Ollama Studio LAN > Ollama Studio over VPN.
First reachable wins. 1-second timeout per probe.

Ollama: the Mac Studio (M4 Max, 36GB) runs Ollama locally; pxx now runs on
the Studio itself, so "studio" and "local" are the same machine.
vLLM: the T5810 (2× RTX A4500 20GB, NVLink) serves qwen2.5-coder-14b-coder-lora
behind an audit-proxy on :8003. The T5810 is SSH-only (office NAT forwards
only port 22), so it is reached through a persistent SSH local-forward
(launchd `local.pxx.gpu-node-1-vllm-tunnel` -> 127.0.0.1:8003). See
deploy/launchd/. Set PXX_VLLM_URL to override.
- LAN = on the office network (workstation:11434, resolvable via corp DNS or mDNS)
- Remote = Studio resolvable while connected to the SSL VPN
- vLLM = OpenAI-compatible endpoint reached via the SSH tunnel (default: 127.0.0.1:8003)
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

PROBE_TIMEOUT_SEC = 1.0

DEFAULT_STUDIO_LAN = "http://workstation.splawoffice.local:11434"
DEFAULT_NEO = "http://localhost:11434"
DEFAULT_VLLM = "http://127.0.0.1:8003"  # T5810 audit-proxy via SSH tunnel


@dataclass(frozen=True)
class Endpoint:
    name: str
    url: str
    backend: str = "ollama"  # "ollama" | "vllm"
    tensor_parallel: bool = False  # informational; True for vLLM TP-2 endpoints


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
    """Return True iff `url` responds to /v1/models with a vLLM/OpenAI models-list payload."""
    if not url:
        return False
    try:
        with urllib.request.urlopen(
            f"{url}/v1/models", timeout=PROBE_TIMEOUT_SEC
        ) as resp:
            data = json.load(resp)
            return isinstance(data, dict) and isinstance(data.get("data"), list)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return False


_probe = _probe_ollama  # backward-compat alias for test monkeypatches


def _ollama_candidates() -> list[Endpoint]:
    return [
        Endpoint(
            "studio_lan", os.environ.get("PXX_STUDIO_LAN_URL", DEFAULT_STUDIO_LAN)
        ),
        Endpoint("studio_remote", os.environ.get("PXX_STUDIO_REMOTE_URL", "")),
        Endpoint("neo", DEFAULT_NEO),
    ]


_candidates = _ollama_candidates  # backward-compat alias


def _vllm_candidates() -> list[Endpoint]:
    url = os.environ.get("PXX_VLLM_URL", DEFAULT_VLLM)
    return [Endpoint("m1_vllm", url, backend="vllm", tensor_parallel=True)]


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

    for probe_fn, candidates in probe_pairs:
        for ep in candidates:
            if probe_fn(ep.url):
                return ep

    raise RuntimeError(
        "No Ollama or vLLM endpoint reachable. "
        "Bring up the VPN to reach the Studio, "
        "or confirm Studio is running Ollama or vLLM on LAN."
    )
