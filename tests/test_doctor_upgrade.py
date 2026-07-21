"""Tests for pxx.doctor and pxx.upgrade — no network (PyPI mocked)."""

from __future__ import annotations

import asyncio
import sys

import httpx

import pxx.upgrade as upgrade_mod
from pxx.config import Settings
from pxx.doctor import Check, print_report, run_doctor
from pxx.upgrade import UpgradeResult, _is_newer, _version_tuple, detect_install_method


def _settings(tmp_path) -> Settings:
    return Settings(memory_dir=tmp_path / "mem", state_dir=tmp_path / "state")


# ---------------------------------------------------------------------------
# doctor


def test_doctor_hard_checks_pass(tmp_path):
    checks = asyncio.run(run_doctor(_settings(tmp_path), cwd=tmp_path))
    by_name = {c.name: c for c in checks}
    assert by_name["python"].ok
    assert by_name["python"].hard
    assert by_name["config"].ok
    assert by_name["memory_dir"].ok
    assert by_name["state_dir"].ok
    # dirs were created by the writability probes
    assert (tmp_path / "mem").is_dir()
    assert (tmp_path / "state").is_dir()


def test_doctor_reports_loaded_config_files(tmp_path):
    (tmp_path / "pxx.toml").write_text('model = "qwen2.5-coder:7b"\n')
    checks = asyncio.run(run_doctor(_settings(tmp_path), cwd=tmp_path))
    config = next(c for c in checks if c.name == "config")
    assert config.ok and "pxx.toml" in config.detail


def test_doctor_unwritable_dir_is_hard_failure(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file")
    settings = Settings(memory_dir=blocker / "mem", state_dir=tmp_path / "state")
    checks = asyncio.run(run_doctor(settings, cwd=tmp_path))
    mem = next(c for c in checks if c.name == "memory_dir")
    assert not mem.ok and mem.hard
    assert not print_report(checks)


def test_doctor_endpoints_soft_failure_no_crash(tmp_path):
    # router may not exist yet (parallel group) or endpoints are unreachable;
    # either way doctor must not crash and must report soft failures only.
    checks = asyncio.run(run_doctor(_settings(tmp_path), cwd=tmp_path))
    endpoint_checks = [c for c in checks if c.name.startswith("endpoint")]
    assert endpoint_checks, "expected at least one endpoint check"
    assert all(not c.hard for c in endpoint_checks)
    assert print_report(checks)  # soft failures don't fail the report


def test_doctor_binary_checks_soft(tmp_path):
    checks = asyncio.run(run_doctor(_settings(tmp_path), cwd=tmp_path))
    for tool in ("aider", "git", "rg"):
        check = next(c for c in checks if c.name == f"binary:{tool}")
        assert not check.hard


def test_print_report_icons(capsys):
    checks = [
        Check("hard_ok", True, "fine", hard=True),
        Check("hard_bad", False, "broken", hard=True),
        Check("soft_bad", False, "meh", hard=False),
    ]
    assert print_report(checks) is False
    out = capsys.readouterr().out
    assert "✅ hard_ok" in out and "❌ hard_bad" in out and "⚠️ soft_bad" in out


def test_print_report_all_good(capsys):
    assert print_report([Check("a", True, "ok", hard=True), Check("b", False, "x")]) is True


# ---------------------------------------------------------------------------
# upgrade: version comparison


def test_version_tuple():
    assert _version_tuple("2.0.0") == (2, 0, 0)
    assert _version_tuple("2.1") == (2, 1, 0)
    assert _is_newer("2.0.1", "2.0.0")
    assert not _is_newer("2.0.0", "2.0.0")
    assert not _is_newer("1.9.9", "2.0.0")


# ---------------------------------------------------------------------------
# upgrade: install method detection


def test_detect_editable_in_this_checkout():
    # the repo has .git next to the package -> editable
    assert detect_install_method() == "editable"


def test_detect_uv_tool(monkeypatch, tmp_path):
    fake_pkg = tmp_path / "site-packages" / "pxx"
    fake_pkg.mkdir(parents=True)
    monkeypatch.setattr(upgrade_mod, "__file__", str(fake_pkg / "upgrade.py"))
    monkeypatch.setattr(
        sys,
        "prefix",
        "/home/u/.local/share/uv/tools/pxx-orchestrator",  # pxx: allow home-path
    )
    assert detect_install_method() == "uv"


def test_detect_pipx(monkeypatch, tmp_path):
    fake_pkg = tmp_path / "site-packages" / "pxx"
    fake_pkg.mkdir(parents=True)
    monkeypatch.setattr(upgrade_mod, "__file__", str(fake_pkg / "upgrade.py"))
    monkeypatch.setattr(
        sys,
        "prefix",
        "/home/u/.local/pipx/venvs/pxx-orchestrator",  # pxx: allow home-path
    )
    assert detect_install_method() == "pipx"


def test_detect_pip(monkeypatch, tmp_path):
    fake_pkg = tmp_path / "site-packages" / "pxx"
    fake_pkg.mkdir(parents=True)
    monkeypatch.setattr(upgrade_mod, "__file__", str(fake_pkg / "upgrade.py"))
    monkeypatch.setattr(sys, "prefix", "/usr/local")
    assert detect_install_method() == "pip"


# ---------------------------------------------------------------------------
# upgrade: latest_version with mocked transport (no network)


def _mock_async_client(monkeypatch, version: str):
    def handler(request):
        return httpx.Response(200, json={"info": {"version": version}})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        upgrade_mod.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), timeout=5),
    )


