"""Internal git helpers shared across pxx modules.

These are thin wrappers around git CLI calls, primarily used for safety
checks and metadata collection.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def is_in_repo() -> bool:
    """True if cwd is inside a git work tree."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            check=False,
            timeout=2,
        )
        return result.returncode == 0 and result.stdout.strip() == b"true"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def is_dirty() -> bool:
    """True if cwd's git work tree has uncommitted or untracked changes."""
    try:
        # Tracked-but-uncommitted changes (staged or unstaged).
        diff = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
        return diff.returncode == 0 and bool(diff.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def has_commits() -> bool:
    """True iff the current git repo has at least one commit (HEAD resolved)."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            check=False,
            timeout=2,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def repo_root() -> Path | None:
    """Return the absolute Path of the current git repo's top-level, or None."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
        if result.returncode != 0:
            return None
        return Path(result.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def head_sha() -> str | None:
    """Return HEAD's full SHA-1, or None when not in a git repo (or unborn HEAD)."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
