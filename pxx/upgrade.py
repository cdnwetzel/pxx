"""Self-update: ``pxx upgrade``.

Detects the install method (uv tool / pipx / pip), checks PyPI for the
latest release, and runs the matching upgrade command. Editable/development
checkouts (a ``.git`` directory next to the package) are refused.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

from . import __version__

PYPI_URL = "https://pypi.org/pypi/pxx-orchestrator/json"
PACKAGE = "pxx-orchestrator"
HTTP_TIMEOUT = 5.0


@dataclass(frozen=True)
class UpgradeResult:
    status: str  # "updated" | "current" | "refused" | "error"
    message: str


def detect_install_method() -> str:
    """Return "editable" | "uv" | "pipx" | "pip"."""
    package_parent = Path(__file__).resolve().parent.parent
    if (package_parent / ".git").exists():
        return "editable"
    prefix = sys.prefix
    if "uv" in prefix and "tool" in prefix:
        return "uv"
    if "pipx" in prefix:
        return "pipx"
    return "pip"


def _version_tuple(version: str) -> tuple[int, ...]:
    parts = [int(x) for x in re.findall(r"\d+", version)[:3]]
    return tuple(parts + [0] * (3 - len(parts)))


def _is_newer(latest: str, current: str) -> bool:
    return _version_tuple(latest) > _version_tuple(current)


async def latest_version() -> str:
    """Fetch the latest released version from PyPI (5s timeout)."""
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        response = await client.get(PYPI_URL)
        response.raise_for_status()
        return str(response.json()["info"]["version"])


async def _run_command(argv: list[str]) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode(errors="replace")


async def _installed_version() -> str | None:
    """Version reported by the installed ``pxx`` in a FRESH process, or None.

    The in-process ``__version__`` is the module loaded at startup — after a
    tool-env replacement (uv/pipx) it describes the OLD install, so the only
    honest probe is executing the entry point again. Prefer the console
    script that launched THIS process (the installation the upgrade command
    targeted); a bare PATH lookup could hit a different, shadowing install.
    """
    argv0 = Path(sys.argv[0])
    exe = str(argv0) if argv0.stem == "pxx" else shutil.which("pxx")
    if exe is None:
        return None
    try:
        rc, out = await _run_command([exe, "--version"])
    except OSError:
        return None
    if rc != 0:
        return None
    match = re.search(r"\d+(?:\.\d+)+", out)
    return match.group(0) if match else None


def _upgrade_command(method: str) -> list[str]:
    if method == "uv":
        return ["uv", "tool", "upgrade", PACKAGE]
    if method == "pipx":
        return ["pipx", "upgrade", PACKAGE]
    return [sys.executable, "-m", "pip", "install", "-U", PACKAGE]


async def upgrade() -> UpgradeResult:
    """Check PyPI and self-upgrade when a newer release exists."""
    method = detect_install_method()
    if method == "editable":
        return UpgradeResult(
            "refused",
            "editable/development install detected (.git next to the package); "
            "refusing to self-upgrade — update with git instead.",
        )
    try:
        latest = await latest_version()
    except Exception as exc:
        return UpgradeResult("error", f"could not check PyPI for updates: {exc!r}")
    if not _is_newer(latest, __version__):
        return UpgradeResult("current", f"pxx {__version__} is up to date (latest: {latest}).")
    command = _upgrade_command(method)
    rc, output = await _run_command(command)
    if rc != 0:
        return UpgradeResult(
            "error",
            f"upgrade command failed ({' '.join(command)}):\n{output[-500:]}",
        )
    # rc==0 is not an upgrade: uv/pipx exit 0 on "nothing to upgrade" (e.g. a
    # package index that hasn't served the new release yet). Verify the
    # outcome before claiming it — observed live on the 2.1.6 rollout, where
    # rc==0 masked a no-op and the old claim reported a phantom upgrade.
    seen = await _installed_version()
    if seen == latest:
        return UpgradeResult("updated", f"upgraded pxx {__version__} -> {latest}.")
    if seen is None:
        return UpgradeResult(
            "updated",
            f"upgrade command succeeded ({' '.join(command)}); could not re-run "
            f"the installed pxx to confirm it now reports {latest}.",
        )
    return UpgradeResult(
        "error",
        f"upgrade command exited 0 but the installed pxx still reports {seen} "
        f"(expected {latest}) — the package index may not have the new release "
        f"yet; retry in a minute. Output tail:\n{output[-300:]}",
    )
