"""Tests for pxx.endpoints — endpoint probing and detection."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from pxx import endpoints
from pxx.endpoints import Endpoint, _probe, detect_endpoint


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


class TestProbeVllmRetry:
    """A busy-but-healthy vLLM can miss the 1s probe intermittently; a single
    miss must not drop the endpoint (that race misrouted live --loop edits)."""

    def _ctx(self, data: bytes):
        import io

        class _Ctx:
            def __enter__(self_):
                return io.BytesIO(data)

            def __exit__(self_, *a):
                pass

        return _Ctx()

    def test_transient_blip_then_success_returns_true(self, monkeypatch):
        from pxx import endpoints

        monkeypatch.setattr(endpoints.time, "sleep", lambda *_: None)
        calls = {"n": 0}

        def flaky(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise TimeoutError("blip")
            return self._ctx(b'{"data": [{"id": "m"}]}')

        monkeypatch.setattr("urllib.request.urlopen", flaky)
        assert endpoints._probe_vllm("http://gpu-node-1:8003") is True
        assert calls["n"] == 2  # retried past the first miss

    def test_all_attempts_fail_returns_false(self, monkeypatch):
        from pxx import endpoints

        monkeypatch.setattr(endpoints.time, "sleep", lambda *_: None)
        calls = {"n": 0}

        def always_fail(*a, **kw):
            calls["n"] += 1
            raise TimeoutError("down")

        monkeypatch.setattr("urllib.request.urlopen", always_fail)
        assert endpoints._probe_vllm("http://gpu-node-1:8003") is False
        assert calls["n"] == endpoints.PROBE_RETRIES  # exhausted retries


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


class TestVllmCandidateList:
    def test_single_url_keeps_legacy_name_and_default_model(self, monkeypatch):
        monkeypatch.setenv("PXX_VLLM_URL", "http://vllm-host-1:8001")
        monkeypatch.delenv("PXX_VLLM_MODEL", raising=False)
        (ep,) = endpoints._vllm_candidates()
        assert ep.name == "m1_vllm"
        assert ep.url == "http://vllm-host-1:8001"
        assert ep.model is None

    def test_comma_list_probed_in_order_first_reachable_wins(self, monkeypatch):
        monkeypatch.delenv("PXX_OLLAMA_BASE", raising=False)
        monkeypatch.setenv("PXX_VLLM_URL", "http://vllm-host-1:8001, http://127.0.0.1:8003")
        monkeypatch.setenv(
            "PXX_VLLM_MODEL", "openai/Qwen3-Coder, openai/qwen2.5-coder-14b"
        )
        first, second = endpoints._vllm_candidates()
        assert (first.url, first.model) == ("http://vllm-host-1:8001", "openai/Qwen3-Coder")
        assert (second.url, second.model) == (
            "http://127.0.0.1:8003",
            "openai/qwen2.5-coder-14b",
        )
        assert first.name == "vllm_vllm-host-1"
        assert second.name == "vllm_127_0_0_1"

        # vllm-host-1 down -> the second candidate is selected with its own model.
        monkeypatch.setattr(
            "pxx.endpoints._probe_vllm", lambda url: url == "http://127.0.0.1:8003"
        )
        monkeypatch.setattr("pxx.endpoints._probe_ollama", lambda url: False)
        result = detect_endpoint()
        assert result.url == "http://127.0.0.1:8003"
        assert result.model == "openai/qwen2.5-coder-14b"

    def test_model_list_shorter_than_urls_leaves_none(self, monkeypatch):
        monkeypatch.setenv("PXX_VLLM_URL", "http://a:1,http://b:2")
        monkeypatch.setenv("PXX_VLLM_MODEL", "openai/model-a")
        first, second = endpoints._vllm_candidates()
        assert first.model == "openai/model-a"
        assert second.model is None


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
