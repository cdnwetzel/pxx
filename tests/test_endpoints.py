"""Tests for pxx.endpoints — endpoint probing and detection."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from pxx.endpoints import Endpoint, _probe, _probe_ollama, _probe_vllm, detect_endpoint


class TestProbe:
    # regression: empty url
    def test_empty_url_returns_false(self):
        assert _probe("") is False

    def test_unreachable_port_returns_false(self):
        # Port 1 is reserved; nothing will be listening.
        assert _probe("http://localhost:1") is False

    def test_rejects_non_ollama_json(self, monkeypatch):
        # A 200 response that isn't Ollama-shaped should still fail the probe.
        import io

        class _Ctx:
            def __init__(self, data: bytes):
                self.body = io.BytesIO(data)

            def __enter__(self):
                return self.body

            def __exit__(self, *args):
                pass

        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda *a, **kw: _Ctx(b'{"something_else": []}'),
        )
        assert _probe("http://x:11434") is False

    def test_rejects_non_json_response(self, monkeypatch):
        import io

        class _Ctx:
            def __init__(self, data: bytes):
                self.body = io.BytesIO(data)

            def __enter__(self):
                return self.body

            def __exit__(self, *args):
                pass

        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda *a, **kw: _Ctx(b"<html>not ollama</html>"),
        )
        assert _probe("http://x:11434") is False

    def test_accepts_valid_ollama_response(self, monkeypatch):
        import io

        class _Ctx:
            def __init__(self, data: bytes):
                self.body = io.BytesIO(data)

            def __enter__(self):
                return self.body

            def __exit__(self, *args):
                pass

        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda *a, **kw: _Ctx(b'{"models": [{"name": "qwen3:4b"}]}'),
        )
        assert _probe("http://x:11434") is True


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
        monkeypatch.setattr("pxx.endpoints._probe_ollama", lambda url: False)
        monkeypatch.setattr("pxx.endpoints._probe_vllm", lambda url: False)
        with pytest.raises(RuntimeError, match="No Ollama or vLLM endpoint reachable"):
            detect_endpoint()

    def test_first_reachable_candidate_wins(self, monkeypatch):
        monkeypatch.delenv("PXX_OLLAMA_BASE", raising=False)
        monkeypatch.setenv("PXX_STUDIO_LAN_URL", "http://studio-lan-fake:11434")
        monkeypatch.setenv("PXX_STUDIO_REMOTE_URL", "http://studio-remote-fake:11434")
        # Make the LAN probe succeed; the others should not even be called.
        monkeypatch.setattr(
            "pxx.endpoints._probe_vllm",
            lambda url: False,
        )
        monkeypatch.setattr(
            "pxx.endpoints._probe_ollama",
            lambda url: url == "http://studio-lan-fake:11434",
        )
        result = detect_endpoint()
        assert result.name == "studio_lan"
        assert result.url == "http://studio-lan-fake:11434"


class TestDetectEndpointTierPreference:
    def test_preferred_backend_ollama_tries_ollama_first(self, monkeypatch):
        monkeypatch.delenv("PXX_OLLAMA_BASE", raising=False)
        monkeypatch.setattr("pxx.endpoints._probe_ollama", lambda url: True)
        monkeypatch.setattr("pxx.endpoints._probe_vllm", lambda url: True)
        result = detect_endpoint(preferred_backend="ollama")
        # Should pick Ollama candidate (studio_lan) not vLLM
        assert result.backend == "ollama"

    def test_preferred_backend_vllm_tries_vllm_first(self, monkeypatch):
        monkeypatch.delenv("PXX_OLLAMA_BASE", raising=False)
        monkeypatch.setattr("pxx.endpoints._probe_vllm", lambda url: True)
        monkeypatch.setattr("pxx.endpoints._probe_ollama", lambda url: True)
        result = detect_endpoint(preferred_backend="vllm")
        # Should pick vLLM candidate
        assert result.backend == "vllm"

    def test_no_preferred_backend_defaults_to_vllm_first(self, monkeypatch):
        monkeypatch.delenv("PXX_OLLAMA_BASE", raising=False)
        monkeypatch.setattr("pxx.endpoints._probe_vllm", lambda url: True)
        monkeypatch.setattr("pxx.endpoints._probe_ollama", lambda url: False)
        result = detect_endpoint(preferred_backend=None)
        # Default behavior: vLLM first
        assert result.backend == "vllm"


class TestEndpointDataclass:
    def test_endpoint_is_frozen(self):
        ep = Endpoint("test", "http://x")
        with pytest.raises(FrozenInstanceError):
            ep.name = "changed"  # type: ignore[misc]
