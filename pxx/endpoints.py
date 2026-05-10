"""Detect which Ollama endpoint to use.

Priority: explicit override > Studio LAN > Studio over VPN > Neo localhost.
First reachable wins. 1-second timeout per probe.

LAN = mDNS / physical office network (e.g. mac-studio.local:11434).
Remote = work DNS resolvable while connected to the SSL VPN.
"""

from __future__ import annotations

import os
import urllib.error
import urllib.request
from dataclasses import dataclass

PROBE_TIMEOUT_SEC = 1.0

DEFAULT_STUDIO_LAN = "http://mac-studio.local:11434"
DEFAULT_NEO = "http://localhost:11434"


@dataclass(frozen=True)
class Endpoint:
    name: str
    url: str


def _probe(url: str) -> bool:
    if not url:
        return False
    try:
        with urllib.request.urlopen(f"{url}/api/tags", timeout=PROBE_TIMEOUT_SEC):
            return True
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _candidates() -> list[Endpoint]:
    return [
        Endpoint("studio_lan", os.environ.get("PXX_STUDIO_LAN_URL", DEFAULT_STUDIO_LAN)),
        Endpoint("studio_remote", os.environ.get("PXX_STUDIO_REMOTE_URL", "")),
        Endpoint("neo", DEFAULT_NEO),
    ]


def detect_endpoint() -> Endpoint:
    override = os.environ.get("PXX_OLLAMA_BASE")
    if override:
        return Endpoint("override", override)

    for ep in _candidates():
        if _probe(ep.url):
            return ep

    raise RuntimeError(
        "No Ollama endpoint reachable. "
        "Bring up the VPN to reach the Studio, "
        "or start Ollama locally (`brew services start ollama`) for offline fallback."
    )
