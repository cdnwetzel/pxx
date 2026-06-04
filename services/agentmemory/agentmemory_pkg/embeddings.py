"""Embedding generation and vector search for observations."""

from __future__ import annotations

import logging
import threading
from typing import Optional

import numpy as np
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

# Global model instance (lazy-loaded)
_model: Optional[SentenceTransformer] = None
_model_lock = threading.Lock()


def get_model() -> SentenceTransformer:
    """Get or initialize the embedding model (thread-safe)."""
    global _model
    if _model is None:
        with _model_lock:
            # Double-check pattern: model might have been loaded by another thread
            if _model is None:
                logger.info("Loading sentence-transformers model (all-MiniLM-L6-v2)...")
                _model = SentenceTransformer("all-MiniLM-L6-v2")
    return _model


def embed_text(text: str) -> list[float]:
    """Generate embedding for text.

    Args:
        text: Text to embed

    Returns:
        Embedding vector as list of floats
    """
    model = get_model()
    embedding = model.encode(text, convert_to_numpy=False)
    return embedding.tolist()


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Generate embeddings for multiple texts (batch).

    Args:
        texts: List of texts to embed

    Returns:
        List of embedding vectors
    """
    if not texts:
        return []
    model = get_model()
    embeddings = model.encode(texts, convert_to_numpy=False)
    return [e.tolist() for e in embeddings]


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Compute cosine similarity between two vectors.

    Args:
        vec_a: First vector
        vec_b: Second vector

    Returns:
        Cosine similarity score (0-1)
    """
    a = np.array(vec_a)
    b = np.array(vec_b)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def vector_search(
    query: str, observation_embeddings: list[tuple[str, list[float]]]
) -> list[tuple[str, float]]:
    """Search observations by vector similarity.

    Args:
        query: Search query text
        observation_embeddings: List of (observation_id, embedding) tuples

    Returns:
        List of (observation_id, similarity_score) sorted by score (descending)
    """
    if not observation_embeddings:
        return []

    query_embedding = embed_text(query)
    results = [
        (obs_id, cosine_similarity(query_embedding, emb))
        for obs_id, emb in observation_embeddings
    ]
    # Sort by similarity score, descending
    results.sort(key=lambda x: x[1], reverse=True)
    return results
