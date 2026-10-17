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

from .errors import PxxError
from .gitenv import communicate_bounded, git_env

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


async def _git(cwd: Path, *args: str) -> str | None:
    """Run a git command; return stdout or None when unavailable/failed. Bounded
    by git_timeout(): a wedged git or a blocking hook can't hang the safety-net
    tie (this runs at startup, before the run's own budget exists)."""
    try:
        # --no-replace-objects: a `git replace` made during the run must not
        # redirect what a SHA means to the guard or to `reset --hard` (2.9.1
        # review) -- every safety-net command sees the real objects.
        proc = await asyncio.create_subprocess_exec(
            "git", "--no-replace-objects", *args, cwd=cwd, stdout=PIPE, stderr=PIPE, env=git_env()
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
    return SafetyNet(
        tag=tag,
        stash_message=stash_message,
        head_sha=head.strip(),
        head_ref=(head_ref or "").strip(),
        stash_sha=(stash_sha or "").strip() or None,
        head_tree=(head_tree or "").strip() or None,
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

    Never rewrites history: if HEAD moved or the branch changed during the run
    (:func:`head_moved_since`), nothing is reset or popped -- the tag and the
    stash stay for the operator, and the caller reports why.
    """
    if net is None or (net.tag is None and not net.stash_message):
        return False
    if await head_moved_since(cwd, net):
        return False
    did = False
    if net.tag:
        if await _git(cwd, "reset", "--hard", net.tag) is None:
            return False  # reset failed — do NOT pop onto an unexpected tree
        did = True
    if net.stash_message:
        ref = await _stash_ref(cwd, net.stash_message, net.stash_sha)
        if ref is not None and await _git(cwd, "stash", "pop", ref) is not None:
            did = True
    return did


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

    Fail-closed secrets gate: after staging and before committing, the staged
    delta is scanned with :func:`pxx.governance.scan_staged`. ANY finding —
    or a scan that cannot run (``PxxError``; per governance doctrine a scan
    that can't run is NOT a clean scan) — skips the commit and returns None:
    the work stays in the tree, fail-closed on the commit, fail-soft on the
    session (the function's contract above).
    """
    if only is not None and not only:
        return None  # the session changed nothing
    staged: list[str] = []
    if only is not None:
        for rel in sorted(only):
            # --literal-pathspecs: a filename is a NAME, never a pattern -- a
            # legal file called `*.py` must not stage every .py (2.9.1 review).
            if await _git(cwd, "--literal-pathspecs", "add", "--", rel) is not None:
                staged.append(rel)
            elif (
                not (cwd / rel).exists()
                and await _git(cwd, "cat-file", "-e", f"HEAD:{rel}") is not None
            ):
                # A deletion already staged (e.g. the source of a `git mv`):
                # `git add` cannot re-add a path gone from index and tree, but
                # it is still the session's change -- keep it in the commit,
                # or a rename is committed as half an add (2.9.1 review).
                staged.append(rel)
        if not staged:
            log.warning("auto-commit: nothing stageable in the session's delta")
            return None
    else:
        status = await _git(cwd, "status", "--porcelain=v1", "--untracked-files=all")
        if not status:
            return None  # nothing to commit
        if await _git(cwd, "add", "-A") is None:
            log.warning("auto-commit: git add failed; leaving work uncommitted")
            return None
    # Fail-closed secrets gate (staged delta, before commit). Lazy import to
    # avoid an import cycle (session.py imports this module lazily too).
    from .governance import load_denylist, scan_staged

    try:
        findings = scan_staged(cwd=cwd, denylist=load_denylist())
    except PxxError as exc:
        # A scan that cannot run is NOT a clean scan — skip the commit.
        log.warning("auto-commit: governance scan could not run (%s); not committing", exc)
        return None
    if findings:
        detail = "; ".join(f"{f.rule} at {f.path}:{f.line} [{f.preview}]" for f in findings)
        log.warning(
            "auto-commit: BLOCKED by governance — %d finding(s) in the staged delta; "
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
    # Path-limited when the session's delta is known: a bare `git commit`
    # would also sweep in anything the operator had already staged -- e.g. a
    # protected path -- into the commit that is meant to be the session's
    # own, secrets-gated one (2.9.1 review). With a pathspec git commits
    # exactly these paths; other staged entries stay staged, uncommitted.
    pathspec = ["--", *staged] if only is not None else []
    literal = ["--literal-pathspecs"] if only is not None else []
    # No repository hooks on pxx's own commit (2.9.1 review): a pre-commit
    # hook written during the run (.git/hooks is invisible to every scope
    # check) could add paths or bytes after the secrets scan above. pxx's
    # gate is the check for this commit.
    no_hooks = ["-c", "core.hooksPath=/dev/null"]
    committed = await _git(
        cwd, *literal, *no_hooks, *identity, "commit", "-q", "--no-verify", "-m", message, *pathspec
    )
    if committed is None:
        log.warning("auto-commit: git commit failed; leaving work uncommitted")
        return None
    return await _git(cwd, "rev-parse", "HEAD")


__all__ = [
    "SafetyNet",
    "commit_session_work",
    "head_moved_since",
    "restore_safety_net",
    "tie_safety_net",
]
