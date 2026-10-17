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
import os
from collections.abc import Callable, Mapping
from pathlib import Path

from .gitenv import git_env


def _sha256_file(path: Path) -> str | None:
    """Content fingerprint; a symlink is fingerprinted by its LINK TEXT (what
    git tracks) -- following it fails for a dangling or directory link, and
    two None fingerprints compared equal, hiding a newly created link (2.9.1
    review)."""
    try:
        if path.is_symlink():
            return "link:" + hashlib.sha256(os.readlink(path).encode()).hexdigest()[:16]
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
            env=git_env(),
        )
        out, _ = await asyncio.wait_for(proc.communicate(), 15.0)
    except (OSError, TimeoutError):
        return None
    if proc.returncode != 0:
        return None
    return out.decode(errors="replace").strip()


async def _git_raw(cwd: Path, *args: str) -> str | None:
    """Like :func:`_git` but UNSTRIPPED -- for NUL-separated output, where a
    strip could clip a path's trailing whitespace."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(cwd),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=git_env(),
        )
    except OSError:
        return None
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), 15.0)
    except TimeoutError:
        proc.kill()
        await proc.wait()  # reap it: never leak a child on the way past
        return None
    if proc.returncode != 0:
        return None
    return out.decode(errors="replace")


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
            env=git_env(),
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
    # -z: paths byte-exact (no quoting); --no-renames: a staged rename is its
    # two REAL paths, not one synthetic "a => b" string that no scope check,
    # fingerprint or `git add` can use (2.9.1 reviews).
    diff = await _git_raw(cwd, "diff", "HEAD", "--numstat", "--no-renames", "-z")
    if diff is None:
        return None  # unknown is not "no tracked changes" (2.9.1 review)
    for record in diff.split("\0"):
        parts = record.split("\t", 2)
        if len(parts) == 3 and parts[2]:
            adds, dels, rel = parts
            numstat[rel] = (int(adds) if adds.isdigit() else 0) + (
                int(dels) if dels.isdigit() else 0
            )
            # Content hash too: equal-VOLUME edits (1+1 -> 1+1) are still
            # caught — the trap case a pure numstat comparison misses.
            content[rel] = _sha256_file(cwd / rel)
    return {"untracked": untracked, "numstat": numstat, "content": content}


async def worktree_delta(cwd: Path, start: dict) -> tuple[list[str], int]:
    """See :func:`worktree_delta_checked`; ``([], 0)`` when the final state
    cannot be read (callers that gate on the delta must use the checked
    form)."""
    return await worktree_delta_checked(cwd, start) or ([], 0)


async def worktree_delta_checked(cwd: Path, start: dict) -> tuple[list[str], int] | None:
    """(paths changed since ``start``, diff lines) — uncommitted included.

    A file dirty at baseline is excluded UNLESS its content hash changed
    (the run edited it — even an equal-volume edit, which a pure numstat
    comparison would miss); an untracked file is excluded unless its content
    fingerprint changed (or it is new since baseline).
    """
    now = await worktree_snapshot(cwd)
    if now is None:
        return None
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
        # absent at baseline is a change on its own -- never inferred from two
        # fingerprints that may both be None (unreadable)
        if rel not in start["untracked"] or start["untracked"][rel] != fingerprint:
            changed.append(rel)
            try:
                diff_lines += len((cwd / rel).read_bytes().splitlines())
            except OSError:
                pass
    # Paths present at baseline and gone now are changes too (2.9.1 review):
    # a dirty file the run restored to HEAD drops out of the numstat, and a
    # pre-existing untracked file the run deleted drops out of the listing.
    # Iterating only the final state made both invisible.
    for rel, lines in start["numstat"].items():
        if rel not in now["numstat"]:
            changed.append(rel)
            diff_lines += lines
    for rel in start["untracked"]:
        if rel not in now["untracked"] and rel not in now["numstat"]:
            changed.append(rel)
    return list(dict.fromkeys(changed)), diff_lines


async def untracked_fingerprints(cwd: Path) -> dict[str, str | None] | None:
    """{untracked path: content fingerprint} -- a baseline for
    :func:`new_files_since`. None when the listing failed. Take it AFTER any
    safety-net stash: the stash removes pre-existing untracked files, and a
    baseline that still lists them would hide a run that re-creates them."""
    paths = await untracked_paths(cwd)
    if paths is None:
        return None
    return {rel: _sha256_file(cwd / rel) for rel in paths}


async def new_files_since(
    cwd: Path,
    baseline: Mapping[str, str | None],
    skip: Callable[[str], bool] | None = None,
) -> list[str] | None:
    """Untracked paths the run CREATED or CHANGED since ``baseline`` (from
    :func:`untracked_fingerprints`) -- a plain ``git diff <ref>`` shows
    neither. ``skip`` drops paths the caller does not count (e.g. interpreter
    caches). None when the listing failed -- never an empty list standing in
    for "unknown": callers that gate on this must fail closed."""
    paths = await untracked_paths(cwd)
    if paths is None:
        return None
    return [
        p
        for p in paths
        if not (skip and skip(p)) and (p not in baseline or baseline[p] != _sha256_file(cwd / p))
    ]


async def new_file_patch(cwd: Path, rel: str, *, binary: bool = False) -> str:
    """A new-file hunk for an untracked ``rel`` (``git diff --no-index
    /dev/null rel``). Read-only by construction: nothing is staged, no
    intent-to-add. "" on any failure. Output is NOT stripped -- a patch must
    keep its trailing newline to stay appliable."""
    # Only paths inside the repo, and never a directory: `rel` comes from git
    # status today, but this helper must not read outside the tree if a
    # caller ever hands it `../x` or an absolute path (2.9.1 review). A file
    # symlink is fine -- git diffs the link text, not its target.
    from .safety import canonicalize

    target = cwd / rel
    root = canonicalize(cwd)
    real = canonicalize(target)
    if real != root and root not in real.parents:
        return ""
    if target.is_dir() and not target.is_symlink():
        return ""
    args = ["git", "-C", str(cwd), "diff", "--no-index", "--no-renames"]
    if binary:
        args.append("--binary")
    args += ["--", "/dev/null", rel]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=git_env(),
        )
    except OSError:
        return ""
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), 15.0)
    except TimeoutError:
        proc.kill()
        await proc.wait()  # reap it: never leak a child on the way past
        return ""
    # --no-index exits 1 when the two sides differ, which a new file always does
    if proc.returncode not in (0, 1):
        return ""
    return out.decode(errors="replace")


__all__ = [
    "new_file_patch",
    "new_files_since",
    "untracked_fingerprints",
    "untracked_paths",
    "worktree_delta",
    "worktree_delta_checked",
    "worktree_snapshot",
]
