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

from .gitenv import INTERNAL_GIT, git_config_home, git_env


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
            *INTERNAL_GIT,
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
            *INTERNAL_GIT,
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
            *INTERNAL_GIT,
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
    diff = await _git_raw(
        cwd, "diff", "HEAD", "--numstat", "--no-renames", "--no-ext-diff", "--no-textconv", "-z"
    )
    if diff is None:
        return None  # unknown is not "no tracked changes" (2.9.1 review)
    flagged = await flagged_state(cwd)
    if flagged is None:
        return None
    control = await git_control_state(cwd)
    if control is None:
        return None
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
    return {
        "untracked": untracked,
        "numstat": numstat,
        "content": content,
        "flagged": flagged,
        "control": control,
    }


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
    if "control" in start and git_control_changed(start["control"], now["control"]):
        return None  # git's own view of the tree can no longer be trusted
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
    # Edits and deletions behind assume-unchanged / skip-worktree flags. git
    # cannot diff them, so their size is counted conservatively -- the whole
    # file (or, for a deletion, the whole HEAD blob) -- never as zero
    # (2.9.1 review); an unmeasurable size makes the delta unavailable.
    for rel in flagged_changed(start.get("flagged", {}), now.get("flagged", {})):
        changed.append(rel)
        size = await _flagged_lines(cwd, rel)
        if size is None:
            return None
        diff_lines += size
    return list(dict.fromkeys(changed)), diff_lines


async def _flagged_lines(cwd: Path, rel: str) -> int | None:
    """Upper-bound line count for a changed flagged path, or None."""
    target = cwd / rel
    if target.is_symlink():
        return 1
    if target.is_file():
        try:
            return len(target.read_bytes().splitlines())
        except OSError:
            return None
    entry = await _git_raw(cwd, "ls-tree", "-z", "--full-tree", "HEAD", "--", rel)
    if entry is None:
        return None
    meta = entry.split("\t", 1)[0].split()
    if len(meta) != 3:
        return 0  # not in HEAD either: nothing was deleted
    blob = await _git_raw(cwd, "cat-file", "blob", meta[2])
    return None if blob is None else len(blob.splitlines())


#: Files that change what git REPORTS or RUNS -- filter, textconv and diff
#: drivers, attributes, excludes, sparse patterns, grafts, shallow
#: boundaries -- in the per-worktree and the common git directory. None of
#: them shows in `git status`, so a change made during the run (a shell
#: `git config`, a write the scope gate missed) is found only by comparing
#: two :func:`git_control_state` fingerprints (2.9.1 review). Configuration
#: is ALSO fingerprinted as git resolves it -- every scope and every
#: `include.path` target -- and so are the global attributes/excludes files.
_CONTROL_FILES = (
    "config",
    "config.worktree",
    "info/attributes",
    "info/exclude",
    "info/sparse-checkout",
    "info/grafts",
    "shallow",
)


async def git_control_state(cwd: Path) -> dict[str, str] | None:
    """Fingerprint of the repository's git control state: each of
    :data:`_CONTROL_FILES` (content hash or ``"<absent>"``) plus the replace
    refs. Compare two with :func:`git_control_changed`; any difference means
    git's view of the tree -- what status, diff, a hash or a checkout reports
    -- may have been redirected during the run, so callers that gate on that
    view must fail closed. None when it cannot be read (fail closed too)."""
    dirs = await _git_raw(
        cwd, "rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir"
    )
    lines = [line for line in (dirs or "").split("\n") if line]
    if len(lines) != 2:
        return None
    state: dict[str, str] = {}
    for label, base in zip(("git-dir", "common-dir"), lines, strict=True):
        for name in _CONTROL_FILES:
            target = Path(base) / name
            try:
                state[f"{label}/{name}"] = hashlib.sha256(target.read_bytes()).hexdigest()[:16]
            except FileNotFoundError:
                state[f"{label}/{name}"] = "<absent>"
            except OSError:
                return None  # unreadable (a directory, permissions): unknown
    # The configuration git actually applies: system, global, local,
    # worktree and command-line scopes, with every included file resolved --
    # an include target is not one of the files above. Hashed, never logged
    # (values can hold credentials).
    config = await _git_raw(cwd, "config", "--list", "--show-origin", "--show-scope", "-z")
    if config is None:
        return None
    state["config/effective"] = hashlib.sha256(config.encode()).hexdigest()[:16]
    for key, label in (("core.attributesFile", "attributes"), ("core.excludesFile", "excludes")):
        target = await _global_git_file(
            cwd, key, "attributes" if label == "attributes" else "ignore"
        )
        try:
            state[f"global/{label}"] = (
                hashlib.sha256(target.read_bytes()).hexdigest()[:16] if target else "<absent>"
            )
        except FileNotFoundError:
            state[f"global/{label}"] = "<absent>"
        except OSError:
            return None
    # In-tree .gitattributes files are control state too: `* -diff` turns
    # every review diff into a "Binary files differ" stub that costs nothing
    # against the diff budget, and a model can write one with write_file
    # (2.9.1 review). Tracked and untracked, every directory, content-hashed.
    attrs = await _git_raw(
        cwd,
        "ls-files",
        "-z",
        "--cached",
        "--others",
        "--exclude-standard",
        "--",
        ":(glob)**/.gitattributes",
    )
    # ...and IGNORED ones: a run can write `sub/.gitignore` hiding its own
    # `sub/.gitattributes` (2.9.1 review). `--directory` keeps a wholly
    # ignored directory one entry (no walk); the attributes inside it only
    # reach files that are ignored too.
    ignored = await _git_raw(
        cwd,
        "ls-files",
        "-z",
        "--others",
        "--ignored",
        "--exclude-standard",
        "--directory",
        "--",
        ":(glob)**/.gitattributes",
    )
    if attrs is None or ignored is None:
        return None
    found = {
        rel
        for rel in (*attrs.split("\0"), *ignored.split("\0"))
        if rel == ".gitattributes" or rel.endswith("/.gitattributes")
    }
    digest = hashlib.sha256()
    for rel in sorted(found | {".gitattributes"}):
        digest.update(rel.encode(errors="surrogateescape") + b"\0")
        digest.update((_sha256_file(cwd / rel) or "<absent>").encode() + b"\0")
    state["attributes/in-tree"] = digest.hexdigest()[:16]
    replace = await _git_raw(
        cwd, "for-each-ref", "--format=%(refname) %(objectname)", "refs/replace/"
    )
    if replace is None:
        return None
    state["refs/replace"] = hashlib.sha256(replace.encode()).hexdigest()[:16]
    return state


