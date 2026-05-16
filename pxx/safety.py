"""Safety foundation for pxx (#002).

Includes pre-session sanity checks and local git safety tags to protect
against broken self-edits.
"""

from __future__ import annotations

import importlib
import sys
import time
import subprocess
from pathlib import Path

from pxx import _git

SAFETY_TAG_PREFIX = "pxx-pre/"
SAFETY_TAG_RETENTION_DAYS = 30


def sanity_check(repo_root: Path, module_name: str = "pxx.endpoints") -> None:
    """Refuse to launch if a critical pxx module fails to import.

    Protects against self-modification (Tier 3 of #001) leaving pxx in a
    broken state. Exits with status 2 on failure.
    """
    try:
        importlib.import_module(module_name)
    except Exception as e:
        print(
            f"pxx: own module `{module_name}` failed to import: {e}\n"
            f"  pxx may have been broken by a self-edit.\n"
            f"  Recover with one of:\n"
            f"    git -C {repo_root} reflog\n"
            f"    git -C {repo_root} reset --hard <last-known-good>\n"
            f"    git -C {repo_root} reset --hard pxx-pre/<unix-ts>",
            file=sys.stderr,
        )
        sys.exit(2)


def create_tag() -> str | None:
    """Create a local-only safety tag at HEAD; stash dirty state.

    Returns the tag name on success, ``None`` if not in a git repo or git
    operations fail.
    """
    if not _git.is_in_repo():
        return None

    ts = int(time.time())
    tag = f"{SAFETY_TAG_PREFIX}{ts}"

    try:
        # Stash any uncommitted changes first so the tag points at a clean
        # HEAD. The stash itself is recoverable via `git stash list`.
        if _git.is_dirty():
            subprocess.run(
                [
                    "git",
                    "stash",
                    "push",
                    "--include-untracked",
                    "--message",
                    f"{tag}: working state at session start",
                ],
                capture_output=True,
                check=False,
                timeout=10,
            )

        # Create the tag at current HEAD.
        result = subprocess.run(
            ["git", "tag", tag],
            capture_output=True,
            check=False,
            timeout=2,
        )
        if result.returncode != 0:
            return None
        return tag
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def prune_old_tags(retention_days: int = SAFETY_TAG_RETENTION_DAYS) -> None:
    """Delete `pxx-pre/<ts>` tags older than `retention_days`."""
    if not _git.is_in_repo():
        return

    cutoff = int(time.time()) - (retention_days * 86400)

    try:
        result = subprocess.run(
            ["git", "tag", "--list", f"{SAFETY_TAG_PREFIX}*"],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
        if result.returncode != 0:
            return
        for tag in result.stdout.strip().splitlines():
            suffix = tag.removeprefix(SAFETY_TAG_PREFIX)
            try:
                ts = int(suffix)
            except ValueError:
                continue
            if ts < cutoff:
                subprocess.run(
                    ["git", "tag", "-d", tag],
                    capture_output=True,
                    check=False,
                    timeout=2,
                )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return
