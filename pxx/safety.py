"""Safety foundation for pxx (#002).

This module prevents pxx from launching in a broken state or without a
recoverable path. Pre-session sanity checks catch import failures (e.g.
after a bad self-edit), while local-only safety tags ensure the user can
always `git reset --hard` to the state before a pxx session began. This is
the primary safety net for autonomous dogfooding.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
import time
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


def _has_unmerged_autonomous_commits() -> bool:
    """Check if there are unpushed [autonomous] commits on the current branch.

    Returns True if the working tree has autonomous commits not yet on origin,
    False otherwise (or on error). Used to detect concurrent multi-agent sessions
    (#CF-017). If another agent's autonomous session is in flight, skip stashing
    to avoid wiping out their work.
    """
    try:
        # Commits ahead of the CURRENT branch's upstream that contain [autonomous].
        # Derive the upstream (@{upstream}) instead of hardcoding origin/main, so
        # this works on release/feature branches too. No upstream -> non-zero rc
        # -> falls through to the safe default (return False).
        result = subprocess.run(
            ["git", "log", "--oneline", "@{upstream}..HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
        if result.returncode == 0:
            return "[autonomous]" in result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return False


def create_tag() -> str | None:
    """Create a local-only safety tag at HEAD; stash dirty state.

    Returns the tag name on success, ``None`` if not in a git repo or git
    operations fail. Skips stashing if concurrent autonomous sessions are
    in flight (PYTEST_CURRENT_TEST set, or unmerged [autonomous] commits).
    """
    if not _git.is_in_repo():
        return None

    # Skip safety tag stashing during pytest to prevent test runs from stashing
    # developer work (#CF-018). Tests can monkeypatch this function if needed.
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return None

    # Skip stashing if another agent's autonomous session is in flight (#CF-017).
    # Unmerged [autonomous] commits indicate an incomplete session; don't wipe
    # out that work with a concurrent stash.
    if _has_unmerged_autonomous_commits():
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
            # Tag creation failed (e.g. a same-second collision on the ts-based
            # name). We may have JUST stashed above — never swallow that silently
            # or the user loses their working state with no pointer to recover it.
            print(
                f"pxx: safety tag {tag!r} could not be created "
                f"({result.stderr.decode(errors='replace').strip() or 'git tag failed'}).\n"
                "      If a stash was taken this session, recover it with:\n"
                "        git stash list   # find the 'pxx-pre/<ts>' entry\n"
                "        git stash pop",
                file=sys.stderr,
            )
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
