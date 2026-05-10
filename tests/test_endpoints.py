"""Tests for pxx.endpoints — endpoint probing and detection."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from pxx.endpoints import Endpoint, _probe, detect_endpoint


class TestProbe:
    def test_empty_url_returns_false(self):
        assert _probe("") is False

    def test_unreachable_port_returns_false(self):
        # Port 1 is reserved; nothing will be listening.
        assert _probe("http://localhost:1") is False


class TestDetectEndpoint:
    def test_explicit_override_short_circuits(self, monkeypatch):
        # PXX_OLLAMA_BASE is taken without probing — even if unreachable.
        monkeypatch.setenv("PXX_OLLAMA_BASE", "http://example.invalid:9999")
        result = detect_endpoint()
        assert result.name == "override"
        assert result.url == "http://example.invalid:9999"

    def test_all_unreachable_raises(self, monkeypatch):
        monkeypatch.delenv("PXX_OLLAMA_BASE", raising=False)
        # Mock all probes to fail so we don't depend on whether local
        # Ollama is running during the test.
        monkeypatch.setattr("pxx.endpoints._probe", lambda url: False)
        with pytest.raises(RuntimeError, match="No Ollama endpoint reachable"):
            detect_endpoint()

    def test_first_reachable_candidate_wins(self, monkeypatch):
        monkeypatch.delenv("PXX_OLLAMA_BASE", raising=False)
        monkeypatch.setenv("PXX_STUDIO_LAN_URL", "http://studio-lan-fake:11434")
        monkeypatch.setenv("PXX_STUDIO_REMOTE_URL", "http://studio-remote-fake:11434")
        # Make the LAN probe succeed; the others should not even be called.
        monkeypatch.setattr(
            "pxx.endpoints._probe",
            lambda url: url == "http://studio-lan-fake:11434",
        )
        result = detect_endpoint()
        assert result.name == "studio_lan"
        assert result.url == "http://studio-lan-fake:11434"


class TestEndpointDataclass:
    def test_endpoint_is_frozen(self):
        ep = Endpoint("test", "http://x")
        with pytest.raises(FrozenInstanceError):
            ep.name = "changed"  # type: ignore[misc]
