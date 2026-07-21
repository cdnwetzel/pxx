"""Unit tests for pxx.memory (agentmemory lifecycle management)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests

from pxx.memory import AgentmemoryManager

# Capture the real Popen at import time; the lifecycle tests patch
# pxx.memory.subprocess.Popen, which would otherwise make subprocess.Popen a
# Mock that can't be used as a spec.
RealPopen = subprocess.Popen


@pytest.fixture
def temp_memory_config(tmp_path: Path) -> Path:
    """Create a temporary agentmemory config file."""
    config = tmp_path / ".agentmemory" / ".env"
    config.parent.mkdir(parents=True, exist_ok=True)
    return config


def test_memory_init_default_config() -> None:
    """Test AgentmemoryManager initializes with default config path."""
    manager = AgentmemoryManager()
    assert manager.config_path == Path.home() / ".agentmemory" / ".env"
    assert manager.process is None
    assert manager.api_base == "http://127.0.0.1:3111"


def test_memory_init_custom_config(temp_memory_config: Path) -> None:
    """Test AgentmemoryManager initializes with custom config path."""
    manager = AgentmemoryManager(config_path=temp_memory_config)
    assert manager.config_path == temp_memory_config


def test_memory_ensure_config_creates_file(tmp_path: Path) -> None:
    """Test _ensure_config() creates config file if missing."""
    config = tmp_path / ".agentmemory" / ".env"
    AgentmemoryManager(config_path=config)

    assert config.exists()
    content = config.read_text()
    assert "EMBEDDING_PROVIDER=local" in content
    assert "AGENTMEMORY_AUTO_COMPRESS=true" in content


def test_memory_ensure_config_preserves_existing(tmp_path: Path) -> None:
    """Test _ensure_config() doesn't overwrite existing config."""
    config = tmp_path / ".agentmemory" / ".env"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("CUSTOM_VALUE=123")

    AgentmemoryManager(config_path=config)

    assert config.read_text() == "CUSTOM_VALUE=123"


@patch("pxx.memory.subprocess.Popen")
@patch("pxx.memory.requests.get")
def test_memory_start_success(mock_get: Mock, mock_popen: Mock) -> None:
    """Test successful agentmemory startup and health check."""
    mock_proc = Mock(spec=RealPopen)
    mock_popen.return_value = mock_proc
    mock_get.return_value.status_code = 200

    manager = AgentmemoryManager()
    manager.start()

    assert manager.process == mock_proc
    mock_popen.assert_called_once()
    # Verify Popen was called with ["agentmemory"]
    args, kwargs = mock_popen.call_args
    assert args[0] == ["agentmemory", "server"]


@patch("pxx.memory.subprocess.Popen")
@patch.object(
    AgentmemoryManager,
    "_wait_for_ready",
    side_effect=TimeoutError("agentmemory failed to start within timeout"),
)
def test_memory_start_timeout(mock_wait: Mock, mock_popen: Mock) -> None:
    """Test a readiness timeout on every launch path surfaces as RuntimeError.

    TimeoutError subclasses OSError, so start() catches it on both the
    console-script and uv-run attempts and re-raises the aggregate RuntimeError.
    """
    mock_proc = Mock(spec=RealPopen)
    mock_popen.return_value = mock_proc

    manager = AgentmemoryManager()
    with pytest.raises(RuntimeError):
        manager.start()


@patch("pxx.memory.subprocess.Popen")
def test_memory_stop_graceful(mock_popen: Mock) -> None:
    """Test graceful agentmemory shutdown."""
    mock_proc = Mock(spec=RealPopen)
    mock_popen.return_value = mock_proc

    manager = AgentmemoryManager()
    manager.process = mock_proc
    manager.stop()

    mock_proc.terminate.assert_called_once()
    mock_proc.wait.assert_called_once_with(timeout=3)


@patch("pxx.memory.subprocess.Popen")
def test_memory_stop_kill_on_timeout(mock_popen: Mock) -> None:
    """Test agentmemory is killed if graceful shutdown times out."""
    mock_proc = Mock(spec=RealPopen)
    mock_proc.wait.side_effect = subprocess.TimeoutExpired("agentmemory", 3)
    mock_popen.return_value = mock_proc

    manager = AgentmemoryManager()
    manager.process = mock_proc
    manager.stop()

    mock_proc.terminate.assert_called_once()
    mock_proc.kill.assert_called_once()


def test_memory_stop_none_process() -> None:
    """Test stop() is safe when process is None."""
    manager = AgentmemoryManager()
    manager.stop()  # Should not raise


@patch("pxx.memory.requests.get")
def test_memory_health_check_success(mock_get: Mock) -> None:
    """Test health_check() returns True when server responds."""
    mock_get.return_value.status_code = 200

    manager = AgentmemoryManager()
    result = manager.health_check()

    assert result is True
    mock_get.assert_called_once_with("http://127.0.0.1:3111/health", timeout=2)


@patch("pxx.memory.requests.get")
def test_memory_health_check_failure(mock_get: Mock) -> None:
    """Test health_check() returns False on connection error."""
    mock_get.side_effect = requests.ConnectionError("Connection error")

    manager = AgentmemoryManager()
    result = manager.health_check()

    assert result is False


@patch("pxx.memory.requests.get")
def test_memory_health_check_bad_status(mock_get: Mock) -> None:
    """Test health_check() returns False on non-200 status."""
    mock_get.return_value.status_code = 500

    manager = AgentmemoryManager()
    result = manager.health_check()

    assert result is False
