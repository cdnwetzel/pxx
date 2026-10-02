"""Suite-wide fixtures.

The GIT_* scrub mirrors ``pxx.gitenv``: git exports repo-targeting and
identity variables into hooks, so a suite run under ``git commit`` (the
pre-commit test gate) inherits them — and every test helper that shells
out to git in a tmp scaffold would silently target the *hook caller's*
repository instead (2026-07-26: a leaked ``GIT_DIR`` staged deletion of
every tracked file in the real repo). ``pxx.gitenv.git_env()`` protects
pxx's own subprocesses; this fixture protects the tests' direct ``git``
calls the same way.

The operator-config scrub is the same lesson one layer up: ``load_settings``
reads ``~/.config/pxx/config.toml``, ``~/.config/pxx/env`` and ``PXX_*``
variables, so a suite run on a machine where the operator has a real pxx
config inherits that operator's hooks, model routes and gate settings. On
such a host ``test_repo_local_hooks_are_not_honored`` failed because the
hooks it saw were the operator's, not the repo's. CI has none of these, which
is exactly why the leak was invisible there. Every test starts with no user
config, no env file and no ``PXX_*`` variable; a test that wants one sets it
with ``monkeypatch`` as the config tests already do.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pxx.gitenv import SCRUBBED_GIT_VARS


@pytest.fixture(autouse=True)
def _scrub_inherited_git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in SCRUBBED_GIT_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _isolate_operator_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No test reads the operator's pxx config, env file or ``PXX_*`` variables."""
    absent = tmp_path / "no-operator-config"
    monkeypatch.setattr("pxx.config._USER_CONFIG", absent / "config.toml")
    monkeypatch.setattr("pxx.config._USER_ENV", absent / "env")
    for var in [v for v in os.environ if v.startswith("PXX_")]:
        monkeypatch.delenv(var, raising=False)
