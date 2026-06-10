"""Allowlist — the network boundary for the whole project, enforced in code.

Nothing outside these hosts (and, for PyPI, this exact path shape) can ever be
fetched. This is deliberately not configurable: the GOAL is a system that lives
off the local LLM at runtime and only ever reaches *official* doc sources at
ingest time. A typo in a config file must not be able to widen that boundary.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

ALLOWED_HOSTS: frozenset[str] = frozenset(
    {
        "docs.python.org",
        "peps.python.org",
        "pypi.org",
    }
)

# PyPI is allowed only via its JSON API, never arbitrary project pages.
_PYPI_JSON_PATH = re.compile(r"^/pypi/[A-Za-z0-9._-]+/(?:[\w.+!-]+/)?json$")
# Capture the X.Y (or bare X) version segment from a docs.python.org URL.
_PYDOC_VERSION = re.compile(r"^/(\d+(?:\.\d+)?)(?:/|$)")


class DisallowedURL(ValueError):
    """Raised when a URL is not on the allowlist. Never caught to 'retry'."""


def is_allowed(url: str) -> bool:
    try:
        parts = urlparse(url)
    except ValueError:
        return False
    if parts.scheme != "https" or parts.hostname is None:
        return False
    host = parts.hostname.lower()
    if host not in ALLOWED_HOSTS:
        return False
    if host == "pypi.org":
        return bool(_PYPI_JSON_PATH.match(parts.path))
    return True


def ensure_allowed(url: str) -> None:
    if not is_allowed(url):
        raise DisallowedURL(f"refusing to fetch off-allowlist URL: {url!r}")


def python_version_of(url: str) -> str | None:
    """Extract the doc version from a docs.python.org URL ('3.12', '3', ...).

    Returns None for non-versioned hosts (peps, pypi). 'stable'/'3'/'dev' are
    left as-is; normalisation to a concrete X.Y is a T3 concern.
    """
    parts = urlparse(url)
    if (parts.hostname or "").lower() != "docs.python.org":
        return None
    m = _PYDOC_VERSION.match(parts.path)
    if m:
        return m.group(1)
    # docs.python.org/3/... canonical alias, or /stable/, /dev/
    seg = parts.path.lstrip("/").split("/", 1)[0]
    return seg or None


def package_of(url: str) -> str | None:
    """Extract the package name from a pypi.org JSON-API URL."""
    parts = urlparse(url)
    if (parts.hostname or "").lower() != "pypi.org":
        return None
    segs = parts.path.strip("/").split("/")
    return segs[1] if len(segs) >= 2 and segs[0] == "pypi" else None
