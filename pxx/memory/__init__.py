"""Persistent observation memory.

Store observations (learnings, file changes, tool outcomes) per project in a
local SQLite db; inject the relevant slice into the next session's prompt.
Memory is context, never policy.
"""

from __future__ import annotations

from .capture import (
    NewObservation,
    observations_from_events,
    observations_from_git,
    record_observations,
)
from .embeddings import Embedder, HashEmbedder, OllamaEmbedder, pick_embedder
from .inject import build_context
from .store import EVIDENCE_RANK, MemoryStats, MemoryStore, Observation

__all__ = [
    "EVIDENCE_RANK",
    "Embedder",
    "HashEmbedder",
    "MemoryStats",
    "MemoryStore",
    "NewObservation",
    "Observation",
    "OllamaEmbedder",
    "build_context",
    "observations_from_events",
    "observations_from_git",
    "pick_embedder",
    "record_observations",
]
