"""Load the refresh source list and expand it to allowlisted URLs."""

from __future__ import annotations

import os
import tomllib
from importlib import resources
from pathlib import Path

from .allowlist import is_allowed


def load_sources() -> dict:
    override = os.environ.get("DOCS_SME_SOURCES")
    if override:
        return tomllib.loads(Path(override).read_text())
    return tomllib.loads(resources.files("docs_rag_sme").joinpath("sources.toml").read_text())


def build_urls(sources: dict) -> list[str]:
    """Expand the config into concrete URLs, dropping anything off-allowlist
    (defence in depth — the fetch layer enforces it again)."""
    version = sources.get("python_version", "3")
    urls: list[str] = []
    urls += [f"https://docs.python.org/{version}/{slug}" for slug in sources.get("stdlib", [])]
    urls += [f"https://pypi.org/pypi/{pkg}/json" for pkg in sources.get("pypi", [])]
    urls += [f"https://peps.python.org/pep-{int(n):04d}/" for n in sources.get("peps", [])]
    return [u for u in urls if is_allowed(u)]
