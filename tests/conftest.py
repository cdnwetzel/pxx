"""Shared pytest fixtures for the pxx test suite."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# Env vars that leak across tests. Two channels:
#   1. ``pxx/__init__`` loads ``~/.config/pxx/env`` into ``os.environ`` at
#      import time (``os.environ.setdefault``), so on a dev machine every
#      machine-local ``PXX_*`` setting (endpoints, models, review backend)
#      is ambient for the whole test session.
#   2. ``cli.main()`` / the self-fix path write ``PXX_*`` vars directly onto
#      ``os.environ`` just before handing off to aider, leaking them into
#      later tests — notably the pre-commit-hook integration tests, which
#      read the ambient environment.
# Sweeping by prefix (not a fixed tuple) keeps the suite hermetic as new
# ``PXX_*`` settings are added.


@pytest.fixture(autouse=True)
def _isolate_pxx_env_vars():
    """Snapshot and restore all ``PXX_*`` env vars around every test.

    Explicit save/restore (not monkeypatch.delenv) because one leak channel
    is production code writing os.environ directly — monkeypatch only tracks
    keys it touched, so it wouldn't undo a test's direct mutation.
    """
    saved = {k: v for k, v in os.environ.items() if k.startswith("PXX_")}
    for k in saved:
        os.environ.pop(k, None)
    try:
        yield
    finally:
        for k in [k for k in os.environ if k.startswith("PXX_")]:
            os.environ.pop(k, None)
        os.environ.update(saved)


@pytest.fixture(autouse=True)
def _redirect_xdg_state(monkeypatch: pytest.MonkeyPatch, tmp_path_factory) -> Path:
    """Redirect ``$XDG_STATE_HOME`` to a per-session tmp dir for every test.

    Without this, any test that exercises ``pxx.audit.write_session_start``
    (directly or via ``main()``) would pollute the developer's real
    ``~/.local/state/pxx/sessions/`` directory. Tests that need to assert
    on the log file can override the fixture or use ``audit.log_dir()``
    after this fixture has resolved it to the tmp dir.

    Also redirects ``$XDG_CONFIG_HOME``: ``pxx.scope`` / ``pxx.governance``
    resolve user config (``trusted-paths`` et al.) via it, so a dev machine's
    real ``~/.config/pxx/`` would otherwise leak gates and settings into
    tests that call ``main()``.

    Returns the redirected log dir for tests that want to inspect it.
    """
    state_root = tmp_path_factory.mktemp("xdg_state")
    monkeypatch.setenv("XDG_STATE_HOME", str(state_root))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("xdg_config")))
    return state_root / "pxx" / "sessions"
