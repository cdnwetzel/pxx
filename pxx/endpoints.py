"""Detect which Ollama endpoint to use.

Priority: explicit override > Studio LAN > Studio over VPN.
First reachable wins. 1-second timeout per probe.

All Ollama instances run on the Studio (M4 Max, 36GB).
- LAN = on the office network (e.g. workstation:11434, resolvable via corp DNS or mDNS)
- Remote = Studio resolvable while connected to the SSL VPN
Neo (8GB MacBook) has no local Ollama and must reach the Studio over the network.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

PROBE_TIMEOUT_SEC = 1.0

DEFAULT_STUDIO_LAN = "http://workstation:11434"
DEFAULT_NEO = "http://localhost:11434"


@dataclass(frozen=True)
class Endpoint:
    name: str
    url: str


def _probe(url: str) -> bool:
    """Return True iff `url` responds to /api/tags with an Ollama-shaped payload.

    Tighter than a bare reachability check: confirms the response parses as
    JSON and contains a `models` key. Prevents probe success on a random HTTP
    responder that happens to be listening on the port.
    """
    if not url:
        return False
    try:
        with urllib.request.urlopen(f"{url}/api/tags", timeout=PROBE_TIMEOUT_SEC) as resp:
            data = json.load(resp)
            return isinstance(data, dict) and "models" in data
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
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
        "or confirm Studio is running Ollama on LAN."
    )
