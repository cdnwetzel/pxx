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
import os
import tempfile
import time
from asyncio.subprocess import PIPE
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .errors import PxxError
from .gitenv import INTERNAL_GIT, communicate_bounded, git_env

log = logging.getLogger("pxx.safety_net")


@dataclass(frozen=True)
class SafetyNet:
    """What was tied at session start. Either field may be None on partial
    failure — report exactly what exists, never claim more."""

    tag: str | None  # pxx-pre/<ts>(-N), None when tagging failed
    stash_message: str | None  # None when the tree was clean (tag only)
    #: Where HEAD stood when the net was tied: the commit, and the branch ref
    #: ("" when detached). A restore only resets when both are unchanged --
    #: otherwise the reset would move a branch the run switched to, or orphan
    #: commits made during the run (2.9.1 review). None: unknown (old callers).
    head_sha: str | None = None
    head_ref: str | None = None
    #: The stash's own commit SHA: restore pops THIS object, never "the newest
    #: stash with the net's message" -- a later stash can reuse the message
    #: (2.9.1 review). None: no stash, or unknown (old callers).
    stash_sha: str | None = None
    #: The start commit's TREE: a second, object-level check on what a restore
    #: would bring back. None: unknown (old callers).
    head_tree: str | None = None
    #: The repository's git control state when the net was tied
    #: (:func:`pxx.worktree.git_control_state`): filter/diff drivers,
    #: attributes, excludes, replace refs. A run that changed it can make a
    #: checkout write other bytes than the tree holds, so restore and commit
    #: refuse (2.9.1 review). None: unknown (old callers).
    control_state: dict[str, str] | None = None


#: Every safety-net / commit git command (see :data:`INTERNAL_GIT`).
_GIT_BASE = INTERNAL_GIT


async def _git_io(
    cwd: Path,
    *args: str,
    stdin: bytes | None = None,
    index_file: str | None = None,
    raw: bool = False,
) -> str | None:
    """Like :func:`_git`, with optional stdin bytes and an alternate index
    (``GIT_INDEX_FILE``) -- the commit is built in a temporary index so the
    operator's real index is never the staging area for pxx's commit."""
    env = git_env()
    if index_file is not None:
        env["GIT_INDEX_FILE"] = index_file
    try:
        proc = await asyncio.create_subprocess_exec(
            *_GIT_BASE, *args, cwd=cwd, stdin=PIPE, stdout=PIPE, stderr=PIPE, env=env
        )
        out, _ = await communicate_bounded(proc, stdin, label=args[0] if args else "")
    except (OSError, TimeoutError):
        return None
    if proc.returncode != 0:
        return None
    text = out.decode(errors="surrogateescape")
    return text if raw else text.strip()  # raw: NUL-separated output keeps its spaces


async def _git(cwd: Path, *args: str) -> str | None:
    """Run a git command; return stdout or None when unavailable/failed. Bounded
    by git_timeout(): a wedged git or a blocking hook can't hang the safety-net
    tie (this runs at startup, before the run's own budget exists)."""
    try:
        # --no-replace-objects: a `git replace` made during the run must not
        # redirect what a SHA means to the guard or to `reset --hard` (2.9.1
        # review) -- every safety-net command sees the real objects.
        proc = await asyncio.create_subprocess_exec(
            *_GIT_BASE, *args, cwd=cwd, stdout=PIPE, stderr=PIPE, env=git_env()
        )
        out, _ = await communicate_bounded(proc, label=args[0] if args else "")
    except (OSError, TimeoutError):
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
    stash_sha: str | None = None
    status = await _git(cwd, "status", "--porcelain=v1", "--untracked-files=all")
    if status:
        stash_message = f"pxx safety net {run_id}"
        out = await _git(cwd, "stash", "push", "--include-untracked", "-m", stash_message)
        if out is None:
            log.warning("safety-net stash failed; continuing without it")
            stash_message = None
        else:
            stash_sha = await _git(cwd, "rev-parse", "--verify", "stash@{0}")

    tag = await _unique_tag(cwd)
    if await _git(cwd, "tag", tag, "HEAD") is None:
        log.warning("safety-net tag %s failed", tag)
        tag = None

    if tag is None and stash_message is None:
        return None
    head_ref = await _git(cwd, "symbolic-ref", "-q", "HEAD")
    head_tree = await _git(cwd, "rev-parse", "--verify", "HEAD^{tree}")
    from .worktree import git_control_state

    return SafetyNet(
        tag=tag,
        stash_message=stash_message,
        head_sha=head.strip(),
        head_ref=(head_ref or "").strip(),
        stash_sha=(stash_sha or "").strip() or None,
        head_tree=(head_tree or "").strip() or None,
        # Unreadable at tie time is recorded as such, never as "unknown"
        # (None): restore and commit then refuse (2.9.1 review).
        control_state=await git_control_state(cwd) or {_UNREADABLE_CONTROL: ""},
    )


