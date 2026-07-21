"""Tests for vector search and hybrid search."""

import pytest
from agentmemory_pkg import embeddings
from agentmemory_pkg.storage import Observation
from agentmemory_pkg.search import SearchEngine


class TestEmbeddings:
    """Test embedding generation."""

    def test_embed_single_text(self):
        """Test embedding a single text."""
        text = "This is a test observation about code changes"
        embedding = embeddings.embed_text(text)

        assert isinstance(embedding, list)
        assert len(embedding) == 384  # all-MiniLM-L6-v2 uses 384-dim vectors
        assert all(isinstance(x, float) for x in embedding)

    def test_embed_multiple_texts(self):
        """Test batch embedding."""
        texts = [
            "First observation about editing files",
            "Second observation about adding features",
            "Third observation about fixing bugs",
        ]
        embeddings_list = embeddings.embed_texts(texts)

        assert len(embeddings_list) == 3
        assert all(len(e) == 384 for e in embeddings_list)

    def test_embed_empty_list(self):
        """Test batch embedding with empty list."""
        embeddings_list = embeddings.embed_texts([])
        assert embeddings_list == []

    def test_cosine_similarity(self):
        """Test cosine similarity computation."""
        vec_a = [1.0, 0.0, 0.0]
        vec_b = [1.0, 0.0, 0.0]
        vec_c = [0.0, 1.0, 0.0]

        # Same vector should have similarity 1.0
        sim_same = embeddings.cosine_similarity(vec_a, vec_b)
        assert sim_same == pytest.approx(1.0)

        # Orthogonal vectors should have similarity 0.0
        sim_orthogonal = embeddings.cosine_similarity(vec_a, vec_c)
        assert sim_orthogonal == pytest.approx(0.0)

    def test_cosine_similarity_zero_vector(self):
        """Test cosine similarity with zero vectors."""
        zero = [0.0, 0.0, 0.0]
        vec = [1.0, 2.0, 3.0]

        sim = embeddings.cosine_similarity(zero, vec)
        assert sim == 0.0

    def test_vector_search(self):
        """Test vector similarity search."""
        # Create some test embeddings
        query = "editing Python code"
        embeddings.embed_text(query)

        obs_embeddings = [
            ("obs-1", embeddings.embed_text("edited Python file")),
            ("obs-2", embeddings.embed_text("deleted old function")),
            ("obs-3", embeddings.embed_text("added new Python module")),
        ]

        results = embeddings.vector_search(query, obs_embeddings)

        # Should return all results sorted by similarity
        assert len(results) <= len(obs_embeddings)
        assert all(isinstance(score, float) for _, score in results)
        # Scores should be sorted descending
        assert results[0][1] >= results[-1][1]


class TestHybridSearch:
    """Test hybrid BM25 + vector search."""

    def test_hybrid_search_combines_signals(self):
        """Test that hybrid search combines BM25 and vector signals."""
        engine = SearchEngine()

        # Create observations with embeddings
        obs1 = Observation(
            id="obs-1",
            project="test",
            content="edited Python file for bug fixes",
            created_at="2024-01-01T00:00:00",
            last_accessed="2024-01-01T00:00:00",
            access_count=0,
            embedding=embeddings.embed_text("edited Python file"),
        )
        obs2 = Observation(
            id="obs-2",
            project="test",
            content="removed old JavaScript code",
            created_at="2024-01-01T00:00:00",
            last_accessed="2024-01-01T00:00:00",
            access_count=0,
            embedding=embeddings.embed_text("removed old JavaScript"),
        )

        # Query about Python should prefer obs1
        results = engine.search(
            "Python code changes", [obs1, obs2], limit=2, use_hybrid=True
        )

        assert len(results) == 2
        assert results[0][0].id == "obs-1"

    def test_hybrid_search_graceful_degradation(self):
        """Test hybrid search works with missing embeddings."""
        engine = SearchEngine()

        obs1 = Observation(
            id="obs-1",
            project="test",
            content="Python code change",
            created_at="2024-01-01T00:00:00",
            last_accessed="2024-01-01T00:00:00",
            access_count=0,
            embedding=embeddings.embed_text("Python"),
        )
        obs2 = Observation(
            id="obs-2",
            project="test",
            content="JavaScript change",
            created_at="2024-01-01T00:00:00",
            last_accessed="2024-01-01T00:00:00",
            access_count=0,
            embedding=None,  # No embedding
        )

        # Should still work, using BM25 for obs2
        results = engine.search("Python", [obs1, obs2], use_hybrid=True)
        assert len(results) > 0
        assert results[0][0].id == "obs-1"

    def test_hybrid_search_vs_bm25(self):
        """Test that hybrid search can differ from BM25-only."""
        engine = SearchEngine()

        # Create observations that test the difference
        obs1 = Observation(
            id="obs-1",
            project="test",
            content="Python Python Python",  # Repeated keyword
            created_at="2024-01-01T00:00:00",
            last_accessed="2024-01-01T00:00:00",
            access_count=0,
            embedding=embeddings.embed_text("JavaScript"),  # Semantically different
        )
        obs2 = Observation(
            id="obs-2",
            project="test",
            content="language implementation details",
            created_at="2024-01-01T00:00:00",
            last_accessed="2024-01-01T00:00:00",
            access_count=0,
            embedding=embeddings.embed_text("Python programming language"),
        )

        # Query about Python
        bm25_results = engine.search("Python", [obs1, obs2], use_hybrid=False)
        hybrid_results = engine.search("Python", [obs1, obs2], use_hybrid=True)

        # Rankings might differ due to semantic understanding
        assert len(bm25_results) > 0
        assert len(hybrid_results) > 0

    def test_hybrid_search_empty(self):
        """Test hybrid search with no observations."""
        engine = SearchEngine()
        results = engine.search("query", [], use_hybrid=True)
        assert results == []

    def test_hybrid_search_respects_limit(self):
        """Test that hybrid search respects the limit parameter."""
        engine = SearchEngine()

        observations = [
            Observation(
                id=f"obs-{i}",
                project="test",
                content=f"observation number {i} about code changes",
                created_at="2024-01-01T00:00:00",
                last_accessed="2024-01-01T00:00:00",
                access_count=0,
                embedding=embeddings.embed_text(f"observation {i}"),
            )
            for i in range(10)
        ]

        results = engine.search("code", observations, limit=3, use_hybrid=True)
        assert len(results) <= 3

    def test_hybrid_search_respects_min_score(self):
        """Test that hybrid search respects the min_score parameter."""
        engine = SearchEngine()

        obs1 = Observation(
            id="obs-1",
            project="test",
            content="completely unrelated content about cooking",
            created_at="2024-01-01T00:00:00",
            last_accessed="2024-01-01T00:00:00",
            access_count=0,
            embedding=embeddings.embed_text("cooking recipes"),
        )
        obs2 = Observation(
            id="obs-2",
            project="test",
            content="code changes in Python file",
            created_at="2024-01-01T00:00:00",
            last_accessed="2024-01-01T00:00:00",
            access_count=0,
            embedding=embeddings.embed_text("Python code"),
        )

        # With high min_score, should filter out irrelevant result
        results = engine.search(
            "Python code", [obs1, obs2], min_score=0.5, use_hybrid=True
        )
        assert len(results) > 0
        assert all(score >= 0.5 for _, score in results)
