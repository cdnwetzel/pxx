"""Allowlist-gated fetch with content-hash delta detection.

Every fetch passes through `ensure_allowed` first — there is no code path that
reaches the network without it. Conditional requests (ETag / If-Modified-Since)
let a refresh run short-circuit unchanged pages cheaply.
"""

from __future__ import annotations

import hashlib

import httpx

from .allowlist import ensure_allowed
from .models import FetchResult, SeenIndex

_USER_AGENT = "docs-rag-sme/0.0.1 (+local ingestion; allowlist-only)"


def content_hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def fetch(url: str, client: httpx.Client, seen: SeenIndex | None = None) -> FetchResult:
    """Fetch one allowlisted URL. If `seen` has prior validators, send a
    conditional request and report `not_modified=True` on a 304."""
    ensure_allowed(url)

    headers = {"User-Agent": _USER_AGENT}
    if seen is not None:
        if etag := seen.etags.get(url):
            headers["If-None-Match"] = etag
        if lastmod := seen.last_modified.get(url):
            headers["If-Modified-Since"] = lastmod

    resp = client.get(url, headers=headers, follow_redirects=True)

    # Redirects are followed by httpx, but the final URL must still be allowed.
    ensure_allowed(str(resp.url))

    if resp.status_code == 304:
        return FetchResult(
            url=url,
            body="",
            content_hash=seen.hashes.get(url, "") if seen else "",
            last_modified=seen.last_modified.get(url) if seen else None,
            etag=seen.etags.get(url) if seen else None,
            not_modified=True,
        )
    resp.raise_for_status()
    body = resp.text
    return FetchResult(
        url=url,
        body=body,
        content_hash=content_hash(body),
        last_modified=resp.headers.get("Last-Modified"),
        etag=resp.headers.get("ETag"),
    )
