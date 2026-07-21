"""Runtime configuration, all via environment so the proxy stays stateless.

Design principle (the project GOAL): at *runtime* this service talks only to a
local LLM. It never reaches an external API. The only cloud LLM involved is the
one used to *build* these tools. Keep it that way — any future component that
wants the network must go through the ingestion allowlist, never the proxy.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Settings:
    # Downstream OpenAI-compatible server. Default = the gpu-node-1 audit-proxy as
    # reached through the SSH tunnel (same value as pxx's DEFAULT_VLLM).
    upstream: str
    host: str
    port: int
    # Per-request upstream timeout (seconds). Generous: local generation of a
    # long diff can take a while. None disables the read timeout.
    timeout: float | None
    # Retrieval augmentation. When off (or when the store/embedder can't be
    # reached) the proxy degrades to a plain verbatim forwarder.
    retrieval: bool

    @classmethod
    def from_env(cls) -> Settings:
        timeout_raw = os.environ.get("DOCS_SME_TIMEOUT", "600")
        return cls(
            upstream=os.environ.get("DOCS_SME_UPSTREAM", "http://127.0.0.1:8003").rstrip("/"),
            host=os.environ.get("DOCS_SME_HOST", "127.0.0.1"),
            port=int(os.environ.get("DOCS_SME_PORT", "8004")),
            timeout=None if timeout_raw in {"0", "none", ""} else float(timeout_raw),
            retrieval=os.environ.get("DOCS_SME_RETRIEVAL", "on").lower() != "off",
        )
