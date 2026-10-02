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

import pytest

from pxx.gitenv import SCRUBBED_GIT_VARS


@pytest.fixture(autouse=True)
def _scrub_inherited_git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in SCRUBBED_GIT_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _isolate_operator_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """No test reads the operator's pxx config, env file or ``PXX_*`` variables.

    The stand-in paths live in their own temp directory, not in the test's
    ``tmp_path``: tests that assert on the contents of ``tmp_path`` must not
    find this fixture's directories there."""
    base = tmp_path_factory.mktemp("operator-isolation")
    absent = base / "no-operator-config"
    monkeypatch.setattr("pxx.config._USER_CONFIG", absent / "config.toml")
    monkeypatch.setattr("pxx.config._USER_ENV", absent / "env")
    for var in [v for v in os.environ if v.startswith("PXX_")]:
        monkeypatch.delenv(var, raising=False)
    # The two paths that bypass those globals -- `governance.load_denylist`
    # reads ``Path.home()/.config/pxx/public-denylist`` and the state dir
    # honours XDG_STATE_HOME -- are closed the same way: an empty HOME under
    # tmp_path and no XDG overrides, so no test reads the operator's files.
    home = base / "no-operator-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("XDG_STATE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
        monkeypatch.delenv(var, raising=False)