async def _stash_ref(cwd: Path, message: str, sha: str | None = None) -> str | None:
    """The ``stash@{N}`` holding the net's stash. With ``sha`` (recorded at tie
    time) ONLY the entry whose commit is that object qualifies -- a later
    stash reusing the message is never popped, and if the object is gone no
    stash is touched. Without it (an old net), the message must equal
    ``message`` EXACTLY, so ``run-1`` never selects ``run-10``. None if
    absent."""
    if sha:
        hashes = await _git(cwd, "stash", "list", "--format=%H")
        for i, line in enumerate((hashes or "").splitlines()):
            if line.strip() == sha:
                return f"stash@{{{i}}}"
        return None
    listing = await _git(cwd, "stash", "list")
    if not listing:
        return None
    for line in listing.splitlines():
        ref, sep, rest = line.partition(":")  # "stash@{N}: On <branch>: <message>"
        if not sep:
            continue
        _, _, subject = rest.rpartition(": ")  # the message is after the last ": "
        if subject.strip() == message:
            return ref.strip()
    return None


async def head_moved_since(cwd: Path, net: SafetyNet | None) -> str:
    """Why HEAD no longer matches where the net was tied ("" when it does,
    or when the net predates this check)."""
    if net is None or net.head_sha is None:
        return ""
    now = (await _git(cwd, "rev-parse", "--verify", "HEAD") or "").strip()
    ref = (await _git(cwd, "symbolic-ref", "-q", "HEAD") or "").strip()
    if ref != (net.head_ref or ""):
        return f"the checked-out branch changed during the run ({net.head_ref or 'detached'} -> {ref or 'detached'})"
    if now != net.head_sha:
        return f"HEAD moved during the run ({net.head_sha[:7]} -> {now[:7] or '?'})"
    # The reset target itself: a run that can run git can move the tag (2.9.1
    # review) -- `git tag -f` onto a planted commit, then fail, and the
    # "restore" would reset the branch onto it.
    if net.tag:
        target = (await _git(cwd, "rev-parse", "--verify", f"{net.tag}^{{commit}}") or "").strip()
        if target != net.head_sha:
            return f"the safety-net tag {net.tag} no longer points at the run's start"
    if net.head_tree:
        tree = (await _git(cwd, "rev-parse", "--verify", "HEAD^{tree}") or "").strip()
        if tree != net.head_tree:
            return "the start commit no longer has the tree it had when the run began"
    return ""


#: :attr:`SafetyNet.control_state` marker: the state could not be read when
#: the net was tied, so no later state can be shown unchanged.
_UNREADABLE_CONTROL = "<unreadable at tie>"


async def git_state_changed_since(cwd: Path, control_start: dict[str, str] | None) -> str:
    """Why the repository's git control state no longer matches
    ``control_start`` ("" when it does, or when the baseline is unknown).
    An unreadable current state counts as changed: nothing is reset or
    committed on a view of the tree that cannot be checked."""
    if control_start is None:
        return ""
    if _UNREADABLE_CONTROL in control_start:
        return "the repository's git configuration could not be read when the run started"
    from .worktree import git_control_changed, git_control_state

    now = await git_control_state(cwd)
    if now is None:
        return "the repository's git configuration could not be read"
    changed = git_control_changed(control_start, now)
    if changed:
        return (
            "the repository's git configuration changed during the run (" + ", ".join(changed) + ")"
        )
    return ""


async def restore_blocker(cwd: Path, net: SafetyNet | None) -> tuple[str, str]:
    """(reason, detail) for why :func:`restore_safety_net` must not run, or
    ``("", "")``. Reasons: ``head_moved`` (history, the branch, the tag or
    the start tree moved) and ``git_state_changed`` (filter/diff drivers,
    attributes, excludes or replace refs changed -- a checkout could then
    write other bytes than the start tree holds)."""
    if net is None:
        return "", ""
    moved = await head_moved_since(cwd, net)
    if moved:
        return "head_moved", moved
    changed = await git_state_changed_since(cwd, net.control_state)
    if changed:
        return "git_state_changed", changed
    return "", ""


