"""Unit tests for pxx.router (9router lifecycle management)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests

from pxx.router import NineRouterManager

# Capture the real Popen at import time. The lifecycle tests patch
# ``pxx.router.subprocess.Popen`` (the shared subprocess module), so inside
# those tests ``subprocess.Popen`` is itself a Mock and can't be used as a
# spec. This reference still points at the genuine class.
RealPopen = subprocess.Popen


@pytest.fixture
def temp_router_config(tmp_path: Path) -> Path:
    """Create a temporary 9router config file."""
    config = tmp_path / ".9router" / "config.yml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("providers: []")
    return config


def test_router_init_default_config() -> None:
    """Test NineRouterManager initializes with default config path."""
    manager = NineRouterManager()
    assert manager.config_path == Path.home() / ".9router" / "config.yml"
    assert manager.process is None
    assert manager.api_base == "http://127.0.0.1:20128"


def test_router_init_custom_config(temp_router_config: Path) -> None:
    """Test NineRouterManager initializes with custom config path."""
    manager = NineRouterManager(config_path=temp_router_config)
    assert manager.config_path == temp_router_config


@patch("pxx.router.subprocess.Popen")
@patch("pxx.router.requests.get")
def test_router_start_success(mock_get: Mock, mock_popen: Mock) -> None:
    """Test successful 9router startup and health check."""
    mock_proc = Mock(spec=RealPopen)
    mock_popen.return_value = mock_proc
    mock_get.return_value.status_code = 200

    manager = NineRouterManager()
    manager.start()

    assert manager.process == mock_proc
    mock_popen.assert_called_once()
    # Verify Popen was launched via the console-script entry point
    args, kwargs = mock_popen.call_args
    assert args[0] == ["nine-router"]


@patch("pxx.router.subprocess.Popen")
@patch.object(
    NineRouterManager,
    "_wait_for_ready",
    side_effect=TimeoutError("9router failed to start within timeout"),
)
def test_router_start_timeout(mock_wait: Mock, mock_popen: Mock) -> None:
    """Test a readiness timeout on every launch path surfaces as RuntimeError.

    TimeoutError subclasses OSError, so start() catches the readiness timeout
    on both the console-script and uv-run attempts and re-raises the aggregate
    RuntimeError("Failed to start 9router: ...").
    """
    mock_proc = Mock(spec=RealPopen)
    mock_popen.return_value = mock_proc

    manager = NineRouterManager()
    with pytest.raises(RuntimeError):
        manager.start()


@patch("pxx.router.subprocess.Popen")
def test_router_stop_graceful(mock_popen: Mock) -> None:
    """Test graceful 9router shutdown."""
    mock_proc = Mock(spec=RealPopen)
    mock_popen.return_value = mock_proc

    manager = NineRouterManager()
    manager.process = mock_proc
    manager.stop()

    mock_proc.terminate.assert_called_once()
    mock_proc.wait.assert_called_once_with(timeout=3)


@patch("pxx.router.subprocess.Popen")
def test_router_stop_kill_on_timeout(mock_popen: Mock) -> None:
    """Test 9router is killed if graceful shutdown times out."""
    mock_proc = Mock(spec=RealPopen)
    mock_proc.wait.side_effect = subprocess.TimeoutExpired("9router", 3)
    mock_popen.return_value = mock_proc

    manager = NineRouterManager()
    manager.process = mock_proc
    manager.stop()

    mock_proc.terminate.assert_called_once()
    mock_proc.kill.assert_called_once()


def test_router_stop_none_process() -> None:
    """Test stop() is safe when process is None."""
    manager = NineRouterManager()
    manager.stop()  # Should not raise


@patch("pxx.router.requests.get")
def test_router_get_usage_success(mock_get: Mock) -> None:
    """Test get_usage() returns router token stats."""
    mock_get.return_value.json.return_value = {
        "total_tokens": 5000,
        "total_cost": 0.05,
    }

    manager = NineRouterManager()
    result = manager.get_usage()

    assert result["total_tokens"] == 5000
    assert result["total_cost"] == 0.05
    mock_get.assert_called_once_with("http://127.0.0.1:20128/v1/usage", timeout=2)


@patch("pxx.router.requests.get")
def test_router_get_usage_timeout(mock_get: Mock) -> None:
    """Test get_usage() returns empty dict on timeout."""
    mock_get.side_effect = requests.ConnectionError("Connection timeout")

    manager = NineRouterManager()
    result = manager.get_usage()

    assert result == {}


@patch("pxx.router.requests.get")
def test_router_get_status_success(mock_get: Mock) -> None:
    """Test get_status() returns router provider status."""
    mock_get.return_value.json.return_value = {
        "active_provider": "openai",
        "fallback_chain": ["openai", "anthropic"],
    }

    manager = NineRouterManager()
    result = manager.get_status()

    assert result["active_provider"] == "openai"
    assert "fallback_chain" in result
    mock_get.assert_called_once_with("http://127.0.0.1:20128/v1/status", timeout=2)


@patch("pxx.router.requests.get")
def test_router_get_status_failure(mock_get: Mock) -> None:
    """Test get_status() returns empty dict on failure."""
    mock_get.side_effect = requests.ConnectionError("Connection error")

    manager = NineRouterManager()
    result = manager.get_status()

    assert result == {}
