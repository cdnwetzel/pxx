"""Tests for pxx.cli pure functions.

Covers the three module-level helpers that have no I/O dependencies on
aider or Ollama: model_for, _in_git_repo, _find_aider.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from pxx.cli import (
    NEO_DEFAULT,
    STUDIO_DEFAULT,
    _find_aider,
    _in_git_repo,
    model_for,
)
from pxx.endpoints import Endpoint


class TestModelFor:
    def test_neo_endpoint_returns_neo_default(self, monkeypatch):
        monkeypatch.delenv("PXX_MODEL", raising=False)
        assert model_for(Endpoint("neo", "http://localhost:11434")) == NEO_DEFAULT

    def test_studio_endpoint_returns_studio_default(self, monkeypatch):
        monkeypatch.delenv("PXX_MODEL", raising=False)
        assert model_for(Endpoint("studio_lan", "http://x:11434")) == STUDIO_DEFAULT

    def test_studio_remote_endpoint_returns_studio_default(self, monkeypatch):
        monkeypatch.delenv("PXX_MODEL", raising=False)
        assert model_for(Endpoint("studio_remote", "http://x:11434")) == STUDIO_DEFAULT

    def test_override_endpoint_returns_studio_default(self, monkeypatch):
        # documented behavior: override endpoint inherits Studio default
        monkeypatch.delenv("PXX_MODEL", raising=False)
        assert model_for(Endpoint("override", "http://x:11434")) == STUDIO_DEFAULT

    def test_pxx_model_env_overrides_all_endpoints(self, monkeypatch):
        monkeypatch.setenv("PXX_MODEL", "ollama_chat/custom")
        for name in ("neo", "studio_lan", "studio_remote", "override"):
            assert model_for(Endpoint(name, "http://x:11434")) == "ollama_chat/custom"


class TestInGitRepo:
    def test_inside_git_repo(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        subprocess.run(["git", "init", "-q"], check=True)
        assert _in_git_repo() is True

    def test_outside_git_repo(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert _in_git_repo() is False


class TestFindAider:
    def test_returns_existing_aider_in_same_venv(self):
        # When pytest runs in pxx's dev venv, aider is installed alongside it
        # because aider-chat is a runtime dep of pxx.
        found = _find_aider()
        assert Path(found).exists()
        assert Path(found).name == "aider"
