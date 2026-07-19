"""Internal git helpers shared across pxx modules.

These helpers centralize git CLI interactions to ensure consistent behavior
(timeouts, error handling) and to provide a stable internal API for
metadata collection and safety checks. Using thin wrappers here allows
modules like `safety` and `drift` to remain focused on logic rather than
parsing subprocess output.
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
        # returncode != 0 is the legitimate "not a git repo" case (nothing to
        # stash / no data-loss risk) -> not dirty. Do NOT fail closed here.
        return diff.returncode == 0 and bool(diff.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # Fail CLOSED (data-loss guard): git missing or timed out is UNKNOWN,
        # not clean -> treat as dirty so --edit never starts an unstashed
        # session on a real dirty tree.
        return True


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


def configured_remotes() -> set[str]:
    """Names of remotes configured in the local repo (no network)."""
    result = subprocess.run(
        ["git", "remote"],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    if result.returncode != 0:
        return set()
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def remote_head_sha(remote: str, ref: str = "main") -> str | None:
    """Return a remote ref's SHA-1 via `git ls-remote`, or None on any failure.

    Hits the network (one round-trip per call), so callers should treat a None
    return as "unreachable / unknown" rather than "diverged". The 5s timeout
    accommodates SSH handshakes to GitHub.
    """
    try:
        result = subprocess.run(
            ["git", "ls-remote", remote, ref],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        return result.stdout.split()[0] or None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
