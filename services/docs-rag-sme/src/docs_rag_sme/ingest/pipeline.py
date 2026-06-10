"""End-to-end ingest: fetch (allowlist-gated) → delta-check → chunk → embed →
upsert. Delta-skip means a refresh run only re-embeds pages whose content hash
changed, keeping recurring runs cheap."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
import psycopg

from ..embed import Embedder
from .. import store as store_mod
from .chunk import chunk as chunk_page
from .fetch import fetch


@dataclass(frozen=True, slots=True)
class IngestResult:
    url: str
    skipped: bool
    n_chunks: int
    reason: str


def ingest_url(
    url: str,
    conn: psycopg.Connection,
    embedder: Embedder,
    http: httpx.Client,
    *,
    force: bool = False,
) -> IngestResult:
    result = fetch(url, http)
    if not force and store_mod.page_hash(conn, url) == result.content_hash:
        return IngestResult(url, skipped=True, n_chunks=0, reason="unchanged")

    chunks = chunk_page(url, result.body, content_hash=result.content_hash)
    if not chunks:
        store_mod.record_page(conn, result)
        return IngestResult(url, skipped=True, n_chunks=0, reason="no-chunks")

    embeddings = embedder.embed([c.text for c in chunks])
    store_mod.upsert_chunks(conn, chunks, embeddings)
    store_mod.record_page(conn, result)
    return IngestResult(url, skipped=False, n_chunks=len(chunks), reason="ingested")
