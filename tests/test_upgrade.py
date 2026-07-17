"""Tests for `pxx --upgrade` (pxx/upgrade.py). Pure helpers only — no network,
no real package mutation; `latest_version` is exercised with a stubbed opener."""

from __future__ import annotations

import io
import json

import pytest

from pxx import upgrade
from pxx.upgrade import InstallMethod


class TestDetectInstallMethod:
    def test_editable_flag_wins(self):
        assert (
            upgrade.detect_install_method("/anywhere/site-packages", editable=True)
            is InstallMethod.EDITABLE
        )

    def test_uv_tool_location(self):
        # Non-home roots: detection keys on the /uv/tools/ substring, and the
        # shipped-content gate flags home-directory paths in tracked tests.
        loc = "/opt/share/uv/tools/pxx-orchestrator/lib/python3.12/site-packages"
        assert (
            upgrade.detect_install_method(loc, editable=False) is InstallMethod.UV_TOOL
        )

    def test_pipx_location(self):
        loc = "/opt/pipx/venvs/pxx-orchestrator/lib/python3.12/site-packages"
        assert upgrade.detect_install_method(loc, editable=False) is InstallMethod.PIPX

    def test_plain_venv_falls_back_to_pip(self):
        loc = "/opt/proj/.venv/lib/python3.12/site-packages"
        assert upgrade.detect_install_method(loc, editable=False) is InstallMethod.PIP


class TestUpgradeCommand:
    def test_uv_tool(self):
        assert upgrade.upgrade_command(InstallMethod.UV_TOOL) == [
            "uv",
            "tool",
            "upgrade",
            "pxx-orchestrator",
        ]

    def test_pipx(self):
        assert upgrade.upgrade_command(InstallMethod.PIPX) == [
            "pipx",
            "upgrade",
            "pxx-orchestrator",
        ]

    def test_pip_uses_running_interpreter(self):
        import sys

        assert upgrade.upgrade_command(InstallMethod.PIP) == [
            sys.executable,
            "-m",
            "pip",
            "install",
            "-U",
            "pxx-orchestrator",
        ]

    def test_editable_has_no_command(self):
        assert upgrade.upgrade_command(InstallMethod.EDITABLE) is None


class TestNeedsUpgrade:
    def test_behind(self):
        assert upgrade.needs_upgrade("1.3.0", "1.3.1")

    def test_same(self):
        assert not upgrade.needs_upgrade("1.3.1", "1.3.1")

    def test_ahead(self):
        assert not upgrade.needs_upgrade("1.4.0", "1.3.1")

    def test_invalid_is_fail_safe(self):
        assert not upgrade.needs_upgrade("1.3.0", "not-a-version")


class TestLatestVersion:
    def test_parses_pypi_json(self, monkeypatch):
        payload = json.dumps({"info": {"version": "9.9.9"}}).encode()

        def fake_urlopen(url, timeout):  # noqa: ARG001
            return io.BytesIO(payload)

        monkeypatch.setattr(upgrade.urllib.request, "urlopen", fake_urlopen)
        assert upgrade.latest_version() == "9.9.9"

    def test_offline_returns_none(self, monkeypatch):
        def boom(url, timeout):  # noqa: ARG001
            raise upgrade.urllib.error.URLError("offline")

        monkeypatch.setattr(upgrade.urllib.request, "urlopen", boom)
        assert upgrade.latest_version() is None


def test_urlopen_stub_is_context_manager_compatible(monkeypatch):
    # latest_version uses `with urlopen(...)`; BytesIO supports the protocol,
    # so this guards the stub shape the tests above rely on.
    payload = json.dumps({"info": {"version": "1.0.0"}}).encode()
    monkeypatch.setattr(
        upgrade.urllib.request, "urlopen", lambda url, timeout: io.BytesIO(payload)
    )
    assert upgrade.latest_version(timeout=0.1) == "1.0.0"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
