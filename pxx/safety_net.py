"""K5 (Option A): the safety net — stash + ``pxx-pre/<ts>`` tag at session start.

Fires on edit-capable session starts (edit/run/loop/auto) inside a git repo;
a no-op anywhere else. A dirty tree is stashed (``--include-untracked``,
message carrying the run id); HEAD is always tagged ``pxx-pre/<ts>``
(``-2``, ``-3``… on collision). Pop is the user's move, never pxx's (1.x
semantics).

The net is insurance, not a gate: any git failure degrades to a partial or
absent net, honestly reported — it never crashes a session (hard rule 1).
"""

from __future__ import annotations

import asyncio
import logging
import time
from asyncio.subprocess import PIPE
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("pxx.safety_net")


@dataclass(frozen=True)
class SafetyNet:
    """What was tied at session start. Either field may be None on partial
    failure — report exactly what exists, never claim more."""

    tag: str | None  # pxx-pre/<ts>(-N), None when tagging failed
    stash_message: str | None  # None when the tree was clean (tag only)


async def _git(cwd: Path, *args: str) -> str | None:
    """Run a git command; return stdout or None when unavailable/failed."""
    try:
        proc = await asyncio.create_subprocess_exec("git", *args, cwd=cwd, stdout=PIPE, stderr=PIPE)
        out, _ = await proc.communicate()
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return out.decode(errors="replace").strip()


async def _unique_tag(cwd: Path) -> str:
    """``pxx-pre/<utc-ts>`` — suffixed ``-2``, ``-3``… until free."""
    base = f"pxx-pre/{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    tag = base
    n = 2
    while await _git(cwd, "show-ref", "--verify", "--quiet", f"refs/tags/{tag}") is not None:
        tag = f"{base}-{n}"
        n += 1
    return tag


async def tie_safety_net(cwd: Path, run_id: str) -> SafetyNet | None:
    """Stash (dirty only) + tag HEAD. Returns the net, or None when nothing
    could be tied (not a git repo, no commits, or git failing outright)."""
    head = await _git(cwd, "rev-parse", "--verify", "HEAD")
    if not head:
        return None  # not a git repo (or nothing to tag)

    stash_message = None
    status = await _git(cwd, "status", "--porcelain=v1", "--untracked-files=all")
    if status:
        stash_message = f"pxx safety net {run_id}"
        out = await _git(cwd, "stash", "push", "--include-untracked", "-m", stash_message)
        if out is None:
            log.warning("safety-net stash failed; continuing without it")
            stash_message = None

    tag = await _unique_tag(cwd)
    if await _git(cwd, "tag", tag, "HEAD") is None:
        log.warning("safety-net tag %s failed", tag)
        tag = None

    if tag is None and stash_message is None:
        return None
    return SafetyNet(tag=tag, stash_message=stash_message)


async def commit_session_work(
    cwd: Path,
    *,
    task_preview: str,
    net_tag: str | None,
    only: set[str] | None = None,
) -> str | None:
    """Commit the session's work after a COMPLETED outcome; return the sha.

    Opt-in only (``settings.auto_commit`` / ``--commit``). Fail-soft by
    contract: nothing-to-commit or any git failure returns None — the
    session is never crashed by a commit that can't happen (hard rule 1).
    The ``pxx-pre/<ts>`` tag already points at pre-session HEAD, so the undo
    story is unchanged: ``git reset --hard <tag>``.

    ``only`` (when given) restricts the commit to exactly those repo-relative
    paths — the session's own work — so pre-existing dirt (.env, WIP notes)
    is never swept in when the tree wasn't stashed first (``safety_net=false``
    or the stash fail-soft path). ``only=None`` stages everything (direct
    callers/tests only).
    """
    if only is not None and not only:
        return None  # the session changed nothing
    if only is not None:
        staged_any = False
        for rel in sorted(only):
            if await _git(cwd, "add", "--", rel) is not None:
                staged_any = True
        if not staged_any:
            log.warning("auto-commit: nothing stageable in the session's delta")
            return None
    else:
        status = await _git(cwd, "status", "--porcelain=v1", "--untracked-files=all")
        if not status:
            return None  # nothing to commit
        if await _git(cwd, "add", "-A") is None:
            log.warning("auto-commit: git add failed; leaving work uncommitted")
            return None
    preview = " ".join(task_preview.split())[:72] or "session work"
    message = f"pxx: {preview}" + (f" [net: {net_tag}]" if net_tag else "")
    # CI runners (and some sandboxes) have no git identity configured; use
    # the repo's when present, else an explicit pxx fallback — a commit must
    # never fail for want of an identity.
    identity: list[str] = []
    if await _git(cwd, "config", "user.name") is None:
        identity = ["-c", "user.name=pxx[bot]"]
    if await _git(cwd, "config", "user.email") is None:
        identity += ["-c", "user.email=pxx[bot]@localhost"]
    if await _git(cwd, *identity, "commit", "-q", "-m", message) is None:
        log.warning("auto-commit: git commit failed; leaving work uncommitted")
        return None
    return await _git(cwd, "rev-parse", "HEAD")


__all__ = ["SafetyNet", "commit_session_work", "tie_safety_net"]
