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

import os

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
