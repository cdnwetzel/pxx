"""Concurrency and scale tests for agentmemory."""

import concurrent.futures
import tempfile
import time
from pathlib import Path


from agentmemory_pkg.embeddings import get_model
from agentmemory_pkg.search import SearchEngine
from agentmemory_pkg.storage import ObservationStore
from agentmemory_pkg.vector_index import VectorIndex


class TestEmbeddingsThreadSafety:
    """Test thread-safe model loading."""

    def test_concurrent_model_access(self):
        """Test that multiple threads can safely access the model."""
        results = []

        def load_and_embed(text: str, thread_id: int):
            from agentmemory_pkg import embeddings

            # Force reset to test concurrent loading
            with embeddings._model_lock:
                embeddings._model = None

            # Try to load model concurrently
            try:
                get_model()
                embedding = embeddings.embed_text(f"{text}_{thread_id}")
                results.append((thread_id, len(embedding)))
            except Exception as e:
                results.append((thread_id, str(e)))

        # Launch 3 threads trying to load model simultaneously
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futures = [
                executor.submit(load_and_embed, "test text", i)
                for i in range(3)
            ]
            concurrent.futures.wait(futures)

        # All threads should succeed
        assert len(results) == 3
        for thread_id, result in results:
            assert isinstance(result, int) and result == 384  # embedding dimension

    def test_concurrent_embedding_generation(self):
        """Test concurrent embedding generation on already-loaded model."""
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [
                executor.submit(
                    lambda i=i: len(get_model().encode(f"text_{i}", convert_to_numpy=False))
                )
                for i in range(5)
            ]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        assert len(results) == 5
        assert all(r == 384 for r in results)  # All same dimension


class TestConcurrentWrites:
    """Test concurrent observation writes."""

    def test_concurrent_observation_storage(self):
        """Test storing observations from multiple threads."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            store = ObservationStore(db_path=str(db_path))

            results = []

            def store_obs(thread_id: int, count: int):
                for i in range(count):
                    try:
                        obs = store.store(
                            f"project_{thread_id}",
                            f"content from thread {thread_id} iteration {i}",
                        )
                        results.append((thread_id, obs.id))
                    except Exception as e:
                        results.append((thread_id, str(e)))

            # Store observations from 3 threads
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                futures = [
                    executor.submit(store_obs, i, 5)
                    for i in range(3)
                ]
                concurrent.futures.wait(futures)

            # Should have 15 successful stores (3 threads × 5 obs each)
            assert len(results) == 15
            successful = [r for r in results if isinstance(r[1], str) and r[1].startswith("obs-")]
            assert len(successful) == 15

    def test_concurrent_search_with_writes(self):
        """Test concurrent search and write operations."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            store = ObservationStore(db_path=str(db_path))
            SearchEngine(store=store)

            # Pre-populate with some observations
            for i in range(10):
                store.store("project", f"machine learning concept {i}")

            results = []

            def search_and_store(thread_id: int):
                try:
                    # Do both search and store operations
                    for i in range(2):
                        obs = store.get_by_project("project")
                        results.append(("search", len(obs)))
                        obs = store.store("project", f"new content from thread {thread_id} iter {i}")
                        results.append(("store", obs.id))
                except Exception as e:
                    results.append(("error", str(e)))

            # Run 3 threads doing mixed operations
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                futures = [
                    executor.submit(search_and_store, i)
                    for i in range(3)
                ]
                concurrent.futures.wait(futures)

            # Should have 12 operations total (3 threads × 4 ops each)
            assert len(results) == 12
            # Should have 6 searches and 6 stores
            stores = [r for r in results if r[0] == "store"]
            searches = [r for r in results if r[0] == "search"]
            assert len(stores) == 6
            assert len(searches) == 6


class TestPerformanceBaselines:
    """Performance benchmarks at different scales."""

    def test_search_latency_1k_observations(self):
        """Benchmark search latency with 1k observations."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            store = ObservationStore(db_path=str(db_path))
            engine = SearchEngine(store=store)

            # Store 1k observations
            for i in range(1000):
                store.store("project", f"document about machine learning {i % 10}")

            # Time a search
            start = time.perf_counter()
            obs = store.get_by_project("project")
            results = engine.search("machine learning", obs, limit=10)
            elapsed = time.perf_counter() - start

            assert len(results) > 0
            # Should be reasonably fast (< 500ms for 1k obs)
            assert elapsed < 0.5

    def test_search_latency_10k_observations(self):
        """Benchmark search latency with 10k observations."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            store = ObservationStore(db_path=str(db_path))
            engine = SearchEngine(store=store)

            # Store 10k observations
            for i in range(10000):
                store.store("project", f"neural network research paper {i % 100}")

            # Time a search
            start = time.perf_counter()
            obs = store.get_by_project("project")
            results = engine.search("neural network", obs, limit=10)
            elapsed = time.perf_counter() - start

            assert len(results) > 0
            # Should scale linearly (< 2s for 10k obs)
            assert elapsed < 2.0

    def test_concurrent_search_stress(self):
        """Stress test concurrent searches."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            store = ObservationStore(db_path=str(db_path))
            engine = SearchEngine(store=store)

            # Store 1k observations
            for i in range(1000):
                store.store("project", f"algorithm complexity optimization {i % 20}")

            results = []

            def concurrent_search(thread_id: int):
                for _ in range(3):
                    obs = store.get_by_project("project")
                    search_results = engine.search(
                        "algorithm", obs, limit=5
                    )
                    results.append((thread_id, len(search_results)))

            # 5 threads, 3 searches each = 15 concurrent searches
            start = time.perf_counter()
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                futures = [
                    executor.submit(concurrent_search, i)
                    for i in range(5)
                ]
                concurrent.futures.wait(futures)
            elapsed = time.perf_counter() - start

            assert len(results) == 15
            # 15 searches should complete in reasonable time (< 10s)
            assert elapsed < 10.0

    def test_hnsw_rebuild_under_load(self):
        """Test HNSW rebuild while search is in progress."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            ObservationStore(db_path=str(db_path))
            index = VectorIndex(dimension=384, max_elements=10000)

            # Store initial embeddings
            initial_embeddings = {
                f"obs-{i}": [float(i) / 1000] * 384
                for i in range(100)
            }

            for obs_id, embedding in initial_embeddings.items():
                index.add_embedding(obs_id, embedding)

            search_results = []
            rebuild_results = []

            def search_loop():
                for _ in range(5):
                    results = index.search([0.5] * 384, k=10)
                    search_results.append(len(results))

            def rebuild_loop():
                new_embeddings = {
                    f"obs-new-{i}": [float(i) / 1000] * 384
                    for i in range(50)
                }
                index.rebuild(new_embeddings)
                rebuild_results.append(True)

            # Run search and rebuild concurrently
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                search_future = executor.submit(search_loop)
                rebuild_future = executor.submit(rebuild_loop)
                concurrent.futures.wait([search_future, rebuild_future])

            # Both should complete without errors
            assert len(search_results) == 5
            assert len(rebuild_results) == 1
