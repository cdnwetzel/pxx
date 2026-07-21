"""Tests for HNSW vector index."""

import tempfile
from pathlib import Path

import pytest
from agentmemory_pkg.vector_index import VectorIndex, HNSWLIB_AVAILABLE


@pytest.mark.skipif(not HNSWLIB_AVAILABLE, reason="hnswlib not available")
class TestVectorIndex:
    """Test HNSW vector index."""

    def test_index_initialization(self):
        """Test index initialization."""
        index = VectorIndex(dimension=384)
        assert index.enabled == HNSWLIB_AVAILABLE
        assert index.get_size() == 0

    def test_add_embedding(self):
        """Test adding embeddings to index."""
        index = VectorIndex(dimension=384)

        # Create mock embeddings
        embedding1 = [0.1] * 384
        embedding2 = [0.2] * 384

        index.add_embedding("obs-1", embedding1)
        index.add_embedding("obs-2", embedding2)

        assert index.get_size() == 2

    def test_add_duplicate_embedding(self):
        """Test that duplicate embeddings are not re-added."""
        index = VectorIndex(dimension=384)

        embedding = [0.1] * 384
        index.add_embedding("obs-1", embedding)
        index.add_embedding("obs-1", embedding)

        # Should only be 1, not 2
        assert index.get_size() == 1

    def test_search_index(self):
        """Test searching the index."""
        index = VectorIndex(dimension=384)

        # Add embeddings
        embedding1 = [0.1] * 384
        embedding2 = [0.2] * 384  # Similar to embedding1
        embedding3 = [0.9] * 384  # Very different

        index.add_embedding("obs-1", embedding1)
        index.add_embedding("obs-2", embedding2)
        index.add_embedding("obs-3", embedding3)

        # Search for similar to embedding1
        results = index.search(embedding1, k=3)

        assert len(results) == 3
        # obs-1 should be most similar, obs-3 least similar
        assert results[0][0] == "obs-1"  # Exact match should rank first

    def test_search_returns_similarity_scores(self):
        """Test that search returns valid similarity scores (0-1 range)."""
        index = VectorIndex(dimension=384)

        embedding1 = [0.5] * 384
        embedding2 = [0.5] * 384

        index.add_embedding("obs-1", embedding1)
        index.add_embedding("obs-2", embedding2)

        results = index.search(embedding1, k=2)

        assert len(results) == 2
        # Scores should be mostly in 0-1 range (floating point may exceed slightly)
        for obs_id, score in results:
            assert -0.01 <= score <= 1.01  # Allow small floating point variance

    def test_search_respects_k(self):
        """Test that search respects k parameter."""
        index = VectorIndex(dimension=384)

        for i in range(10):
            embedding = [0.1 * (i + 1)] * 384
            index.add_embedding(f"obs-{i}", embedding)

        results = index.search([0.5] * 384, k=3)
        assert len(results) <= 3

    def test_remove_embedding(self):
        """Test removing embeddings from index (cleanup mappings).

        Note: HNSW doesn't support true deletion, so this just cleans up mappings.
        The actual embedding remains in the index but is no longer accessible.
        """
        index = VectorIndex(dimension=384)

        embedding = [0.1] * 384
        index.add_embedding("obs-1", embedding)
        assert index.get_size() == 1
        assert "obs-1" in index.reverse_map

        index.remove_embedding("obs-1")
        # Mapping is removed
        assert "obs-1" not in index.reverse_map

    def test_clear_index(self):
        """Test clearing the index."""
        index = VectorIndex(dimension=384)

        for i in range(5):
            embedding = [0.1 * (i + 1)] * 384
            index.add_embedding(f"obs-{i}", embedding)

        assert index.get_size() == 5

        index.clear()
        assert index.get_size() == 0

    def test_rebuild_index(self):
        """Test rebuilding index from embeddings dict."""
        index = VectorIndex(dimension=384)

        embeddings = {
            "obs-1": [0.1] * 384,
            "obs-2": [0.2] * 384,
            "obs-3": [0.3] * 384,
        }

        index.rebuild(embeddings)
        assert index.get_size() == 3

    def test_invalid_embedding_dimension(self):
        """Test that invalid embedding dimensions are handled."""
        index = VectorIndex(dimension=384)

        invalid_embedding = [0.1] * 100  # Wrong dimension
        index.add_embedding("obs-1", invalid_embedding)

        # Should still have size 0 (not added)
        assert index.get_size() == 0

    def test_search_empty_index(self):
        """Test searching empty index."""
        index = VectorIndex(dimension=384)

        results = index.search([0.5] * 384, k=10)
        assert results == []

    def test_thread_safety(self):
        """Test that index operations are thread-safe."""
        import threading

        index = VectorIndex(dimension=384)

        def add_embeddings():
            for i in range(10):
                embedding = [0.1 * (i + 1)] * 384
                index.add_embedding(
                    f"obs-{threading.current_thread().name}-{i}", embedding
                )

        threads = [
            threading.Thread(target=add_embeddings, name=f"thread-{i}")
            for i in range(3)
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Should have 30 embeddings (10 per thread)
        assert index.get_size() == 30

    def test_disabled_index(self):
        """Test graceful degradation when hnswlib not available."""
        # Create index that thinks it's disabled
        index = VectorIndex(dimension=384)
        index.enabled = False

        embedding = [0.1] * 384
        index.add_embedding("obs-1", embedding)

        # Should not add anything if disabled
        assert index.get_size() == 0

        results = index.search(embedding, k=10)
        assert results == []

    def test_save_and_load_persistence(self):
        """Test saving and loading index from disk."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create and populate index
            index1 = VectorIndex(dimension=384)
            embeddings = {
                "obs-1": [0.1] * 384,
                "obs-2": [0.2] * 384,
                "obs-3": [0.3] * 384,
            }
            index1.rebuild(embeddings)
            assert index1.get_size() == 3

            # Save to disk
            save_path = Path(tmpdir) / "vector_index"
            assert index1.save(str(save_path))
            assert (save_path / "hnsw.idx").exists()
            assert (save_path / "metadata.json").exists()

            # Load into new index
            index2 = VectorIndex(dimension=384)
            assert index2.load(str(save_path))
            assert index2.get_size() == 3

            # Verify embeddings can still be searched
            results = index2.search([0.15] * 384, k=3)
            assert len(results) == 3
            # Should find obs-1 as top result (closest to 0.15)
            assert results[0][0] == "obs-1"

    def test_load_nonexistent_path(self):
        """Test loading from nonexistent path."""
        index = VectorIndex(dimension=384)
        assert not index.load("/nonexistent/path")

    def test_schema_mismatch_on_load(self):
        """Test that schema mismatches are detected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create and save index with dimension 384
            index1 = VectorIndex(dimension=384)
            index1.add_embedding("obs-1", [0.1] * 384)
            save_path = Path(tmpdir) / "vector_index"
            index1.save(str(save_path))

            # Try to load with different dimension
            index2 = VectorIndex(dimension=768)  # Different dimension
            assert not index2.load(str(save_path))  # Should fail

    def test_rebuild_after_many_deletions(self):
        """Test that index can be rebuilt after deletions.

        Note: HNSW doesn't support true deletion, so get_size() (next_id)
        remains unchanged. Instead, rebuild() clears and repopulates.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create index with embeddings
            index1 = VectorIndex(dimension=384)
            embeddings = {f"obs-{i}": [float(i) / 100] * 384 for i in range(10)}
            index1.rebuild(embeddings)

            # Simulate deletions from mappings
            for i in range(5):
                index1.remove_embedding(f"obs-{i}")

            # Verify mappings cleaned up
            for i in range(5):
                assert f"obs-{i}" not in index1.reverse_map

            # But size (next_id) unchanged because HNSW doesn't support deletion
            assert index1.get_size() == 10

            # Rebuild with only remaining embeddings (clears and repopulates)
            remaining = {f"obs-{i}": [float(i) / 100] * 384 for i in range(5, 10)}
            index1.rebuild(remaining)
            assert index1.get_size() == 5

            # Save and load
            save_path = Path(tmpdir) / "vector_index"
            index1.save(str(save_path))

            index2 = VectorIndex(dimension=384)
            index2.load(str(save_path))
            assert index2.get_size() == 5

            # Verify only 5 embeddings accessible
            assert len(index2.id_map) == 5
            assert len(index2.reverse_map) == 5
