"""`pxx --upgrade` — self-update the installed pxx-orchestrator distribution.

This lives at the pxx CLI layer, never as an aider slash command: pxx
``os.execv``s into aider, so an in-session command could not replace the running
pxx. The verb detects how pxx was installed, reports current → latest, and runs
the right upgrade for that method — refusing outright on an editable/source
checkout, where the correct move is ``git pull`` and never a pip overwrite.
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
from enum import Enum
from importlib import metadata

from packaging.version import InvalidVersion, Version

from pxx import __version__

DIST_NAME = "pxx-orchestrator"
_PYPI_JSON = "https://pypi.org/pypi/{dist}/json"


class InstallMethod(str, Enum):
    UV_TOOL = "uv-tool"
    PIPX = "pipx"
    PIP = "pip"
    EDITABLE = "editable"


def detect_install_method(location: str, editable: bool) -> InstallMethod:
    """Classify the install from its on-disk location + editable flag. Pure so
    the branching is unit-tested without touching a real environment."""
    if editable:
        return InstallMethod.EDITABLE
    loc = location.replace("\\", "/")
    if "/uv/tools/" in loc:
        return InstallMethod.UV_TOOL
    if "/pipx/" in loc:
        return InstallMethod.PIPX
    return InstallMethod.PIP


def upgrade_command(method: InstallMethod, dist: str = DIST_NAME) -> list[str] | None:
    """The upgrade command for a method, or None when self-upgrade is refused
    (editable). PIP uses the running interpreter so it targets THIS env."""
    match method:
        case InstallMethod.UV_TOOL:
            return ["uv", "tool", "upgrade", dist]
        case InstallMethod.PIPX:
            return ["pipx", "upgrade", dist]
        case InstallMethod.PIP:
            return [sys.executable, "-m", "pip", "install", "-U", dist]
        case InstallMethod.EDITABLE:
            return None


def needs_upgrade(current: str, latest: str) -> bool:
    """True when `latest` is a strictly newer release than `current`. Invalid
    version strings are treated as "don't offer an upgrade" (fail safe)."""
    try:
        return Version(latest) > Version(current)
    except InvalidVersion:
        return False


def _is_editable(dist: metadata.Distribution) -> bool:
    raw = dist.read_text("direct_url.json")
    if not raw:
        return False
    info = json.loads(raw)
    return bool(info.get("dir_info", {}).get("editable"))


def latest_version(dist: str = DIST_NAME, timeout: float = 5.0) -> str | None:
    """Newest version on PyPI, or None if PyPI can't be reached (offline-safe —
    pxx is offline-capable, so `--upgrade` degrades instead of hanging)."""
    url = _PYPI_JSON.format(dist=dist)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 (https literal)
            data = json.load(resp)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None
    version = data.get("info", {}).get("version")
    return version if isinstance(version, str) else None


def _resolve() -> tuple[InstallMethod, str]:
    dist = metadata.distribution(DIST_NAME)
    location = str(dist.locate_file(""))
    return detect_install_method(location, _is_editable(dist)), location


def upgrade_main() -> int:
    current = __version__
    try:
        method, location = _resolve()
    except metadata.PackageNotFoundError:
        print(
            f"pxx: {DIST_NAME} is not installed as a distribution — nothing to "
            "self-upgrade.",
            file=sys.stderr,
        )
        return 1

    if method is InstallMethod.EDITABLE:
        print(
            f"pxx: editable/source checkout at {location}\n"
            "     upgrade it in place:  git pull && uv sync --extra dev\n"
            "     (refusing to pip-upgrade over an editable install).",
            file=sys.stderr,
        )
        return 1

    print(f"pxx {current} — checking PyPI for {DIST_NAME} ({method.value} install)…")
    latest = latest_version()
    if latest is None:
        print(
            "pxx: could not reach PyPI (offline?) — try again later.", file=sys.stderr
        )
        return 2
    if not needs_upgrade(current, latest):
        print(f"pxx: already up to date (installed {current}, latest {latest}).")
        return 0

    cmd = upgrade_command(method)
    assert cmd is not None  # EDITABLE returned above; every other method has one
    print(f"pxx: {current} → {latest}   via   {' '.join(cmd)}")
    try:
        return subprocess.run(cmd, check=False).returncode
    except FileNotFoundError:
        # The install-method tool (uv/pipx) isn't on PATH — degrade to a
        # one-line instruction instead of dumping a traceback (the exact UX
        # 1.3.1 set out to remove).
        print(
            f"pxx: upgrade tool {cmd[0]!r} is not on PATH — "
            f"run `{' '.join(cmd)}` yourself.",
            file=sys.stderr,
        )
        return 2
