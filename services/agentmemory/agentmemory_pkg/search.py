import math
from .storage import Observation


class BM25Ranker:
    """BM25 relevance ranking for observations."""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1  # Term frequency saturation point
        self.b = b  # Length normalization parameter
        self.avg_doc_length = 0
        self.doc_freqs = {}
        self.idf_cache = {}
        self.num_docs = 0

    def index_documents(self, documents: list[str]) -> None:
        """Build index from documents."""
        self.num_docs = len(documents)
        total_length = sum(len(doc.split()) for doc in documents)
        self.avg_doc_length = total_length / max(1, len(documents))

        # Calculate document frequencies
        for doc in documents:
            tokens = set(doc.lower().split())
            for token in tokens:
                self.doc_freqs[token] = self.doc_freqs.get(token, 0) + 1

        # Pre-calculate IDF values
        for token in self.doc_freqs:
            idf = math.log(
                (self.num_docs - self.doc_freqs[token] + 0.5)
                / (self.doc_freqs[token] + 0.5)
                + 1
            )
            self.idf_cache[token] = idf

    def score(self, query: str, document: str) -> float:
        """Calculate BM25 score for document against query."""
        if self.num_docs == 0:
            return 0.0

        query_tokens = query.lower().split()
        doc_tokens = document.lower().split()
        doc_length = len(doc_tokens)

        score = 0.0
        for token in query_tokens:
            # Term frequency in document
            tf = sum(1 for t in doc_tokens if t == token)

            if tf == 0:
                continue

            # IDF (inverse document frequency)
            idf = self.idf_cache.get(token, math.log((self.num_docs + 1) / 1.0))

            # BM25 formula
            numerator = idf * tf * (self.k1 + 1)
            norm_factor = 1 - self.b + self.b * (doc_length / self.avg_doc_length)
            denominator = tf + self.k1 * norm_factor
            score += numerator / denominator

        return score

    def rank(
        self, query: str, observations: list[Observation], use_cache: bool = True
    ) -> list[tuple[Observation, float]]:
        """Rank observations by relevance to query.

        Args:
            query: Search query
            observations: Observations to rank
            use_cache: If True, only re-index if doc count changed
        """
        if not observations:
            return []

        # Only re-index if doc count changed (or no cache)
        if not use_cache or self.num_docs != len(observations):
            self.index_documents([obs.content for obs in observations])

        results = []
        for obs in observations:
            score = self.score(query, obs.content)
            if score > 0:
                results.append((obs, score))

        # Sort by score descending
        results.sort(key=lambda x: x[1], reverse=True)
        return results


class SearchEngine:
    """High-level search interface for observations."""

    def __init__(self, store=None):
        self.ranker = BM25Ranker()
        self.store = store
        # Optional vector index for fast similarity search
        self.vector_index = None
        try:
            from .vector_index import VectorIndex

            self.vector_index = VectorIndex()
        except Exception as e:
            import logging

            logging.warning(f"Vector index unavailable: {e}")

    def load_bm25_index_from_store(self) -> bool:
        """Load persisted BM25 index from database."""
        if not self.store:
            return False

        result = self.store.load_bm25_index()
        if result:
            term_freq, idf_cache, num_docs, avg_doc_length = result
            self.ranker.doc_freqs = term_freq
            self.ranker.idf_cache = idf_cache
            self.ranker.num_docs = num_docs
            self.ranker.avg_doc_length = avg_doc_length
            return True
        return False

    def save_bm25_index_to_store(self) -> bool:
        """Persist BM25 index to database."""
        if not self.store:
            return False

        return self.store.save_bm25_index(
            self.ranker.doc_freqs,
            self.ranker.idf_cache,
            self.ranker.num_docs,
            self.ranker.avg_doc_length,
        )

    def invalidate_bm25_index(self) -> bool:
        """Invalidate cached BM25 index."""
        if not self.store:
            return False
        return self.store.invalidate_bm25_index()

    def search(
        self,
        query: str,
        observations: list[Observation],
        limit: int = 10,
        min_score: float = 0.0,
        use_hybrid: bool = True,
    ) -> list[tuple[Observation, float]]:
        """Search and rank observations using BM25 or hybrid BM25+vector."""
        if not observations:
            return []

        if use_hybrid:
            return self._hybrid_search(query, observations, limit, min_score)
        else:
            ranked = self.ranker.rank(query, observations)
            return [(obs, score) for obs, score in ranked if score >= min_score][:limit]

    def _hybrid_search(
        self,
        query: str,
        observations: list[Observation],
        limit: int = 10,
        min_score: float = 0.0,
    ) -> list[tuple[Observation, float]]:
        """Hybrid search combining BM25 + vector similarity.

        Weighting: 40% BM25, 60% vector similarity.
        Uses HNSW index if available for fast approximate search.
        """
        from . import embeddings as emb_module

        # BM25 ranking
        bm25_results = self.ranker.rank(query, observations)
        bm25_scores = {obs.id: score for obs, score in bm25_results}

        # Normalize BM25 scores to 0-1 range
        max_bm25 = max(bm25_scores.values()) if bm25_scores else 1.0
        if max_bm25 == 0:
            max_bm25 = 1.0
        bm25_scores = {
            obs_id: score / max_bm25 for obs_id, score in bm25_scores.items()
        }

        # Vector search (skip observations without embeddings)
        vector_scores = {}
        obs_with_embeddings = [obs for obs in observations if obs.embedding is not None]

        if obs_with_embeddings:
            try:
                # Try HNSW index first (fast, ~O(log n))
                if (
                    self.vector_index
                    and self.vector_index.enabled
                    and self.vector_index.get_size() > 0
                ):
                    query_embedding = emb_module.embed_text(query)
                    vector_results = self.vector_index.search(
                        query_embedding, k=len(obs_with_embeddings)
                    )
                    max_vector = max((score for _, score in vector_results), default=0)
                    if max_vector > 0:
                        vector_scores = {
                            obs_id: score / max_vector
                            for obs_id, score in vector_results
                        }
                else:
                    # Fallback to brute-force search (O(n))
                    query_embedding = emb_module.embed_text(query)
                    vector_results = emb_module.vector_search(
                        query,
                        [(obs.id, obs.embedding) for obs in obs_with_embeddings],
                    )
                    max_vector = max((score for _, score in vector_results), default=0)
                    if max_vector > 0:
                        vector_scores = {
                            obs_id: score / max_vector
                            for obs_id, score in vector_results
                        }
            except Exception:
                # Graceful degradation if vector search fails
                pass

        # Combine scores: 40% BM25 + 60% vector
        combined_scores = {}
        for obs in observations:
            bm25 = bm25_scores.get(obs.id, 0.0)
            vector = vector_scores.get(obs.id, 0.0)
            combined = 0.4 * bm25 + 0.6 * vector
            if combined > 0:
                combined_scores[obs.id] = combined

        # Sort by combined score and return
        results = [
            (obs, combined_scores[obs.id])
            for obs in observations
            if obs.id in combined_scores
        ]
        results.sort(key=lambda x: x[1], reverse=True)

        return [(obs, score) for obs, score in results if score >= min_score][:limit]