async def restore_safety_net(cwd: Path, net: SafetyNet | None) -> bool:
    """Restore the user's pre-run working tree — for use on an ABORT (a
    non-COMPLETED outcome), so a run that produced nothing worth keeping does
    not strand the user's uncommitted work.

    ``reset --hard`` to the ``pxx-pre`` tag (discards the failed run's partial
    edits, back to pre-run HEAD), then pops the net's OWN stash — restoring both
    tracked-dirty AND **untracked** files. The tag alone cannot bring untracked
    files back (they are in no commit), which is the data-loss this fixes.

    A tag-only net (clean pre-run tree) is still reset, so a failed run leaves a
    clean tree, not its garbage.

    Fail-soft (hard rule 1): if the reset fails the stash is left untouched (we
    never pop onto an unexpected tree); any git failure leaves tag/stash intact
    for manual recovery. Returns True when something was restored.

    Never rewrites history: if HEAD moved or the branch changed during the run,
    or the repository's git configuration changed (:func:`restore_blocker`),
    nothing is reset or popped -- the tag and the stash stay for the
    operator, and the caller reports why.
    """
    if net is None or (net.tag is None and not net.stash_message):
        return False
    did, _problem = await _restore(cwd, net)
    return did


async def _restore(cwd: Path, net: SafetyNet) -> tuple[bool, str]:
    """(something restored?, what did NOT come back -- "" when nothing)."""
    reason, _ = await restore_blocker(cwd, net)
    if reason:
        return False, ""
    did = False
    if net.tag:
        # Reset to the SHA that was just checked, never the tag NAME: a
        # re-tag between the check and the reset is not covered (2.9.1
        # review). Old nets without a recorded SHA fall back to the tag.
        if await _git(cwd, "reset", "--hard", net.head_sha or net.tag) is None:
            return False, ""  # reset failed — do NOT pop onto an unexpected tree
        did = True
    if net.stash_message:
        ref = await _stash_ref(cwd, net.stash_message, net.stash_sha)
        if ref is not None and await _git(cwd, "stash", "pop", ref) is not None:
            did = True
        else:
            # The operator's pre-run work is still in the stash: say so, never
            # a bare "restored" (2.9.1 review: a file the run created at a
            # stashed path made the pop fail behind `restored: true`).
            where = (net.stash_sha or "")[:12] or net.stash_message
            return did, f"the pre-run changes are still stashed ({where}); git stash pop it"
    return did, ""


async def _file_mode(cwd: Path, rel: str, st_mode: int, filemode: bool) -> str:
    """The mode to commit a regular file with, as `git add` would choose it:
    the owner execute bit (``S_IXUSR``) when ``core.fileMode`` is on; else the
    mode already in the index (or HEAD) for an existing entry, 100644 for a
    new one."""
    if filemode:
        return "100755" if st_mode & 0o100 else "100644"
    for args in (
        ("ls-files", "-s", "-z", "--", f":(literal){rel}"),
        ("ls-tree", "-z", "--full-tree", "HEAD", "--", f":(literal){rel}"),
    ):
        entry = await _git_io(cwd, *args, raw=True)
        mode = (entry or "").split(" ", 1)[0]
        if mode in ("100644", "100755"):
            return mode
    return "100644"


async def _filtered_paths(cwd: Path, names: list[str]) -> list[str] | None:
    """The ``names`` that carry a git ``filter`` attribute (None on failure)."""
    if not names:
        return []
    out = await _git_io(
        cwd,
        "check-attr",
        "-z",
        "--stdin",
        "filter",
        stdin=b"\0".join(map(os.fsencode, names)) + b"\0",
        raw=True,
    )
    if out is None:
        return None
    fields = out.split("\0")
    return [
        fields[i]
        for i in range(0, len(fields) - 2, 3)
        if fields[i + 2] not in ("unspecified", "unset")
    ]


def _remove_created(cwd: Path, created: Sequence[str]) -> None:
    """Delete files a failed run created (and directories that leaves
    empty), confined to ``cwd``. Best-effort: what cannot be removed stays."""
    from .safety import canonicalize

    root = canonicalize(cwd)
    for rel in created:
        target = cwd / rel
        # Confine the ENTRY, not a symlink's target: unlink removes the link.
        parent = canonicalize(target.parent)
        if root != parent and root not in parent.parents:
            continue
        if target.is_dir() and not target.is_symlink():
            continue
        try:
            target.unlink()
        except FileNotFoundError:
            continue
        except OSError:
            log.warning("safety net: could not remove created file %s", rel)
            continue
        while parent != root:
            try:
                parent.rmdir()  # only succeeds when empty
            except OSError:
                break
            parent = parent.parent


