"""`docs-sme-ingest <url>...` — fetch allowlisted pages and either dry-run the
chunks (default) or embed + store them into Postgres/pgvector (`--store`)."""

from __future__ import annotations

import argparse
import sys

import httpx

from .allowlist import DisallowedURL, ensure_allowed
from .chunk import chunk
from .fetch import fetch


def _dry_run(url: str, max_chunks: int) -> int:
    with httpx.Client(timeout=30.0) as client:
        result = fetch(url, client)
    chunks = chunk(url, result.body, content_hash=result.content_hash)
    print(f"{url}\n  hash={result.content_hash[:12]}  chunks={len(chunks)}\n")
    for c in chunks[:max_chunks]:
        ver = c.python_version or c.package_version or "-"
        print(f"  [{c.chunk_id}] ({ver}) {c.title}")
        print(f"      {c.text.replace(chr(10), ' ')[:120]}\n")
    if len(chunks) > max_chunks:
        print(f"  ... {len(chunks) - max_chunks} more")
    return 0


def _store_run(urls: list[str], force: bool) -> int:
    # Imported lazily so the dry-run path needs no DB/embedding deps.
    from .. import store as store_mod
    from ..embed import Embedder
    from .pipeline import ingest_url

    conn = store_mod.connect()
    store_mod.init_schema(conn)
    embedder = Embedder()
    with httpx.Client(timeout=60.0) as http:
        for url in urls:
            r = ingest_url(url, conn, embedder, http, force=force)
            flag = "skip" if r.skipped else "ok"
            print(f"  [{flag}] {r.reason:9} {r.n_chunks:>4} chunks  {url}")
    print(f"\n  total chunks in store: {store_mod.count(conn)}")
    embedder.close()
    conn.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="docs-sme-ingest", description=__doc__)
    parser.add_argument("url", nargs="+", help="allowlisted URL(s)")
    parser.add_argument("--store", action="store_true", help="embed + store (else dry-run)")
    parser.add_argument("--force", action="store_true", help="re-ingest even if unchanged")
    parser.add_argument("--max", type=int, default=5, help="dry-run: max chunks to print")
    args = parser.parse_args(argv)

    try:
        for url in args.url:
            ensure_allowed(url)
    except DisallowedURL as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.store:
        return _store_run(args.url, args.force)
    return _dry_run(args.url[0], args.max)


if __name__ == "__main__":
    raise SystemExit(main())
