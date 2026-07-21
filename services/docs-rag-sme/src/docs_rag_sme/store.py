"""Postgres + pgvector store. Vectors for semantic recall, a generated
tsvector for exact-identifier (BM25-ish) matching — both indexed so T2 can do
hybrid retrieval. SQL filtering on python_version/package is the version-aware
edge. page_state holds per-URL hashes/validators for delta-only refresh.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import numpy as np
import psycopg
from pgvector.psycopg import register_vector

from .embed import EMBED_DIM
from .ingest.models import DocChunk, FetchResult

DSN = os.environ.get("DOCS_SME_DSN", "postgresql://localhost/docs_sme")

_SCHEMA = f"""
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS doc_chunks (
    chunk_id        text PRIMARY KEY,
    source_url      text NOT NULL,
    title           text NOT NULL,
    body            text NOT NULL,
    python_version  text,
    package         text,
    package_version text,
    last_modified   text,
    anchor          text,
    content_hash    text,
    embedding       vector({EMBED_DIM}),
    -- translate('._' -> spaces) so dotted identifiers (asyncio.TaskGroup)
    -- split into searchable tokens; otherwise they index as one opaque lexeme.
    tsv             tsvector GENERATED ALWAYS AS
                      (to_tsvector('english',
                        translate(coalesce(title,'') || ' ' || coalesce(body,''), '._', '  '))) STORED
);
CREATE INDEX IF NOT EXISTS doc_chunks_tsv_idx ON doc_chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS doc_chunks_vec_idx ON doc_chunks
    USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS doc_chunks_pyver_idx ON doc_chunks (python_version);
CREATE INDEX IF NOT EXISTS doc_chunks_pkg_idx ON doc_chunks (package);

CREATE TABLE IF NOT EXISTS page_state (
    url           text PRIMARY KEY,
    content_hash  text,
    etag          text,
    last_modified text,
    source_url    text
);
"""


def connect(dsn: str = DSN) -> psycopg.Connection:
    conn = psycopg.connect(dsn, autocommit=True)
    register_vector(conn)
    return conn


def init_schema(conn: psycopg.Connection) -> None:
    conn.execute(_SCHEMA)


def upsert_chunks(
    conn: psycopg.Connection,
    chunks: Sequence[DocChunk],
    embeddings: Sequence[list[float]],
) -> int:
    if len(chunks) != len(embeddings):
        raise ValueError("chunks and embeddings length mismatch")
    rows = [
        (
            c.chunk_id, c.source_url, c.title, c.text, c.python_version,
            c.package, c.package_version, c.last_modified, c.anchor,
            c.content_hash, emb,
        )
        for c, emb in zip(chunks, embeddings, strict=True)
    ]
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO doc_chunks (chunk_id, source_url, title, body,
                python_version, package, package_version, last_modified,
                anchor, content_hash, embedding)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (chunk_id) DO UPDATE SET
                source_url=EXCLUDED.source_url, title=EXCLUDED.title,
                body=EXCLUDED.body, python_version=EXCLUDED.python_version,
                package=EXCLUDED.package, package_version=EXCLUDED.package_version,
                last_modified=EXCLUDED.last_modified, anchor=EXCLUDED.anchor,
                content_hash=EXCLUDED.content_hash, embedding=EXCLUDED.embedding
            """,
            rows,
        )
    return len(rows)


def page_hash(conn: psycopg.Connection, url: str) -> str | None:
    row = conn.execute("SELECT content_hash FROM page_state WHERE url=%s", (url,)).fetchone()
    return row[0] if row else None


def record_page(conn: psycopg.Connection, result: FetchResult) -> None:
    conn.execute(
        """
        INSERT INTO page_state (url, content_hash, etag, last_modified)
        VALUES (%s,%s,%s,%s)
        ON CONFLICT (url) DO UPDATE SET
            content_hash=EXCLUDED.content_hash, etag=EXCLUDED.etag,
            last_modified=EXCLUDED.last_modified
        """,
        (result.url, result.content_hash, result.etag, result.last_modified),
    )


def vector_search(
    conn: psycopg.Connection,
    query_embedding: list[float],
    k: int = 5,
    python_version: str | None = None,
) -> list[dict]:
    """Cosine-nearest chunks, optionally filtered by python_version. Full hybrid
    (vector + tsv rank + rerank) is T2; this verifies the store end-to-end."""
    qv = np.asarray(query_embedding, dtype=np.float32)
    where = "WHERE python_version = %s" if python_version else ""
    sql = f"""
        SELECT chunk_id, title, source_url, python_version, package,
               1 - (embedding <=> %s) AS score
        FROM doc_chunks
        {where}
        ORDER BY embedding <=> %s
        LIMIT %s
    """
    # The distance operand appears twice (SELECT score + ORDER BY); pass it twice.
    params = [qv, *([python_version] if python_version else []), qv, k]
    rows = conn.execute(sql, params).fetchall()
    cols = ["chunk_id", "title", "source_url", "python_version", "package", "score"]
    return [dict(zip(cols, r, strict=True)) for r in rows]


def _rows_to_dicts(rows, cols) -> list[dict]:
    return [dict(zip(cols, r, strict=True)) for r in rows]


def hybrid_search(
    conn: psycopg.Connection,
    query_text: str,
    query_embedding: list[float],
    k: int = 5,
    candidates: int = 20,
    python_version: str | None = None,
) -> list[dict]:
    """Reciprocal-rank fusion of semantic (vector) and lexical (tsvector)
    retrieval. The lexical leg catches exact identifiers (`asyncio.TaskGroup`)
    that embeddings smear; the vector leg catches paraphrased intent. RRF needs
    no score calibration between the two. Returns chunk dicts incl. body text.
    """
    qv = np.asarray(query_embedding, dtype=np.float32)
    vfilter = "WHERE python_version = %s" if python_version else ""
    cols = ["chunk_id", "title", "body", "source_url", "python_version", "package"]
    sel = ", ".join(cols)

    vec_sql = f"SELECT {sel} FROM doc_chunks {vfilter} ORDER BY embedding <=> %s LIMIT %s"
    vec_params = [*([python_version] if python_version else []), qv, candidates]
    vec_rows = _rows_to_dicts(conn.execute(vec_sql, vec_params).fetchall(), cols)

    lex_filter = "AND python_version = %s" if python_version else ""
    lex_sql = f"""
        SELECT {sel} FROM doc_chunks
        WHERE tsv @@ plainto_tsquery('english', translate(%s, '._', '  ')) {lex_filter}
        ORDER BY ts_rank(tsv, plainto_tsquery('english', translate(%s, '._', '  '))) DESC
        LIMIT %s
    """
    lex_params = [query_text, *([python_version] if python_version else []), query_text, candidates]
    lex_rows = _rows_to_dicts(conn.execute(lex_sql, lex_params).fetchall(), cols)

    return _rrf_fuse(vec_rows, lex_rows, k=k)


def _rrf_fuse(vec_rows: list[dict], lex_rows: list[dict], k: int, k0: int = 60) -> list[dict]:
    scores: dict[str, float] = {}
    by_id: dict[str, dict] = {}
    for ranked in (vec_rows, lex_rows):
        for rank, row in enumerate(ranked):
            cid = row["chunk_id"]
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k0 + rank)
            by_id.setdefault(cid, row)
    ordered = sorted(scores, key=lambda c: scores[c], reverse=True)[:k]
    return [{**by_id[c], "score": scores[c]} for c in ordered]


def count(conn: psycopg.Connection) -> int:
    return conn.execute("SELECT count(*) FROM doc_chunks").fetchone()[0]
