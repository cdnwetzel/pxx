"""Pluggable rerank stage. Default is identity (RRF order is already decent).

A cross-encoder reranker (bge-reranker-v2-m3) is a meaningful quality bump but
needs a torch model download — a separate infra step (see scripts/setup-rerank.sh,
staged). Until then `IdentityReranker` keeps the path working with no heavy deps.
Select via DOCS_SME_RERANK={none|bge}.
"""

from __future__ import annotations

import os
from typing import Protocol


class Reranker(Protocol):
    def rerank(self, query: str, hits: list[dict], top_k: int) -> list[dict]: ...


class IdentityReranker:
    """No-op: trust the upstream (RRF) ordering, just truncate to top_k."""

    def rerank(self, query: str, hits: list[dict], top_k: int) -> list[dict]:
        return hits[:top_k]


def get_reranker() -> Reranker:
    kind = os.environ.get("DOCS_SME_RERANK", "none").lower()
    if kind in {"none", "", "identity"}:
        return IdentityReranker()
    if kind == "bge":
        from .rerank_bge import BGEReranker  # imported lazily; torch optional

        return BGEReranker()
    raise ValueError(f"unknown DOCS_SME_RERANK={kind!r}")
