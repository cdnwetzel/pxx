"""HNSW-based vector index for fast approximate nearest neighbor search."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# Optional HNSW support
try:
    import hnswlib

    HNSWLIB_AVAILABLE = True
except ImportError:
    HNSWLIB_AVAILABLE = False
    logger.warning("hnswlib not available — vector index will use brute-force search")


class VectorIndex:
    """HNSW-based vector index for fast similarity search.

    Provides approximate nearest neighbor search with O(log n) complexity.
    Falls back to brute-force if hnswlib unavailable.
    """

    def __init__(self, dimension: int = 384, max_elements: int = 100000):
        """Initialize vector index.

        Args:
            dimension: Embedding dimension (default 384 for all-MiniLM-L6-v2)
            max_elements: Max observations to index (can grow dynamically)
        """
        self.dimension = dimension
        self.max_elements = max_elements
        self.index: Optional[hnswlib.Index] = None
        self.id_map: dict[int, str] = {}  # Internal ID → observation ID
        self.reverse_map: dict[str, int] = {}  # Observation ID → internal ID
        self.next_id = 0
        self.lock = threading.Lock()
        self.enabled = HNSWLIB_AVAILABLE

        if self.enabled:
            self._init_index()

    def _init_index(self) -> None:
        """Initialize HNSW index."""
        self.index = hnswlib.Index(space="cosine", dim=self.dimension)
        self.index.init_index(
            max_elements=self.max_elements,
            ef_construction=200,
            M=16,
        )
        self.index.set_ef(50)  # ef parameter for search

    def add_embedding(self, obs_id: str, embedding: list[float]) -> None:
        """Add observation embedding to index.

        Args:
            obs_id: Observation ID
            embedding: Embedding vector
        """
        if not self.enabled:
            return

        if not embedding or len(embedding) != self.dimension:
            logger.warning(f"Invalid embedding for {obs_id}")
            return

        with self.lock:
            # Skip if already indexed
            if obs_id in self.reverse_map:
                return

            # Assign internal ID
            internal_id = self.next_id
            self.id_map[internal_id] = obs_id
            self.reverse_map[obs_id] = internal_id
            self.next_id += 1

            # Add to index
            try:
                emb_array = np.array([embedding], dtype=np.float32)
                self.index.add_items(emb_array, [internal_id])
            except Exception as e:
                logger.error(f"Error adding embedding to index: {e}")
                # Clean up on failure
                del self.id_map[internal_id]
                del self.reverse_map[obs_id]
                self.next_id -= 1

    def search(
        self, query_embedding: list[float], k: int = 10
    ) -> list[tuple[str, float]]:
        """Search index for similar embeddings.

        Args:
            query_embedding: Query embedding vector
            k: Number of results to return

        Returns:
            List of (observation_id, similarity_score) sorted by score
        """
        if not self.enabled or self.next_id == 0:
            return []

        try:
            query_array = np.array([query_embedding], dtype=np.float32)
            with self.lock:
                labels, distances = self.index.knn_query(
                    query_array, k=min(k, self.next_id)
                )

            # Convert distances to similarity scores
            # HNSW uses cosine distance, convert to similarity: 1 - distance
            results = []
            for internal_id, distance in zip(labels[0], distances[0]):
                obs_id = self.id_map.get(internal_id)
                if obs_id:
                    similarity = 1.0 - distance  # cosine distance → similarity
                    results.append((obs_id, max(0.0, similarity)))

            return results
        except Exception as e:
            logger.error(f"Error searching index: {e}")
            return []

    def remove_embedding(self, obs_id: str) -> None:
        """Remove observation from index.

        Note: HNSW doesn't support deletion, so we just clean up mappings.
        """
        if not self.enabled:
            return

        with self.lock:
            if obs_id in self.reverse_map:
                internal_id = self.reverse_map.pop(obs_id)
                self.id_map.pop(internal_id, None)

    def clear(self) -> None:
        """Clear all embeddings from index."""
        if not self.enabled:
            return

        with self.lock:
            self.id_map.clear()
            self.reverse_map.clear()
            self.next_id = 0
            self._init_index()

    def get_size(self) -> int:
        """Get number of embeddings in index."""
        return self.next_id

    def rebuild(self, embeddings: dict[str, list[float]]) -> None:
        """Rebuild index from embeddings.

        Args:
            embeddings: Dict of observation_id → embedding
        """
        if not self.enabled:
            return

        self.clear()
        for obs_id, embedding in embeddings.items():
            self.add_embedding(obs_id, embedding)
        logger.info(f"Rebuilt vector index with {len(embeddings)} embeddings")

    def save(self, path: str | Path) -> bool:
        """Persist index to disk.

        Args:
            path: Directory to save index files (creates if missing)

        Returns:
            True if successful, False otherwise
        """
        if not self.enabled or not self.index:
            return False

        try:
            path = Path(path)
            path.mkdir(parents=True, exist_ok=True)

            with self.lock:
                # Save HNSW index
                index_file = path / "hnsw.idx"
                self.index.save_index(str(index_file))

                # Save metadata and mappings
                metadata = {
                    "dimension": self.dimension,
                    "max_elements": self.max_elements,
                    "next_id": self.next_id,
                    "id_map": {str(k): v for k, v in self.id_map.items()},
                    "reverse_map": self.reverse_map,
                }
                meta_file = path / "metadata.json"
                meta_file.write_text(json.dumps(metadata))

                logger.info(f"Saved vector index to {path}")
                return True
        except Exception as e:
            logger.error(f"Error saving vector index: {e}")
            return False

    def load(self, path: str | Path) -> bool:
        """Load index from disk.

        Args:
            path: Directory containing index files

        Returns:
            True if successful, False otherwise
        """
        if not self.enabled:
            return False

        try:
            path = Path(path)
            if not path.exists():
                return False

            meta_file = path / "metadata.json"
            if not meta_file.exists():
                return False

            metadata = json.loads(meta_file.read_text())

            # Validate schema
            if metadata["dimension"] != self.dimension:
                logger.warning(
                    f"Schema mismatch: loaded dimension {metadata['dimension']} != "
                    f"current {self.dimension}. Rebuild required."
                )
                return False

            with self.lock:
                # Load HNSW index
                index_file = path / "hnsw.idx"
                self.index = hnswlib.Index(space="cosine", dim=self.dimension)
                self.index.load_index(str(index_file))
                self.index.set_ef(50)

                # Restore mappings
                self.id_map = {int(k): v for k, v in metadata["id_map"].items()}
                self.reverse_map = metadata["reverse_map"]
                self.next_id = metadata["next_id"]

                logger.info(
                    f"Loaded vector index from {path} ({self.next_id} embeddings)"
                )
                return True
        except Exception as e:
            logger.error(f"Error loading vector index: {e}")
            return False
