"""Sanitized environment for git subprocesses.

git exports repo-targeting and identity variables (``GIT_DIR``,
``GIT_INDEX_FILE``, ``GIT_AUTHOR_*``, …) into hooks. Any pxx invocation
that inherits them — running under a pre-commit hook, a CI step, another
tool's hook — would silently operate on the *caller's* repository instead
of the repo pxx was pointed at (proven 2026-07-26: a leaked ``GIT_DIR``
made a tmp-scaffold ``git add -A`` stage deletion of every tracked file
in the real repo). Every pxx git subprocess must therefore run with these
variables scrubbed: pxx targets the repository given by ``cwd``, with that
repository's configured identity, always.

Deliberately NOT scrubbed: transport/config vars (``GIT_SSH*``,
``GIT_CONFIG_GLOBAL``, ``GIT_TRACE*``…) — removing those would break user
setups without any wrong-repo risk.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os

log = logging.getLogger("pxx.gitenv")

_DEFAULT_GIT_TIMEOUT = 60.0

SCRUBBED_GIT_VARS: tuple[str, ...] = (
    # repo targeting — the wrong-repo class
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_NAMESPACE",
    "GIT_PREFIX",
    # identity overrides — beat the target repo's own config
    "GIT_AUTHOR_NAME",
    "GIT_AUTHOR_EMAIL",
    "GIT_AUTHOR_DATE",
    "GIT_COMMITTER_NAME",
    "GIT_COMMITTER_EMAIL",
    "GIT_COMMITTER_DATE",
)


def git_env() -> dict[str, str]:
    """A copy of the current environment safe to hand a git subprocess."""
    return {k: v for k, v in os.environ.items() if k not in SCRUBBED_GIT_VARS}


def git_timeout() -> float:
    """Wall-clock bound for a single git subprocess. Generous enough for a large
    stash/diff, but finite: a wedged git or a BLOCKING git hook (a pre-commit
    prompt, a credential helper) must never hang a run — least of all the
    safety-net tie at startup, which runs before the run's own budget exists.
    Override with ``PXX_GIT_TIMEOUT`` (positive, finite seconds)."""
    raw = os.environ.get("PXX_GIT_TIMEOUT")
    if raw:
        try:
            value = float(raw)
        except ValueError:
            value = 0.0
        if value > 0 and math.isfinite(value):
            return value
    return _DEFAULT_GIT_TIMEOUT


async def communicate_bounded(
    proc: asyncio.subprocess.Process,
    input_bytes: bytes | None = None,
    *,
    label: str = "",
) -> tuple[bytes, bytes | None]:
    """``proc.communicate()`` bounded by :func:`git_timeout`. On timeout the child
    is KILLED and reaped — cancelling ``communicate()`` alone leaves the process
    running and its transport abandoned — then :class:`TimeoutError` is re-raised
    so the caller degrades (a git subprocess must not outlive the bound)."""
    timeout = git_timeout()
    try:
        return await asyncio.wait_for(proc.communicate(input_bytes), timeout=timeout)
    except TimeoutError:
        # kill + reap. The child may have exited between the timeout and here, so
        # both kill() and wait() can hit ProcessLookupError — swallow it (the
        # process is already gone, which is the outcome we wanted) and preserve
        # the TimeoutError path.
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await proc.wait()  # reap the child; never leak the transport
        except ProcessLookupError:
            pass
        log.warning("git %s timed out after %.0fs — killed", label or "command", timeout)
        raise
