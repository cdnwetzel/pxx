"""Tests for pxx.doctor — httpx.MockTransport, no network."""

from __future__ import annotations

import asyncio

import httpx

import pxx.doctor
from pxx.config import ModelRef
from pxx.doctor import _tool_calling_check

SPEC = ModelRef(provider="vllm", model="devstral", base_url="http://test.local")

#: The exact vLLM 400 body when launched without tool-call flags (F8).
VLLM_400_BODY = {
    "error": {
        "message": '"auto" tool choice requires --enable-auto-tool-choice '
        "and --tool-call-parser to be set",
        "type": "BadRequestError",
        "param": None,
        "code": 400,
    }
}


def mock_client(monkeypatch, handler) -> None:
    monkeypatch.setattr(
        pxx.doctor,
        "_client_factory",
        lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def test_tool_capable_endpoint_reports_ok(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "http://test.local/v1/chat/completions"
        return httpx.Response(200, json={"choices": [], "usage": {"total_tokens": 1}})

    mock_client(monkeypatch, handler)
    check = asyncio.run(_tool_calling_check(SPEC))
    assert check is not None
    assert check.ok and not check.hard
    assert "tool calling supported" in check.detail


def test_vllm_without_tool_flags_reports_actionable_warning(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json=VLLM_400_BODY)

    mock_client(monkeypatch, handler)
    check = asyncio.run(_tool_calling_check(SPEC))
    assert check is not None
    assert not check.ok and not check.hard  # warning, never a doctor failure
    assert "tool calling is DISABLED" in check.detail
    assert "--enable-auto-tool-choice" in check.detail
    assert "--tool-call-parser" in check.detail


def test_connection_error_is_a_warning_not_a_failure(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    mock_client(monkeypatch, handler)
    check = asyncio.run(_tool_calling_check(SPEC))
    assert check is not None
    assert not check.ok and not check.hard
    assert "probe failed" in check.detail


def test_ollama_is_skipped():
    # Ollama endpoints support tool calling out of the box — no probe.
    assert asyncio.run(_tool_calling_check(ModelRef(provider="ollama"))) is None


def test_hook_coverage_warns_in_edit_mode_without_matching_hook():
    from pxx.config import Settings
    from pxx.doctor import _hook_coverage_check
    from pxx.safety import Hook, PermissionMode

    check = _hook_coverage_check(Settings(permission=PermissionMode.EDIT))
    assert not check.ok and not check.hard  # a warning line, never a failure
    assert "HOOKS_MISSING" in check.detail and "docs/CONFIG.md" in check.detail

    # a hook whose matcher misses run_shell does not cover it
    settings = Settings(
        permission=PermissionMode.EDIT,
        hooks=(Hook(event="PreToolUse", command="true", matcher="pytest"),),
    )
    assert not _hook_coverage_check(settings).ok


def test_hook_coverage_ok_with_matching_hook_and_other_modes():
    from pxx.config import Settings
    from pxx.doctor import _hook_coverage_check
    from pxx.safety import Hook, PermissionMode

    settings = Settings(
        permission=PermissionMode.EDIT,
        hooks=(Hook(event="PreToolUse", command="true", matcher="run_shell"),),
    )
    assert _hook_coverage_check(settings).ok
    # HOOKS_MISSING only applies to edit mode
    assert _hook_coverage_check(Settings(permission=PermissionMode.ASK)).ok
    assert _hook_coverage_check(Settings(permission=PermissionMode.AUTO)).ok
