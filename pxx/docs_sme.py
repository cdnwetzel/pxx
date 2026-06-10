"""`--with-docs` integration (#009): route aider through the docs-rag-sme proxy
and tell it the project's Python version for version-aware retrieval.

stdlib-only (urllib/tomllib) — the SME is a separate service pxx merely points
at; pxx takes on no new third-party deps for it.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_SME_URL = "http://127.0.0.1:8004"
_XY = re.compile(r"(\d+)\.(\d+)")


def sme_base_url() -> str:
    return os.environ.get("PXX_DOCS_SME_URL", DEFAULT_SME_URL).rstrip("/")


def resolve_python_version(project_dir: Path) -> str | None:
    """Resolve the project's Python minor version (e.g. '3.12') for doc
    filtering. Precedence: .python-version → pyproject requires-python →
    the running interpreter."""
    pv = project_dir / ".python-version"
    if pv.is_file():
        if m := _XY.search(pv.read_text()):
            return f"{m.group(1)}.{m.group(2)}"

    pp = project_dir / "pyproject.toml"
    if pp.is_file():
        try:
            data = tomllib.loads(pp.read_text())
        except tomllib.TOMLDecodeError:
            data = {}
        requires = data.get("project", {}).get("requires-python")
        if requires and (m := _XY.search(requires)):
            return f"{m.group(1)}.{m.group(2)}"

    return f"{sys.version_info.major}.{sys.version_info.minor}"


def probe_sme(base: str, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(f"{base}/healthz", timeout=timeout) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


def notify_version(base: str, version: str | None, timeout: float = 2.0) -> bool:
    """Tell the SME the current session's Python version. Best-effort."""
    data = json.dumps({"python_version": version}).encode()
    req = urllib.request.Request(
        f"{base}/control/context",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False
