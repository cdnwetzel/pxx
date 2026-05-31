"""Detect which Ollama or vLLM endpoint to use.

Priority: explicit override > vLLM (Machine 1) > Ollama Studio LAN > Ollama Studio over VPN.
First reachable wins. 1-second timeout per probe.

Ollama instances: Studio (M4 Max, 36GB) + optional local on Neo.
vLLM: Machine 1 (Xeon + 2× RTX A4500, optional).
- LAN = on the office network (e.g. workstation:11434, resolvable via corp DNS or mDNS)
- Remote = Studio resolvable while connected to the SSL VPN
- vLLM = OpenAI-compatible endpoint on Machine 1 (default: :8000)
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

PROBE_TIMEOUT_SEC = 1.0

DEFAULT_STUDIO_LAN = "http://workstation.splawoffice.local:11434"
DEFAULT_NEO = "http://localhost:11434"
DEFAULT_VLLM = "http://workstation.splawoffice.local:8000"


@dataclass(frozen=True)
class Endpoint:
    name: str
    url: str
    backend: str = "ollama"        # "ollama" | "vllm"
    tensor_parallel: bool = False  # informational; True for vLLM TP-2 endpoints


def _probe_ollama(url: str) -> bool:
    """Return True iff `url` responds to /api/tags with an Ollama-shaped payload."""
    if not url:
        return False
    try:
        with urllib.request.urlopen(f"{url}/api/tags", timeout=PROBE_TIMEOUT_SEC) as resp:
            data = json.load(resp)
            return isinstance(data, dict) and "models" in data
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return False


def _probe_vllm(url: str) -> bool:
    """Return True iff `url` responds to /v1/models with a vLLM/OpenAI models-list payload."""
    if not url:
        return False
    try:
        with urllib.request.urlopen(f"{url}/v1/models", timeout=PROBE_TIMEOUT_SEC) as resp:
            data = json.load(resp)
            return isinstance(data, dict) and isinstance(data.get("data"), list)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return False


_probe = _probe_ollama  # backward-compat alias for test monkeypatches


def _ollama_candidates() -> list[Endpoint]:
    return [
        Endpoint("studio_lan", os.environ.get("PXX_STUDIO_LAN_URL", DEFAULT_STUDIO_LAN)),
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
