"""Retrieval + request augmentation for the proxy.

Flow: gate (is this request doc-relevant?) → embed query → hybrid_search →
rerank → format a context block injected *late* in the message list so Aider's
cached system+repo-map prefix stays intact (plan Decision B).
"""

from __future__ import annotations

import os
import re

import httpx
import psycopg

from . import store as store_mod
from .embed import Embedder
from .rerank import Reranker, get_reranker

# Gate: only spend retrieval on requests that plausibly want library docs —
# a dotted identifier, an import, a backticked symbol, or doc-intent words.
_DOC_HINT = re.compile(
    r"(\b(?:import|from)\s+\w)|(\b\w+\.\w+)|(`[^`]+`)"
    r"|\b(deprecat\w*|changelog|signature|docs?|documentation|latest|API|stdlib)\b",
    re.IGNORECASE,
)

# Distinctive module names that warrant retrieval even when mentioned bare (no
# dot) — e.g. "how does sqlite3 row_factory work". Curated to avoid common
# English words (so not 're'/'os'/'json'/'http'/'enum'/'abc'). Surfaced by the
# §6 A/B, which caught contextlib/sqlite3 questions slipping past the gate.
_MODULE_HINTS = frozenset({
    "asyncio", "contextlib", "dataclasses", "dataclass", "functools",
    "itertools", "collections", "pathlib", "subprocess", "sqlite3",
    "argparse", "tomllib", "urllib", "logging", "typing",
})
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

_TOP_K = int(os.environ.get("DOCS_SME_TOP_K", "5"))
_CANDIDATES = int(os.environ.get("DOCS_SME_CANDIDATES", "20"))


def is_augmentable(user_text: str) -> bool:
    if not user_text:
        return False
    if _DOC_HINT.search(user_text):
        return True
    return any(w.lower() in _MODULE_HINTS for w in _WORD.findall(user_text))


def format_context(hits: list[dict]) -> str:
    lines = [
        "Relevant excerpts from official documentation (current as of last "
        "ingest). Prefer these over recalled knowledge and cite the version "
        "when it matters:",
        "",
    ]
    for h in hits:
        ver = h.get("python_version") or h.get("package_version") or ""
        src = h.get("source_url", "")
        tag = f"{h['title']}" + (f" — {ver}" if ver else "")
        lines.append(f"### {tag}\n{h['body']}\n<source: {src}>\n")
    return "\n".join(lines)


class Retriever:
    """Holds the (sync) DB connection + embedder + reranker. Created in the
    proxy lifespan; if construction fails the proxy degrades to plain forward."""

    def __init__(
        self,
        conn: psycopg.Connection | None = None,
        embedder: Embedder | None = None,
        reranker: Reranker | None = None,
    ) -> None:
        self.conn = conn or store_mod.connect()
        self.embedder = embedder or Embedder()
        self.reranker = reranker or get_reranker()

    def retrieve(self, query_text: str, python_version: str | None = None) -> list[dict]:
        qv = self.embedder.embed_one(query_text)
        hits = store_mod.hybrid_search(
            self.conn, query_text, qv, k=_CANDIDATES, candidates=_CANDIDATES,
            python_version=python_version,
        )
        # Default-safe (plan §7): a version filter that returns nothing is
        # better than injecting wrong-version docs — so we do NOT widen the
        # search on an empty version-filtered result. Callers that want the
        # unfiltered fallback pass python_version=None explicitly.
        return self.reranker.rerank(query_text, hits, top_k=_TOP_K)

    def close(self) -> None:
        self.embedder.close()
        self.conn.close()


def last_user_text(messages: list[dict]) -> str:
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, list):  # OpenAI content-parts form
                content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
            return content or ""
    return ""


def inject_context(messages: list[dict], context: str) -> list[dict]:
    """Insert a system message carrying the retrieved context immediately
    before the final user turn — late enough to preserve the cached prefix."""
    ctx_msg = {"role": "system", "content": context}
    last_user_idx = max(
        (i for i, m in enumerate(messages) if m.get("role") == "user"), default=len(messages) - 1
    )
    return [*messages[:last_user_idx], ctx_msg, *messages[last_user_idx:]]


def augment_messages(
    messages: list[dict], retriever: Retriever, python_version: str | None = None
) -> tuple[list[dict], int]:
    """Return (possibly-augmented messages, n_chunks_injected)."""
    query = last_user_text(messages)
    if not is_augmentable(query):
        return messages, 0
    hits = retriever.retrieve(query, python_version=python_version)
    if not hits:
        return messages, 0
    return inject_context(messages, format_context(hits)), len(hits)


def make_http_client() -> httpx.Client:  # pragma: no cover - trivial
    return httpx.Client(timeout=60.0)
