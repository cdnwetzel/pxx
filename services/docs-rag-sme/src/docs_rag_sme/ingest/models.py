"""Data shapes for ingested doc chunks. Metadata is the whole point — every
chunk carries enough provenance for version-aware retrieval (T3)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class DocChunk:
    source_url: str
    title: str  # e.g. "asyncio.TaskGroup" or section heading
    text: str
    # Provenance / version metadata (the differentiator for T3 filtering).
    python_version: str | None = None
    package: str | None = None
    package_version: str | None = None
    last_modified: str | None = None
    anchor: str | None = None
    # content_hash is the hash of the *source page*, set by the fetch layer so
    # all chunks from one page share it and delta-refresh can skip unchanged pages.
    content_hash: str | None = None

    @property
    def chunk_id(self) -> str:
        """Stable id from the bytes that define this chunk's identity."""
        basis = f"{self.source_url}\x00{self.anchor or ''}\x00{self.title}"
        return hashlib.sha256(basis.encode()).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class FetchResult:
    url: str
    body: str
    content_hash: str
    last_modified: str | None = None
    etag: str | None = None
    # True when a conditional request short-circuited (page unchanged → skip).
    not_modified: bool = False


@dataclass
class SeenIndex:
    """Per-URL content hash + validators, persisted between ingest runs so a
    refresh reprocesses only changed pages."""

    hashes: dict[str, str] = field(default_factory=dict)
    etags: dict[str, str] = field(default_factory=dict)
    last_modified: dict[str, str] = field(default_factory=dict)

    def is_changed(self, url: str, content_hash: str) -> bool:
        return self.hashes.get(url) != content_hash

    def record(self, result: FetchResult) -> None:
        self.hashes[result.url] = result.content_hash
        if result.etag:
            self.etags[result.url] = result.etag
        if result.last_modified:
            self.last_modified[result.url] = result.last_modified