async def _global_git_file(cwd: Path, key: str, default_name: str) -> Path | None:
    """Where git reads the global attributes/excludes file: ``key`` when set
    (``--type=path`` expands ``~``), else ``$XDG_CONFIG_HOME/git/<name>``
    (``~/.config`` when unset). None when it cannot be located."""
    configured = await _git_raw(cwd, "config", "--type=path", "--get", key)
    if configured and configured.strip("\n"):
        target = Path(configured.rstrip("\n"))
        return target if target.is_absolute() else cwd / target
    base = git_config_home()
    return base / "git" / default_name if base else None


def git_control_changed(start: Mapping[str, str], now: Mapping[str, str]) -> list[str]:
    """Names of the :func:`git_control_state` entries that differ."""
    return sorted(k for k in set(start) | set(now) if start.get(k) != now.get(k))


#: Fingerprint for a flagged entry absent from the working tree.
_MISSING = "<missing>"


async def flagged_state(cwd: Path) -> dict[str, str] | None:
    """{path: fingerprint or "<missing>"} for every tracked entry flagged
    assume-unchanged or skip-worktree. git reports such files as clean, so
    `git diff` / `git status` never show an edit or a deletion of one (2.9.1
    reviews); compare two states with :func:`flagged_changed`. None when git
    fails -- callers that gate must fail closed."""
    listing = await _git_raw(cwd, "ls-files", "-v", "-z")
    if listing is None:
        return None
    state: dict[str, str] = {}
    for entry in listing.split("\0"):
        if len(entry) < 3 or entry[1] != " ":
            continue
        tag, rel = entry[0], entry[2:]
        if tag.islower() or tag == "S":
            target = cwd / rel
            present = target.exists() or target.is_symlink()
            state[rel] = (_sha256_file(target) or "<unreadable>") if present else _MISSING
    return state


def flagged_changed(start: Mapping[str, str], now: Mapping[str, str]) -> list[str]:
    """Flagged paths whose state differs between two :func:`flagged_state`
    snapshots: an edit, a deletion or a re-creation. A file absent at both
    points (a sparse-checkout skip-worktree entry) is not a change."""
    return sorted(rel for rel in set(start) | set(now) if start.get(rel) != now.get(rel))


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


#: git's C-style escapes for a quoted path in a patch header.
_PATCH_ESCAPES = {7: "\\a", 8: "\\b", 9: "\\t", 10: "\\n", 11: "\\v", 12: "\\f", 13: "\\r"}


def _patch_path(name: str) -> str:
    """``name`` as git writes it in a patch header: verbatim, or -- when it
    holds a quote, a backslash, a control or a non-ASCII byte -- C-quoted the
    way ``git apply`` parses it (2.9.1 review: a newline in a synthesized
    header made the recorded patch unappliable)."""
    raw = os.fsencode(name)
    if not any(b < 0x20 or b in (0x22, 0x5C, 0x7F) or b >= 0x80 for b in raw):
        return name
    out = []
    for b in raw:
        if b in _PATCH_ESCAPES:
            out.append(_PATCH_ESCAPES[b])
        elif b in (0x22, 0x5C):
            out.append("\\" + chr(b))
        elif 0x20 <= b < 0x7F:
            out.append(chr(b))
        else:
            out.append(f"\\{b:03o}")
    return '"' + "".join(out) + '"'


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
    # Confine where the ENTRY lives, not where a symlink points: git diffs a
    # link's text, never its target, so an in-repo link to /elsewhere is a
    # valid new file; a `../x` or absolute `rel` is not.
    real = canonicalize(target.parent) / target.name
    if root not in real.parents:
        return ""
    if target.is_symlink():
        # A symlink's content is its link text (mode 120000) -- exactly what
        # git records. `git diff --no-index` cannot produce it for a link to
        # a DIRECTORY (it walks the target), so build it directly.
        try:
            link = os.readlink(target)
        except OSError:
            return ""
        a, b = _patch_path("a/" + rel), _patch_path("b/" + rel)
        lines = link.split("\n")  # a link text is bytes: it may hold newlines
        body = "".join(f"+{line}\n" for line in lines)
        return (
            f"diff --git {a} {b}\nnew file mode 120000\n--- /dev/null\n"
            f"+++ {b}\n@@ -0,0 +1,{len(lines)} @@\n{body}\\ No newline at end of file\n"
        )
    if target.is_dir():
        return ""
    args = [*INTERNAL_GIT, "-C", str(cwd), "diff", "--no-index", "--no-renames"]
    args += ["--no-ext-diff", "--no-textconv"]
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
    "flagged_changed",
    "flagged_state",
    "new_file_patch",
    "new_files_since",
    "untracked_fingerprints",
    "untracked_paths",
    "worktree_delta",
    "worktree_delta_checked",
    "worktree_snapshot",
]
