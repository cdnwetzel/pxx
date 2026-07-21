"""Rerank tests. Identity always runs; bge runs only if the rerank extra is
installed (skips cleanly otherwise)."""

from __future__ import annotations

import importlib.util

import pytest

from docs_rag_sme.rerank import IdentityReranker, get_reranker

_HITS = [
    {"title": "a", "body": "irrelevant filler text", "source_url": "u"},
    {"title": "b", "body": "asyncio.gather runs awaitables concurrently", "source_url": "u"},
]


def test_identity_truncates_preserving_order():
    out = IdentityReranker().rerank("asyncio.gather", _HITS, top_k=1)
    assert len(out) == 1
    assert out[0]["title"] == "a"  # identity keeps upstream order


def test_get_reranker_default_is_identity():
    assert isinstance(get_reranker(), IdentityReranker)


def test_get_reranker_rejects_unknown(monkeypatch):
    monkeypatch.setenv("DOCS_SME_RERANK", "bogus")
    with pytest.raises(ValueError):
        get_reranker()


@pytest.mark.skipif(
    importlib.util.find_spec("sentence_transformers") is None,
    reason="rerank extra not installed (uv sync --extra rerank)",
)
def test_bge_reorders_by_relevance(monkeypatch):
    # Use a small cross-encoder for speed; the prod default is bge-reranker-v2-m3.
    monkeypatch.setenv("DOCS_SME_RERANK_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
    from docs_rag_sme.rerank_bge import BGEReranker

    out = BGEReranker().rerank("how to use asyncio.gather", _HITS, top_k=2)
    # Cross-encoder must float the relevant passage to the top.
    assert out[0]["title"] == "b"
    assert "rerank_score" in out[0]
