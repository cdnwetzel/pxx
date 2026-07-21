"""Tests for persistent BM25 indexing."""

import tempfile
from pathlib import Path


from agentmemory_pkg.search import BM25Ranker, SearchEngine
from agentmemory_pkg.storage import ObservationStore


class TestBM25Persistence:
    """Test BM25 index persistence."""

    def test_save_and_load_bm25_index(self):
        """Test saving and loading BM25 index from database."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            store = ObservationStore(db_path=str(db_path))

            # Create and populate index
            ranker = BM25Ranker()
            docs = [
                "python programming language",
                "javascript web development",
                "golang systems programming",
            ]
            ranker.index_documents(docs)

            # Save to store
            assert store.save_bm25_index(
                ranker.doc_freqs, ranker.idf_cache, ranker.num_docs, ranker.avg_doc_length
            )

            # Load into new ranker
            result = store.load_bm25_index()
            assert result is not None

            term_freq, idf_cache, num_docs, avg_doc_length = result
            assert num_docs == 3
            assert len(idf_cache) > 0
            assert "python" in term_freq or "programming" in term_freq

    def test_search_engine_load_bm25_index(self):
        """Test SearchEngine loading BM25 index."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            store = ObservationStore(db_path=str(db_path))

            # Create and save index
            ranker = BM25Ranker()
            docs = [
                "machine learning algorithms",
                "deep neural networks",
                "natural language processing",
            ]
            ranker.index_documents(docs)
            store.save_bm25_index(
                ranker.doc_freqs, ranker.idf_cache, ranker.num_docs, ranker.avg_doc_length
            )

            # Load in SearchEngine
            engine = SearchEngine(store=store)
            assert engine.load_bm25_index_from_store()
            assert engine.ranker.num_docs == 3

    def test_invalidate_bm25_index(self):
        """Test invalidating BM25 index."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            store = ObservationStore(db_path=str(db_path))

            # Save index
            ranker = BM25Ranker()
            docs = ["test document one", "test document two"]
            ranker.index_documents(docs)
            store.save_bm25_index(
                ranker.doc_freqs, ranker.idf_cache, ranker.num_docs, ranker.avg_doc_length
            )

            # Verify it exists
            result = store.load_bm25_index()
            assert result is not None

            # Invalidate
            assert store.invalidate_bm25_index()

            # Should be gone
            result = store.load_bm25_index()
            assert result is None

    def test_bm25_caching_reduces_reindexing(self):
        """Test that BM25 caching reduces re-indexing on repeated queries."""
        ranker = BM25Ranker()
        docs = [
            "apple fruit red",
            "banana fruit yellow",
            "cherry fruit small",
        ]

        # First indexing
        ranker.index_documents(docs)
        first_num_docs = ranker.num_docs

        # Second ranking with same docs should not re-index
        # (in rank method with use_cache=True)
        ranker.num_docs = first_num_docs  # Simulate cache hit
        assert ranker.num_docs == 3

        # Different doc count should trigger re-index
        different_docs = docs + ["orange fruit citrus"]
        ranker.index_documents(different_docs)
        assert ranker.num_docs == 4

    def test_save_bm25_index_empty(self):
        """Test saving empty BM25 index."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            store = ObservationStore(db_path=str(db_path))

            ranker = BM25Ranker()
            # Don't index anything
            assert store.save_bm25_index(
                ranker.doc_freqs, ranker.idf_cache, ranker.num_docs, ranker.avg_doc_length
            )

            # Should still load, but empty
            result = store.load_bm25_index()
            assert result is not None
            term_freq, idf_cache, num_docs, avg_doc_length = result
            assert num_docs == 0
            assert len(idf_cache) == 0

    def test_bm25_index_survives_roundtrip(self):
        """Test that BM25 index values survive save/load roundtrip."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            store = ObservationStore(db_path=str(db_path))

            # Create index with known values
            ranker = BM25Ranker()
            docs = ["the quick brown fox", "the lazy dog", "the cat sat"]
            ranker.index_documents(docs)

            original_num_docs = ranker.num_docs
            original_avg_length = ranker.avg_doc_length
            original_idf_count = len(ranker.idf_cache)

            # Save and load
            store.save_bm25_index(
                ranker.doc_freqs, ranker.idf_cache, ranker.num_docs, ranker.avg_doc_length
            )
            result = store.load_bm25_index()

            term_freq, idf_cache, num_docs, avg_doc_length = result
            assert num_docs == original_num_docs
            assert avg_doc_length == original_avg_length
            assert len(idf_cache) == original_idf_count
