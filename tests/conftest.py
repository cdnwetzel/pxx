"""Suite-wide fixtures.

The GIT_* scrub mirrors ``pxx.gitenv``: git exports repo-targeting and
identity variables into hooks, so a suite run under ``git commit`` (the
pre-commit test gate) inherits them — and every test helper that shells
out to git in a tmp scaffold would silently target the *hook caller's*
repository instead (2026-07-26: a leaked ``GIT_DIR`` staged deletion of
every tracked file in the real repo). ``pxx.gitenv.git_env()`` protects
pxx's own subprocesses; this fixture protects the tests' direct ``git``
calls the same way.
"""

from __future__ import annotations

import pytest

from pxx.gitenv import SCRUBBED_GIT_VARS


@pytest.fixture(autouse=True)
def _scrub_inherited_git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in SCRUBBED_GIT_VARS:
        monkeypatch.delenv(var, raising=False)
