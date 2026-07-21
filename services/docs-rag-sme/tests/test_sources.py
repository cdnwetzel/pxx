"""T4: source config expansion. The shipped sources.toml must expand to only
allowlisted URLs, and the loader must round-trip the packaged file."""

from __future__ import annotations

from docs_rag_sme.ingest.allowlist import is_allowed
from docs_rag_sme.ingest.sources import build_urls, load_sources


def test_packaged_sources_load():
    src = load_sources()
    assert src["python_version"]
    assert src["stdlib"] and src["pypi"] and src["peps"]


def test_build_urls_shapes():
    src = {
        "python_version": "3.12",
        "stdlib": ["library/asyncio-task.html"],
        "pypi": ["httpx"],
        "peps": [8, 484],
    }
    urls = build_urls(src)
    assert "https://docs.python.org/3.12/library/asyncio-task.html" in urls
    assert "https://pypi.org/pypi/httpx/json" in urls
    assert "https://peps.python.org/pep-0008/" in urls
    assert "https://peps.python.org/pep-0484/" in urls


def test_every_packaged_url_is_allowlisted():
    urls = build_urls(load_sources())
    assert urls
    assert all(is_allowed(u) for u in urls)


def test_offlist_entries_are_dropped():
    src = {"python_version": "3.12", "pypi": ["httpx"], "stdlib": [], "peps": []}
    # A malicious-looking package name can't escape pypi.org/pypi/<pkg>/json shape.
    src["pypi"].append("../../../etc/passwd")
    urls = build_urls(src)
    assert all("etc/passwd" not in u for u in urls)
    assert "https://pypi.org/pypi/httpx/json" in urls
