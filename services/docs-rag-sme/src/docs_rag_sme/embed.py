"""Local embedding via Ollama. Runtime stays local: embeddings come from the
already-running Ollama (`nomic-embed-text`, 768-dim), never a remote API."""

from __future__ import annotations

import os

import httpx

EMBED_MODEL = os.environ.get("DOCS_SME_EMBED_MODEL", "nomic-embed-text")
OLLAMA_URL = os.environ.get("DOCS_SME_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
EMBED_DIM = int(os.environ.get("DOCS_SME_EMBED_DIM", "768"))


class Embedder:
    def __init__(
        self,
        model: str = EMBED_MODEL,
        url: str = OLLAMA_URL,
        client: httpx.Client | None = None,
    ) -> None:
        self.model = model
        self.url = url
        self._client = client or httpx.Client(timeout=60.0)

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Batch-embed. Returns one 768-dim vector per input, order preserved."""
        if not texts:
            return []
        resp = self._client.post(
            f"{self.url}/api/embed",
            # truncate: a safety net so an over-long chunk degrades to a
            # truncated embedding instead of a 400. Chunking keeps inputs well
            # under the context window; this only ever bites pathological pages.
            json={"model": self.model, "input": texts, "truncate": True},
        )
        resp.raise_for_status()
        vectors = resp.json()["embeddings"]
        if any(len(v) != EMBED_DIM for v in vectors):
            raise ValueError(f"expected {EMBED_DIM}-dim embeddings, got {[len(v) for v in vectors]}")
        return vectors

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]

    def close(self) -> None:
        self._client.close()