async def settle_failed_run(
    cwd: Path,
    net: SafetyNet | None,
    emit: Callable[[dict], Awaitable[None]],
    *,
    created: Sequence[str] = (),
) -> str:
    """After a non-COMPLETED outcome: restore the pre-run tree
    (:func:`restore_safety_net`) or, when :func:`restore_blocker` refuses,
    leave everything for the operator. Emits the ``safety_net``
    ``gate_decision`` either way and returns the summary suffix to append
    ("" when there is nothing to say). One implementation for the session,
    the loop and the goal path.

    ``created``: repo-relative files the caller KNOWS the run created (absent
    from its post-stash baseline). ``reset --hard`` never removes untracked
    files, so they are deleted first -- only when the restore is allowed,
    only inside the repo, never a directory (2.9.1 review)."""
    if net is None:
        return ""
    reason, detail = await restore_blocker(cwd, net)
    if reason:
        await emit(
            {
                "gate": "safety_net",
                "allowed": False,
                "restored": False,
                "reason": reason,
                "tag": net.tag or "",
            }
        )
        return f" [net not restored: {detail}; the start is tagged {net.tag or '(untagged)'}]"
    _remove_created(cwd, created)
    if net.tag is None and not net.stash_message:
        return ""
    did, problem = await _restore(cwd, net)
    if problem:
        await emit(
            {
                "gate": "safety_net",
                "allowed": True,
                "restored": False,
                "reason": "stash_not_restored",
                "tag": net.tag or "",
            }
        )
        return f" [net partly restored: {problem}]"
    if did:
        await emit({"gate": "safety_net", "allowed": True, "restored": True, "tag": net.tag or ""})
    return ""


