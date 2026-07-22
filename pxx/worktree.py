"""Shared worktree snapshot/delta helpers.

"What did this run actually change?" has exactly one implementation — used
by both Session (K8 mutation accounting) and run_loop (end-of-loop commit
staging). The snapshot tracks two channels: tracked-vs-HEAD diff volume
(``--numstat``) and untracked-file content fingerprints. The delta between
two snapshots excludes pre-existing dirt — and still INCLUDES a file that
was dirty at baseline and edited further (the trap case; path-set
subtraction would silently drop it).
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path


def _sha256_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return None


async def _git(cwd: Path, *args: str) -> str | None:
    """Run a git command; return stripped stdout or None on any failure."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(cwd),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), 15.0)
    except (OSError, TimeoutError):
        return None
    if proc.returncode != 0:
        return None
    return out.decode(errors="replace").strip()


async def _git_status_entries(cwd: Path) -> list[tuple[str, str]] | None:
    """(2-char status, path) entries from ``git status -z``.

    The ``-z`` flag disables git's path quoting ENTIRELY: every path comes
    back byte-exact — spaces, non-ASCII, embedded quotes. This is the one
    place status output is parsed; never strip quotes by hand.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(cwd),
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), 15.0)
    except (OSError, TimeoutError):
        return None
    if proc.returncode != 0:
        return None
    entries: list[tuple[str, str]] = []
    fields = out.decode(errors="replace").split("\0")
    i = 0
    while i < len(fields):
        entry = fields[i]
        i += 1
        if len(entry) < 4 or entry[0] not in " MADRCU?!":
            continue
        if entry[0] in "RC":
            # rename/copy: the NEXT NUL field is the source path — consume
            # it so it is never misread as its own status entry.
            i += 1
        entries.append((entry[:2], entry[3:]))
    return entries


async def untracked_paths(cwd: Path) -> list[str] | None:
    """Untracked repo-relative paths (exact, quoting-proof), or None on failure."""
    entries = await _git_status_entries(cwd)
    if entries is None:
        return None
    return [path for status, path in entries if status == "??"]


async def worktree_snapshot(cwd: Path) -> dict | None:
    """Worktree state: {"untracked": {rel: sha256|None}, "numstat": {rel: lines}}.

    None when cwd is not a git work tree.
    """
    paths = await untracked_paths(cwd)
    if paths is None:
        return None
    untracked: dict[str, str | None] = {}
    for rel in paths:
        untracked[rel] = _sha256_file(cwd / rel)
    numstat: dict[str, int] = {}
    content: dict[str, str | None] = {}
    diff = await _git(cwd, "diff", "HEAD", "--numstat")
    for line in (diff or "").splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            adds, dels, rel = parts
            numstat[rel] = (int(adds) if adds.isdigit() else 0) + (
                int(dels) if dels.isdigit() else 0
            )
            # Content hash too: equal-VOLUME edits (1+1 -> 1+1) are still
            # caught — the trap case a pure numstat comparison misses.
            content[rel] = _sha256_file(cwd / rel)
    return {"untracked": untracked, "numstat": numstat, "content": content}


async def worktree_delta(cwd: Path, start: dict) -> tuple[list[str], int]:
    """(paths changed since ``start``, diff lines) — uncommitted included.

    A file dirty at baseline is excluded UNLESS its content hash changed
    (the run edited it — even an equal-volume edit, which a pure numstat
    comparison would miss); an untracked file is excluded unless its content
    fingerprint changed (or it is new since baseline).
    """
    now = await worktree_snapshot(cwd)
    if now is None:
        return [], 0
    changed: list[str] = []
    diff_lines = 0
    for rel, lines in now["numstat"].items():
        base = start["numstat"].get(rel)
        fingerprint_changed = now["content"].get(rel) != start["content"].get(rel)
        if base is None or fingerprint_changed:
            changed.append(rel)
            if base is None:
                diff_lines += lines
            else:
                diff_lines += max(0, lines - base)
    for rel, fingerprint in now["untracked"].items():
        if start["untracked"].get(rel) != fingerprint:
            changed.append(rel)
            try:
                diff_lines += len((cwd / rel).read_bytes().splitlines())
            except OSError:
                pass
    return changed, diff_lines


__all__ = ["untracked_paths", "worktree_delta", "worktree_snapshot"]
