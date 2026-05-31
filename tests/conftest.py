"""Shared pytest fixtures for the pxx test suite."""

from __future__ import annotations

from pathlib import Path

import pytest


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
