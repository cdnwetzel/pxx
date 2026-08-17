"""Embedding-space versioning: the store stamps which embedder built its vectors
and fails closed when a different embedding space is attached over existing
vectors — cross-space cosine is meaningless (silent garbage), so the store refuses
to fabricate a similarity. Retrieval analogue of the content-truthfulness gate.

No network, no numpy: HashEmbedder + a tiny fake embedder, tmp_path everywhere.
"""

from __future__ import annotations

import asyncio
from array import array

import pytest

from pxx.memory.embeddings import HashEmbedder, OllamaEmbedder, embedder_identity
from pxx.memory.store import EmbeddingSpaceError, MemoryStore


def run(coro):
    return asyncio.run(coro)


class FakeEmbedder:
    """256-dim blobs, but a DIFFERENT identity than HashEmbedder(256) — models the
    dangerous case: same dimension, different model → incomparable vectors."""

    def __init__(self, identity: str = "ollama:bge-m3", dim: int = 256) -> None:
        self._identity = identity
        self._dim = dim

    @property
    def identity(self) -> str:
        return self._identity

    async def embed(self, texts: list[str]) -> list[bytes]:
        return [array("f", [0.1] * self._dim).tobytes() for _ in texts]


def _add(store, content="hello world"):
    return run(store.add("proj", "note", content))


# ---------------------------------------------------------------- identity


def test_embedder_identity_strings():
    assert HashEmbedder(256).identity == "hash:dim=256"
    assert HashEmbedder(128).identity == "hash:dim=128"  # dim is part of the space
    assert (
        OllamaEmbedder("http://x", model="nomic-embed-text").identity == "ollama:nomic-embed-text"
    )
    assert OllamaEmbedder("http://x", model="bge-m3").identity == "ollama:bge-m3"


def test_embedder_identity_fallback_to_classname():
    class NoIdentity:
        async def embed(self, texts):  # pragma: no cover - never called
            return []

    assert embedder_identity(NoIdentity()) == "NoIdentity"


# ---------------------------------------------------------------- stamping


def test_fresh_store_stamps_current_identity(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    store.set_embedder(HashEmbedder(256))
    assert store._stamped_identity() == "hash:dim=256"


def test_matching_embedder_reattach_is_ok(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    store.set_embedder(HashEmbedder(256))
    _add(store)
    assert store._has_embeddings()
    store.set_embedder(HashEmbedder(256))  # same identity, vectors present → fine
    assert store._stamped_identity() == "hash:dim=256"


# ---------------------------------------------------------------- NEGATIVE CONTROL


def test_mismatched_embedder_over_vectors_fails_closed_same_dim(tmp_path):
    """The silent-corruption case: SAME dimension (256), DIFFERENT model. Must
    raise rather than let search cosine across two incomparable spaces."""
    store = MemoryStore(tmp_path / "m.db")
    store.set_embedder(HashEmbedder(256))
    _add(store)  # stored vectors are in the hash:dim=256 space
    with pytest.raises(EmbeddingSpaceError) as exc:
        store.set_embedder(FakeEmbedder(identity="ollama:bge-m3", dim=256))
    msg = str(exc.value)
    assert "hash:dim=256" in msg and "ollama:bge-m3" in msg
    assert "reindex" in msg.lower() or "reset_embedding_space" in msg


def test_mismatched_embedder_over_vectors_fails_closed_diff_dim(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    store.set_embedder(HashEmbedder(256))
    _add(store)
    with pytest.raises(EmbeddingSpaceError):
        store.set_embedder(HashEmbedder(128))  # different dim → different identity


# ---------------------------------------------------------------- allowed transitions


def test_empty_store_adopts_new_embedder(tmp_path):
    # No vectors yet → switching the embedder is safe and re-stamps.
    store = MemoryStore(tmp_path / "m.db")
    store.set_embedder(HashEmbedder(256))
    store.set_embedder(HashEmbedder(128))  # empty: no error
    assert store._stamped_identity() == "hash:dim=128"


def test_reset_embedding_space_enables_reindex(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    store.set_embedder(HashEmbedder(256))
    _add(store, "original doc")
    with pytest.raises(EmbeddingSpaceError):
        store.set_embedder(FakeEmbedder(dim=256))
    store.reset_embedding_space()  # clears vectors + stamp AND detaches the embedder
    assert store._has_embeddings() is False
    store.set_embedder(FakeEmbedder(dim=256))  # now adopts the new space
    assert store._stamped_identity() == "ollama:bge-m3"
    # Reindex under the new embedder and confirm retrieval works in the new space.
    _add(store, "fresh doc in the new space")
    assert store._has_embeddings() is True
    results = run(store.search("proj", "fresh doc"))
    assert any("fresh doc" in o.content for o in results)


def test_detach_embedder_always_allowed(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    store.set_embedder(HashEmbedder(256))
    _add(store)
    store.set_embedder(None)  # detaching never raises


def test_readd_after_reset_reindexes_duplicate_content(tmp_path):
    """The reindex path must repopulate vectors for DUPLICATE content: after a
    reset NULLs the embedding, re-adding the same observation (dedup UPDATE path)
    must write the fresh vector, not leave it NULL."""
    store = MemoryStore(tmp_path / "m.db")
    store.set_embedder(HashEmbedder(256))
    _add(store, "same content")
    store.reset_embedding_space()
    assert store._has_embeddings() is False
    store.set_embedder(HashEmbedder(256))  # re-attach (same space here)
    _add(store, "same content")  # duplicate hash -> UPDATE path
    assert store._has_embeddings() is True  # vector repopulated, not left NULL


def test_dedup_add_without_embedder_does_not_wipe_vector(tmp_path):
    """COALESCE guard: a dedup add while no embedder is attached (embedding=None)
    must KEEP the existing vector, never NULL it."""
    store = MemoryStore(tmp_path / "m.db")
    store.set_embedder(HashEmbedder(256))
    _add(store, "keep me")
    assert store._has_embeddings() is True
    store.set_embedder(None)  # detach
    _add(store, "keep me")  # duplicate hash, no embedder -> embedding is None
    assert store._has_embeddings() is True  # existing vector preserved


# ---------------------------------------------------------------- migration


def test_preexisting_unstamped_vectors_assume_current_and_warn(tmp_path, caplog):
    """A store built before versioning has vectors but no stamp: on attach we
    assume the current embedder (common no-change case) and WARN, not raise."""
    path = tmp_path / "m.db"
    store = MemoryStore(path)
    store.set_embedder(HashEmbedder(256))
    _add(store)
    # Simulate a pre-versioning db: drop the stamp but keep the vectors.
    store._db.execute("DELETE FROM meta WHERE key = 'embedding_identity'")
    store._db.commit()
    assert store._stamped_identity() is None and store._has_embeddings()
    with caplog.at_level("WARNING", logger="pxx.memory.store"):
        store.set_embedder(HashEmbedder(256))  # no raise
    assert store._stamped_identity() == "hash:dim=256"
    assert "no embedding-space stamp" in caplog.text