def test_latest_version(monkeypatch):
    _mock_async_client(monkeypatch, "9.9.9")
    assert asyncio.run(upgrade_mod.latest_version()) == "9.9.9"


# ---------------------------------------------------------------------------
# upgrade: full flow


def test_upgrade_refuses_editable():
    result = asyncio.run(upgrade_mod.upgrade())
    assert result.status == "refused"
    assert "editable" in result.message


def test_upgrade_current(monkeypatch):
    monkeypatch.setattr(upgrade_mod, "detect_install_method", lambda: "pip")

    async def fake_latest():
        return upgrade_mod.__version__

    monkeypatch.setattr(upgrade_mod, "latest_version", fake_latest)
    result = asyncio.run(upgrade_mod.upgrade())
    assert result.status == "current"


def test_upgrade_runs_command(monkeypatch):
    monkeypatch.setattr(upgrade_mod, "detect_install_method", lambda: "pipx")
    _mock_async_client(monkeypatch, "99.0.0")
    ran: list[list[str]] = []

    async def fake_run(argv):
        ran.append(argv)
        return 0, "ok"

    monkeypatch.setattr(upgrade_mod, "_run_command", fake_run)
    result = asyncio.run(upgrade_mod.upgrade())
    assert result.status == "updated"
    assert ran == [["pipx", "upgrade", "pxx-orchestrator"]]
    assert "99.0.0" in result.message


def test_upgrade_command_failure(monkeypatch):
    monkeypatch.setattr(upgrade_mod, "detect_install_method", lambda: "uv")
    _mock_async_client(monkeypatch, "99.0.0")

    async def fake_run(argv):
        return 1, "boom"

    monkeypatch.setattr(upgrade_mod, "_run_command", fake_run)
    result = asyncio.run(upgrade_mod.upgrade())
    assert result.status == "error"
    assert "boom" in result.message


def test_upgrade_pypi_unreachable(monkeypatch):
    monkeypatch.setattr(upgrade_mod, "detect_install_method", lambda: "pip")

    async def fake_latest():
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(upgrade_mod, "latest_version", fake_latest)
    result = asyncio.run(upgrade_mod.upgrade())
    assert result.status == "error"
    assert isinstance(result, UpgradeResult)
