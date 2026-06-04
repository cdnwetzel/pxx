"""Simple LRU cache for search results (performance optimization)."""

import hashlib


class SearchCache:
    """Cache for search results with project isolation."""

    def __init__(self, maxsize: int = 128):
        self.maxsize = maxsize
        self._cache = {}

    def _key(
        self,
        project: str,
        query: str,
        limit: int = 10,
        min_score: float = 0.0,
    ) -> str:
        """Generate cache key from parameters."""
        params = f"{project}|{query}|{limit}|{min_score}"
        return hashlib.md5(params.encode()).hexdigest()

    def get(
        self,
        project: str,
        query: str,
        limit: int = 10,
        min_score: float = 0.0,
    ) -> dict | None:
        """Get cached search result."""
        key = self._key(project, query, limit, min_score)
        return self._cache.get(key)

    def set(
        self,
        project: str,
        query: str,
        result: dict,
        limit: int = 10,
        min_score: float = 0.0,
    ) -> None:
        """Set cached search result."""
        key = self._key(project, query, limit, min_score)

        # Simple size management: clear if cache too large
        if len(self._cache) >= self.maxsize:
            # Remove oldest entry (simple FIFO, not true LRU)
            self._cache.pop(next(iter(self._cache)))

        self._cache[key] = result

    def invalidate_project(self, project: str) -> None:
        """Invalidate all cache entries for a project."""
        keys_to_delete = [k for k in self._cache.keys() if k.startswith(project)]
        for k in keys_to_delete:
            self._cache.pop(k, None)

    def clear(self) -> None:
        """Clear all cached results."""
        self._cache.clear()