async def commit_session_work(
    cwd: Path,
    *,
    task_preview: str,
    net_tag: str | None,
    only: set[str] | None = None,
    control_start: dict[str, str] | None = None,
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

    Fail-closed secrets gate: each path's bytes are hashed once and those
    exact blobs are scanned with :func:`pxx.governance.scan_blobs` before
    the commit is built from them (the operator's index is never the
    staging area). ANY finding —
    or a scan that cannot run (``PxxError``; per governance doctrine a scan
    that can't run is NOT a clean scan) — skips the commit and returns None:
    the work stays in the tree, fail-closed on the commit, fail-soft on the
    session (the function's contract above).
    """
    if only is not None and not only:
        return None  # the session changed nothing
    # A run that changed filter drivers, attributes or replace refs can make
    # git hash or report other bytes than the tree holds (2.9.1 review).
    changed = await git_state_changed_since(cwd, control_start)
    if changed:
        log.warning("auto-commit skipped: %s; nothing committed", changed)
        return None
    # The commit is built from blobs pxx hashed ONCE and scanned, in a
    # temporary index, with `commit-tree` (2.9.1 reviews): `git commit --
    # <paths>` re-read the working tree (and re-ran clean filters) AFTER the
    # scan, an assume-unchanged file's `git add` did nothing, a name like
    # `0:x` parsed as a revision, and repository hooks ran. Here nothing is
    # read twice, no name is parsed as a revision, no hook runs, and the
    # operator's real index is only updated for the committed paths, after
    # the fact.
    head = await _git(cwd, "rev-parse", "--verify", "-q", "HEAD")
    if only is None:
        listing = await _git_io(
            cwd, "status", "--porcelain=v1", "-z", "--no-renames", "--untracked-files=all", raw=True
        )
        if listing is None:
            return None
        names = {e[3:] for e in listing.split("\0") if len(e) > 3}
    else:
        names = set(only)
    # A `filter` attribute makes `hash-object --path` run a clean command on
    # the bytes BEFORE the scan -- it can see a secret, rewrite what is
    # committed, or write elsewhere (2.9.1 review). Such paths are committed
    # by the operator, never by pxx; eol/ident conversions stay (no code).
    filtered = await _filtered_paths(cwd, sorted(names))
    if filtered is None or filtered:
        log.warning(
            "auto-commit skipped: %s; nothing committed",
            "could not read git attributes"
            if filtered is None
            else f"{len(filtered)} path(s) carry a git filter attribute",
        )
        return None
    # git's own rule for the executable bit: with core.fileMode=false (WSL
    # /mnt/c, FAT, some network mounts report 0o777) the worktree bits are
    # ignored and the recorded mode is kept (2.9.1 review).
    filemode = (await _git(cwd, "config", "--type=bool", "core.fileMode") or "true") != "false"
    entries: list[tuple[str, str, str]] = []  # (mode, sha, path); mode "0" = delete
    for rel in sorted(names):
        target = cwd / rel
        if target.is_symlink():
            sha = await _git_io(
                cwd,
                "hash-object",
                "-w",
                "--stdin",
                "--no-filters",
                stdin=os.fsencode(os.readlink(target)),
            )
            mode = "120000"
        elif target.is_file():
            try:
                data = target.read_bytes()  # the ONE read of these bytes
            except OSError:
                return None
            sha = await _git_io(cwd, "hash-object", "-w", "--stdin", f"--path={rel}", stdin=data)
            mode = await _file_mode(cwd, rel, target.stat().st_mode, filemode)
        elif not target.exists():
            tracked = head and await _git_io(
                cwd,
                "ls-tree",
                "-z",
                "--full-tree",
                "HEAD",
                "--",
                rel,
            )
            if not tracked:
                continue  # never existed in HEAD: nothing to delete
            sha, mode = "", "0"
        else:
            continue  # a directory (e.g. a submodule): not the session's file
        if mode != "0" and not sha:
            log.warning("auto-commit: could not hash %s; leaving work uncommitted", rel)
            return None
        entries.append((mode, sha, rel))
    if not entries:
        log.warning("auto-commit: nothing stageable in the session's delta")
        return None
    # Fail-closed secrets gate on the EXACT blobs the commit will record.
    from .governance import load_denylist, scan_blobs

    try:
        findings = scan_blobs(
            cwd, [(rel, sha) for mode, sha, rel in entries if mode != "0"], denylist=load_denylist()
        )
    except PxxError as exc:
        # A scan that cannot run is NOT a clean scan — skip the commit.
        log.warning("auto-commit: governance scan could not run (%s); not committing", exc)
        return None
    if findings:
        detail = "; ".join(f"{f.rule} at {f.path}:{f.line} [{f.preview}]" for f in findings)
        log.warning(
            "auto-commit: BLOCKED by governance — %d finding(s) in the session's delta; "
            "not committing, work left in the tree: %s",
            len(findings),
            detail,
        )
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
    null_sha = "0" * (len(head) if head else 40)
    info = b"".join(
        (f"{mode} {sha or null_sha}\t".encode() + os.fsencode(rel) + b"\0")
        for mode, sha, rel in entries
    )
    with tempfile.TemporaryDirectory(prefix="pxx-commit-") as tmp:
        index = str(Path(tmp) / "index")
        base = ["read-tree", head] if head else ["read-tree", "--empty"]
        if await _git_io(cwd, *base, index_file=index) is None:
            return None
        if (
            await _git_io(cwd, "update-index", "-z", "--index-info", stdin=info, index_file=index)
            is None
        ):
            log.warning("auto-commit: could not build the commit's index; leaving work uncommitted")
            return None
        tree = await _git_io(cwd, "write-tree", index_file=index)
    if not tree:
        return None
    if head and tree == await _git(cwd, "rev-parse", "--verify", f"{head}^{{tree}}"):
        return None  # the session's paths already match HEAD: nothing to commit
    parents = ["-p", head] if head else []
    commit = await _git_io(cwd, *identity, "commit-tree", tree, *parents, "-m", message)
    if not commit:
        log.warning("auto-commit: git commit-tree failed; leaving work uncommitted")
        return None
    # Move HEAD only if it is still where we read it (no race, no surprise).
    update = ["update-ref", "-m", f"pxx: {preview}", "HEAD", commit] + ([head] if head else [])
    if await _git_io(cwd, *update) is None:
        log.warning("auto-commit: HEAD moved under the commit; not updating it")
        return None
    # Bring the real index in line for exactly the committed paths, so they
    # read as clean; every other entry (the operator's staging) is untouched.
    await _git_io(cwd, "update-index", "-z", "--index-info", stdin=info)
    return commit


__all__ = [
    "SafetyNet",
    "commit_session_work",
    "git_state_changed_since",
    "head_moved_since",
    "restore_blocker",
    "restore_safety_net",
    "settle_failed_run",
    "tie_safety_net",
]
