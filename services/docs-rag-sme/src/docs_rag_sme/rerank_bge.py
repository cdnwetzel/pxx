"""bge cross-encoder reranker (optional, T2b).

Loaded only when DOCS_SME_RERANK=bge. Needs the `rerank` extra (torch +
sentence-transformers) and a one-time model download — see scripts/setup-rerank.sh.
A cross-encoder scores each (query, passage) jointly, which beats the bi-encoder
RRF ordering on precision; it runs on scraps of VRAM (the spare 5080/A1000).
"""

from __future__ import annotations

import os


def _auto_device() -> str:
    import torch

    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


class BGEReranker:
    def __init__(self, model_name: str | None = None, device: str | None = None) -> None:
        from sentence_transformers import CrossEncoder

        self.model_name = model_name or os.environ.get(
            "DOCS_SME_RERANK_MODEL", "BAAI/bge-reranker-v2-m3"
        )
        self.model = CrossEncoder(self.model_name, device=device or _auto_device(), max_length=512)

    def rerank(self, query: str, hits: list[dict], top_k: int) -> list[dict]:
        if not hits:
            return []
        scores = self.model.predict([(query, h["body"]) for h in hits])
        ranked = sorted(zip(hits, scores, strict=True), key=lambda x: x[1], reverse=True)
        return [{**h, "rerank_score": float(s)} for h, s in ranked[:top_k]]
