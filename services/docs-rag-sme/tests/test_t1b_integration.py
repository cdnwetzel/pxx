"""T1b end-to-end against the real local Postgres+pgvector and Ollama. Skips
cleanly when either service is down so the unit suite still runs anywhere."""

from __future__ import annotations

import httpx
import pytest

from docs_rag_sme.embed import EMBED_DIM, Embedder
from docs_rag_sme.ingest.models import DocChunk, FetchResult


def _ollama_up() -> bool:
    try:
        return httpx.get("http://127.0.0.1:11434/api/tags", timeout=1.0).status_code == 200
    except Exception:
        return False


def _pg_up() -> bool:
    try:
        from docs_rag_sme import store

        store.connect().close()
        return True
    except Exception:
        return False


needs_ollama = pytest.mark.skipif(not _ollama_up(), reason="ollama not running")
needs_pg = pytest.mark.skipif(not _pg_up(), reason="postgres/pgvector not running")


@needs_ollama
def test_embedder_returns_768_dim():
    emb = Embedder()
    vecs = emb.embed(["hello world", "asyncio.TaskGroup"])
    emb.close()
    assert len(vecs) == 2
    assert all(len(v) == EMBED_DIM for v in vecs)


@needs_pg
@needs_ollama
def test_upsert_search_roundtrip_and_version_filter():
    from docs_rag_sme import store

    conn = store.connect()
    store.init_schema(conn)
    # Isolate this test's rows by a unique source.
    src = "https://docs.python.org/3.12/_pytest_t1b.html"
    conn.execute("DELETE FROM doc_chunks WHERE source_url = %s", (src,))

    chunks = [
        DocChunk(source_url=src, title="asyncio.TaskGroup", text="group of tasks",
                 python_version="3.12", anchor="asyncio.TaskGroup", content_hash="h"),
        DocChunk(source_url=src, title="asyncio.gather", text="run awaitables concurrently",
                 python_version="3.12", anchor="asyncio.gather", content_hash="h"),
        DocChunk(source_url="https://docs.python.org/3.10/_pytest_t1b.html",
                 title="old.thing", text="run awaitables concurrently",
                 python_version="3.10", anchor="old.thing", content_hash="h"),
    ]
    emb = Embedder()
    vectors = emb.embed([c.text for c in chunks])
    n = store.upsert_chunks(conn, chunks, vectors)
    assert n == 3

    q = emb.embed_one("how do I run awaitables concurrently")
    hits = store.vector_search(conn, q, k=3, python_version="3.12")
    emb.close()
    assert hits, "expected at least one hit"
    # Version filter must exclude the 3.10 row entirely.
    assert all(h["python_version"] == "3.12" for h in hits)
    # Semantic recall: gather should outrank TaskGroup for this query.
    assert hits[0]["title"] == "asyncio.gather"

    conn.execute("DELETE FROM doc_chunks WHERE source_url = %s", (src,))
    conn.execute("DELETE FROM doc_chunks WHERE python_version = %s AND title = 'old.thing'", ("3.10",))
    conn.close()


@needs_pg
def test_page_state_delta():
    from docs_rag_sme import store

    conn = store.connect()
    store.init_schema(conn)
    url = "https://docs.python.org/3.12/_pytest_delta.html"
    store.record_page(conn, FetchResult(url=url, body="", content_hash="hash-v1"))
    assert store.page_hash(conn, url) == "hash-v1"
    store.record_page(conn, FetchResult(url=url, body="", content_hash="hash-v2"))
    assert store.page_hash(conn, url) == "hash-v2"
    conn.execute("DELETE FROM page_state WHERE url = %s", (url,))
    conn.close()
