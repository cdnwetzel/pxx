"""Embedding providers for memory vector search.

The :class:`Embedder` protocol returns raw float32 blobs (``bytes``), so the
store never needs numpy. :class:`HashEmbedder` is deterministic and fully
offline — it is the default and the test embedder. :class:`OllamaEmbedder`
calls a local Ollama ``/api/embed`` endpoint via httpx.
:func:`pick_embedder` probes Ollama with a short timeout and falls back to
:class:`HashEmbedder` (the decision is logged for audit).
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from array import array
from typing import Protocol, runtime_checkable

import httpx

log = logging.getLogger("pxx.memory.embeddings")

_TOKEN_RE = re.compile(r"[a-z0-9]+")


@runtime_checkable
class Embedder(Protocol):
    """Turns texts into float32 embedding blobs (one per input text)."""

    async def embed(self, texts: list[str]) -> list[bytes]:
        """Return one little-endian float32 blob per text."""
        ...

    @property
    def identity(self) -> str:
        """A stable string identifying the EMBEDDING SPACE this embedder produces
        (provider + model + any dimension/revision that changes the vectors).
        Two embedders whose vectors are NOT comparable MUST have different
        identities — the store stamps an index with this and refuses to compare
        query and stored vectors across a mismatch (see
        :meth:`pxx.memory.store.MemoryStore.set_embedder`)."""
        ...


def embedder_identity(embedder: object) -> str:
    """The embedding-space identity of ``embedder``. Falls back to the class name
    for embedders that predate the ``identity`` property, but WARNS when it does —
    a class name is a weak stamp (two differently-configured instances of the same
    class would collide), so a real embedder must expose an explicit ``identity``."""
    ident = getattr(embedder, "identity", None)
    if isinstance(ident, str) and ident:
        return ident
    fallback = type(embedder).__name__
    log.warning(
        "embedder %s exposes no `identity`; using the class name as its embedding-space "
        "stamp — differently-configured instances of this class would collide. Give it an "
        "explicit `identity` (provider + model + dimension).",
        fallback,
    )
    return fallback


class HashEmbedder:
    """Deterministic token-hashing embedder (feature hashing + L2 norm).

    Each lowercase alphanumeric token is hashed into one of ``dim`` buckets
    with a sign bit (the hashing trick); the resulting vector is
    L2-normalized. Identical text always yields an identical blob, and texts
    sharing tokens get positive cosine similarity — good enough for
    keyword-ish recall without any model or network.
    """

    def __init__(self, dim: int = 256) -> None:
        if dim <= 0:
            raise ValueError("dim must be positive")
        self.dim = dim

    @property
    def identity(self) -> str:
        # dim is part of the space: a different dim yields incomparable vectors.
        return f"hash:dim={self.dim}"

    async def embed(self, texts: list[str]) -> list[bytes]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> bytes:
        vec = array("f", [0.0]) * self.dim
        for token in _TOKEN_RE.findall(text.lower()):
            digest = hashlib.sha256(token.encode()).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dim
            vec[bucket] += 1.0 if digest[4] & 1 else -1.0
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0.0:
            vec = array("f", (v / norm for v in vec))
        return vec.tobytes()


class OllamaEmbedder:
    """Embeds via a local Ollama server's ``/api/embed`` endpoint."""

    def __init__(
        self, base_url: str, model: str = "nomic-embed-text", *, timeout: float = 30.0
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    @property
    def identity(self) -> str:
        # The MODEL NAME is the space discriminator: two 768-dim models produce
        # incomparable vectors, so the name (not just the dimension) must be in
        # the identity. (Provider prefix disambiguates from a same-named model on
        # a different backend.) NOTE: base_url is deliberately NOT included — the
        # same model+weights served on a different endpoint yields identical
        # vectors, so an endpoint move must not force a spurious reindex. Adding
        # the served model's immutable digest (to catch a same-name revision bump)
        # is a deliberate follow-up: it needs an async /api/show probe the sync
        # property can't make, and the name-vs-digest granularity is an open
        # coordination decision. Name-level is the honest floor until then.
        return f"ollama:{self.model}"

    async def embed(self, texts: list[str]) -> list[bytes]:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
            resp = await client.post("/api/embed", json={"model": self.model, "input": texts})
            resp.raise_for_status()
            data = resp.json()
        return [array("f", (float(x) for x in vec)).tobytes() for vec in data["embeddings"]]


async def pick_embedder(base_url: str | None, *, probe_timeout: float = 1.0) -> Embedder:
    """Return an :class:`OllamaEmbedder` when reachable, else :class:`HashEmbedder`.

    Never raises: any probe failure falls back to the offline embedder.
    """
    if base_url:
        try:
            async with httpx.AsyncClient(
                base_url=base_url.rstrip("/"), timeout=probe_timeout
            ) as client:
                resp = await client.get("/api/tags")
                resp.raise_for_status()
            log.info("memory embeddings: using ollama at %s", base_url)
            return OllamaEmbedder(base_url)
        except Exception:
            log.info("ollama unreachable at %s; using HashEmbedder", base_url)
    return HashEmbedder()
