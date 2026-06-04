import math
from .storage import Observation


class BM25Ranker:
    """BM25 relevance ranking for observations."""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1  # Term frequency saturation point
        self.b = b    # Length normalization parameter
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
                (self.num_docs - self.doc_freqs[token] + 0.5) /
                (self.doc_freqs[token] + 0.5) + 1
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
            idf = self.idf_cache.get(
                token,
                math.log((self.num_docs + 1) / 1.0)
            )

            # BM25 formula
            numerator = idf * tf * (self.k1 + 1)
            norm_factor = 1 - self.b + self.b * (doc_length / self.avg_doc_length)
            denominator = tf + self.k1 * norm_factor
            score += numerator / denominator

        return score

    def rank(
        self, query: str, observations: list[Observation]
    ) -> list[tuple[Observation, float]]:
        """Rank observations by relevance to query."""
        if not observations:
            return []

        # Re-index on each ranking (simple approach; could optimize)
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

    def __init__(self):
        self.ranker = BM25Ranker()

    def search(
        self,
        query: str,
        observations: list[Observation],
        limit: int = 10,
        min_score: float = 0.0,
    ) -> list[tuple[Observation, float]]:
        """Search and rank observations."""
        if not observations:
            return []

        ranked = self.ranker.rank(query, observations)

        # Filter by minimum score and limit
        return [
            (obs, score) for obs, score in ranked
            if score >= min_score
        ][:limit]
