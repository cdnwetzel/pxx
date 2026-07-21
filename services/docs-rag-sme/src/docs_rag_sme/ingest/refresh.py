"""`docs-sme-refresh` — the perpetual part (T4). Crawl every configured source,
ingest with delta-skip (unchanged pages cost nothing), emit a JSON-Lines summary.
Designed to be driven by a launchd/systemd timer.
"""

from __future__ import annotations

import json
import sys

import httpx

from .. import store as store_mod
from ..embed import Embedder
from .pipeline import ingest_url
from .sources import build_urls, load_sources


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    force = "--force" in argv

    urls = build_urls(load_sources())
    conn = store_mod.connect()
    store_mod.init_schema(conn)
    embedder = Embedder()
    ingested = skipped = failed = chunks = 0

    with httpx.Client(timeout=60.0) as http:
        for url in urls:
            try:
                r = ingest_url(url, conn, embedder, http, force=force)
            except Exception as exc:  # noqa: BLE001 - one bad page must not abort the run
                failed += 1
                print(json.dumps({"url": url, "status": "error", "detail": str(exc)[:200]}))
                continue
            if r.skipped:
                skipped += 1
            else:
                ingested += 1
                chunks += r.n_chunks
            print(json.dumps({"url": url, "status": r.reason, "chunks": r.n_chunks}))

    embedder.close()
    total = store_mod.count(conn)
    conn.close()
    print(json.dumps({
        "summary": True, "urls": len(urls), "ingested": ingested,
        "skipped": skipped, "failed": failed, "new_chunks": chunks, "total_chunks": total,
    }))
    return 1 if failed and not ingested else 0


if __name__ == "__main__":
    raise SystemExit(main())
