"""Source pages — the single hashing authority.

A page's ``sha-256`` is over the **raw file bytes** (no normalization, no newline munging),
addressed by the **canonicalized, symlink-resolved** path. The SAME rule is used everywhere it
matters — the patcher's ``expected_sha`` check, the reindex after a write, and the restart
reconciliation — so a hash means one thing across the whole system.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


def canonical_path(root: Path, path: str | Path) -> Path:
    """Canonicalize + resolve symlinks, then require the result stays under ``root``.

    Fail-closed against path escape (``../`` or a symlink pointing outside the repo): a page
    request that resolves outside the sandbox root is refused, not silently served.
    """
    root_resolved = Path(root).resolve()
    candidate = (
        (root_resolved / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
    )
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise ValueError(f"path escapes sandbox root: {path}")
    return candidate


def page_hash(data: bytes) -> str:
    """sha-256 over raw bytes. The one authority — no normalization."""
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Page:
    """A source page: the resolved path, its text, and the hash of its raw bytes."""

    path: str  # the caller-facing (root-relative) path, for the capsule/ledger
    text: str
    sha: str


class PageStore:
    """Reads repo files as hashed pages. The hash is always recomputed from disk — the store
    holds no cached authority, so a page fault always reflects current bytes."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()

    def exists(self, path: str) -> bool:
        try:
            return canonical_path(self.root, path).is_file()
        except ValueError:
            return False

    def read(self, path: str) -> Page:
        """Page-fault a file in: returns its text + the hash of its raw bytes."""
        resolved = canonical_path(self.root, path)
        raw = resolved.read_bytes()
        return Page(path=path, text=raw.decode("utf-8", errors="replace"), sha=page_hash(raw))

    def current_hash(self, path: str) -> str | None:
        """The current on-disk hash, or None if the file is absent."""
        try:
            resolved = canonical_path(self.root, path)
            return page_hash(resolved.read_bytes()) if resolved.is_file() else None
        except (ValueError, OSError):
            return None

    def write(self, path: str, text: str) -> str:
        """Atomically replace a file's bytes (tmp-then-replace) and return the new hash.

        Atomic per file: a crash never exposes a half-written source page. The returned hash
        is what a reindex would compute — the caller records it as the page's new revision.
        """
        resolved = canonical_path(self.root, path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        data = text.encode("utf-8")
        tmp = resolved.with_name(f".{resolved.name}.tmp-{page_hash(data)[:8]}")
        tmp.write_bytes(data)
        tmp.replace(resolved)  # atomic on POSIX
        return page_hash(data)
