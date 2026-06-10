"""Structural chunking. The invariant from the plan: a function/class entry's
signature is never split from its description.

For docs.python.org (Sphinx HTML), the unit is the `<dl class="py ...">`
definition block: its `<dt>` (signature, with an id anchor) stays welded to its
`<dd>` (description) in one chunk. PyPI JSON is chunked from its structured
fields. Both yield `DocChunk`s carrying version provenance.
"""

from __future__ import annotations

import json
from dataclasses import replace

from lxml import html as lxml_html

from .allowlist import package_of, python_version_of
from .models import DocChunk

# Keep embed inputs comfortably under nomic-embed-text's ~2k-token window.
MAX_CHARS = 6000
OVERLAP = 200


def _text(node) -> str:
    return " ".join(node.text_content().split())


def _split_long(chunk: DocChunk) -> list[DocChunk]:
    """Split an over-long chunk into overlapping windows, repeating the header
    (signature/title) on each so the identifier stays searchable in every part.
    Distinct anchors keep chunk_ids unique. Normal-size chunks pass through."""
    if len(chunk.text) <= MAX_CHARS:
        return [chunk]
    head, sep, body = chunk.text.partition("\n\n")
    if not sep:
        head, body = "", chunk.text
    budget = MAX_CHARS - (len(head) + 2 if head else 0)
    step = max(budget - OVERLAP, 1)
    parts: list[DocChunk] = []
    start = i = 0
    base_anchor = chunk.anchor or "chunk"
    while start < len(body):
        window = body[start : start + budget]
        text = f"{head}\n\n{window}" if head else window
        parts.append(replace(chunk, text=text, anchor=f"{base_anchor}#part{i}"))
        start += step
        i += 1
    return parts


def _finalize(chunks: list[DocChunk]) -> list[DocChunk]:
    out: list[DocChunk] = []
    for c in chunks:
        out.extend(_split_long(c))
    return out


def chunk_sphinx(url: str, body: str, *, content_hash: str | None = None) -> list[DocChunk]:
    """Chunk a Sphinx HTML page (docs.python.org) into API + prose chunks."""
    pyver = python_version_of(url)
    tree = lxml_html.fromstring(body)
    chunks: list[DocChunk] = []

    # 1) API entries: each <dl class="py ..."> = one welded signature+body chunk.
    for dl in tree.xpath('//dl[contains(@class, "py")]'):
        dts = dl.xpath("./dt")
        dds = dl.xpath("./dd")
        if not dts:
            continue
        anchor = dts[0].get("id")
        signature = " ".join(_text(dt) for dt in dts)
        body_text = " ".join(_text(dd) for dd in dds)
        title = anchor or signature[:80]
        text = signature if not body_text else f"{signature}\n\n{body_text}"
        chunks.append(
            DocChunk(
                source_url=url,
                title=title,
                text=text,
                python_version=pyver,
                anchor=anchor,
                content_hash=content_hash,
            )
        )

    # 2) Prose: top-level <section> headings with their immediate paragraphs.
    for section in tree.xpath("//section[@id]"):
        heading = section.xpath("./h1|./h2|./h3")
        paras = section.xpath("./p")
        if not heading or not paras:
            continue
        title = _text(heading[0])
        prose = " ".join(_text(p) for p in paras)
        if not prose:
            continue
        chunks.append(
            DocChunk(
                source_url=url,
                title=title,
                text=f"{title}\n\n{prose}",
                python_version=pyver,
                anchor=section.get("id"),
                content_hash=content_hash,
            )
        )

    return _finalize(chunks)


def chunk_pypi(url: str, body: str, *, content_hash: str | None = None) -> list[DocChunk]:
    """Chunk a pypi.org JSON-API response into a package-summary chunk."""
    data = json.loads(body)
    info = data.get("info", {})
    package = package_of(url) or info.get("name")
    version = info.get("version")
    summary = info.get("summary") or ""
    requires_python = info.get("requires_python") or ""
    fields = [f"{package} {version}", summary]
    if requires_python:
        fields.append(f"Requires-Python: {requires_python}")
    home = (info.get("project_urls") or {}).get("Homepage") or info.get("home_page")
    if home:
        fields.append(f"Homepage: {home}")
    text = "\n".join(f for f in fields if f)
    return [
        DocChunk(
            source_url=url,
            title=f"{package} {version}".strip(),
            text=text,
            package=package,
            package_version=version,
            content_hash=content_hash,
        )
    ]


def chunk(url: str, body: str, *, content_hash: str | None = None) -> list[DocChunk]:
    """Dispatch to the right chunker by source host."""
    if "pypi.org" in url:
        return chunk_pypi(url, body, content_hash=content_hash)
    return chunk_sphinx(url, body, content_hash=content_hash)
