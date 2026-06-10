"""Shared pytest fixtures for the pxx test suite."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# Env vars that cli.main() / the self-fix path write directly onto os.environ
# (just before handing off to aider). In-process tests that call main() would
# otherwise leak them into later tests — notably the pre-commit-hook
# integration tests, which read the ambient environment.
_PXX_ENV_VARS = (
    "PXX_DIFF_CAP",
    "PXX_SCOPE",
    "PXX_ALLOW_BIG_DIFF",
    "PXX_AUTONOMOUS",
)


@pytest.fixture(autouse=True)
def _redirect_xdg_state(monkeypatch: pytest.MonkeyPatch, tmp_path_factory) -> Path:
    """Redirect ``$XDG_STATE_HOME`` to a per-session tmp dir for every test.

    Without this, any test that exercises ``pxx.audit.write_session_start``
    (directly or via ``main()``) would pollute the developer's real
    ``~/.local/state/pxx/sessions/`` directory. Tests that need to assert
    on the log file can override the fixture or use ``audit.log_dir()``
    after this fixture has resolved it to the tmp dir.

    Returns the redirected log dir for tests that want to inspect it.
    """
    state_root = tmp_path_factory.mktemp("xdg_state")
    monkeypatch.setenv("XDG_STATE_HOME", str(state_root))
    return state_root / "pxx" / "sessions"


@pytest.fixture(autouse=True)
def _isolate_pxx_env_vars():
    """Snapshot and restore the pxx env vars main() mutates, around every test.

    Explicit save/restore (not monkeypatch.delenv) because the leak comes from
    production code writing os.environ directly — monkeypatch only tracks keys
    it touched, so it wouldn't undo a test's direct mutation.
    """
    saved = {k: os.environ.get(k) for k in _PXX_ENV_VARS}
    for k in _PXX_ENV_VARS:
        os.environ.pop(k, None)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
