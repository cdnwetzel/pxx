"""`docs-sme-ingest <url>` — T1a dry run: fetch one allowlisted page and print
the structural chunks it produces. No embedding, no store yet (T1b)."""

from __future__ import annotations

import argparse
import sys

import httpx

from .allowlist import DisallowedURL, ensure_allowed
from .chunk import chunk
from .fetch import fetch


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="docs-sme-ingest", description=__doc__)
    parser.add_argument("url", help="allowlisted URL (docs.python.org / peps / pypi json)")
    parser.add_argument("--max", type=int, default=5, help="max chunks to print")
    args = parser.parse_args(argv)

    try:
        ensure_allowed(args.url)
    except DisallowedURL as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    with httpx.Client(timeout=30.0) as client:
        result = fetch(args.url, client)

    chunks = chunk(args.url, result.body, content_hash=result.content_hash)
    print(f"{args.url}\n  hash={result.content_hash[:12]}  chunks={len(chunks)}\n")
    for c in chunks[: args.max]:
        ver = c.python_version or c.package_version or "-"
        print(f"  [{c.chunk_id}] ({ver}) {c.title}")
        snippet = c.text.replace("\n", " ")[:120]
        print(f"      {snippet}\n")
    if len(chunks) > args.max:
        print(f"  ... {len(chunks) - args.max} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
