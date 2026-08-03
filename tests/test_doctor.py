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


#: A 200 body carrying a structured tool call (the healthy result).
TOOL_CALL_BODY = {
    "choices": [
        {
            "message": {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": '{"path": "README.md"}'},
                    }
                ],
            }
        }
    ]
}
#: A 200 body where the model answered in prose instead of calling the tool.
PROSE_BODY = {
    "choices": [{"message": {"role": "assistant", "content": "Sure — I'd open README.md and…"}}]
}


def test_tool_call_under_realistic_context_reports_ok(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "http://test.local/v1/chat/completions"
        body = request.read().decode()
        # The probe must carry a realistic context, not a toy "ping": the real
        # system prompt, a tool definition, and tool_choice=auto (F2).
        assert '"tool_choice": "auto"' in body or '"tool_choice":"auto"' in body
        assert "read_file" in body
        assert '"role": "system"' in body or '"role":"system"' in body
        return httpx.Response(200, json=TOOL_CALL_BODY)

    mock_client(monkeypatch, handler)
    check = asyncio.run(_tool_calling_check(SPEC))
    assert check is not None
    assert check.ok and not check.hard
    assert "verified under a realistic context" in check.detail


def test_prose_under_realistic_context_is_f2_warning(monkeypatch):
    # The endpoint accepts `tools` and returns 200, but the model answered in
    # prose — the exact F2 degradation a toy probe would have called "supported".
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=PROSE_BODY)

    mock_client(monkeypatch, handler)
    check = asyncio.run(_tool_calling_check(SPEC))
    assert check is not None
    assert not check.ok and not check.hard  # warning, never a doctor failure
    assert "PROSE" in check.detail
    assert "F2" in check.detail


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


def test_unparseable_200_body_is_a_warning(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    mock_client(monkeypatch, handler)
    check = asyncio.run(_tool_calling_check(SPEC))
    assert check is not None
    assert not check.ok and not check.hard
    assert "unparseable" in check.detail


def test_ollama_is_probed_not_skipped(monkeypatch):
    # F2: ollama is exactly where small instruct models accept `tools` but
    # prose out under load — so it must be probed, not skipped.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=TOOL_CALL_BODY)

    mock_client(monkeypatch, handler)
    check = asyncio.run(_tool_calling_check(ModelRef(provider="ollama", model="qwen2.5-coder:7b")))
    assert check is not None
    assert check.ok
    assert "verified under a realistic context" in check.detail


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


# --- model presence on reachable endpoints ----------------------------------


def _fake_probe(endpoints):
    async def probe(specs, timeout=1.0):  # noqa: ASYNC109 - mirrors probe_endpoints
        return endpoints

    return probe


def _endpoint_checks_with(monkeypatch, spec, endpoint):
    import pxx.router
    from pxx.config import Settings
    from pxx.doctor import _endpoint_checks

    monkeypatch.setattr(pxx.router, "probe_endpoints", _fake_probe([endpoint]))
    settings = Settings(model=spec)
    return asyncio.run(_endpoint_checks(settings))


def test_model_absent_on_multi_model_endpoint_is_flagged(monkeypatch):
    from pxx.router import Endpoint

    spec = ModelRef(provider="ollama", model="qwen2.5-coder:7b")  # ollama: no tool probe
    ep = Endpoint(provider="ollama", base_url="http://x", models=("a", "b"), reachable=True)
    checks = _endpoint_checks_with(monkeypatch, spec, ep)
    model_checks = [c for c in checks if c.name == "model:qwen2.5-coder:7b"]
    assert model_checks and not model_checks[0].ok
    assert "not served" in model_checks[0].detail


def test_model_absent_on_single_model_endpoint_notes_autocorrect(monkeypatch):
    from pxx.router import Endpoint

    spec = ModelRef(provider="ollama", model="missing")
    ep = Endpoint(provider="ollama", base_url="http://x", models=("only",), reachable=True)
    checks = _endpoint_checks_with(monkeypatch, spec, ep)
    model_checks = [c for c in checks if c.name == "model:missing"]
    assert model_checks and model_checks[0].ok
    assert "auto-correct" in model_checks[0].detail


def test_model_present_adds_no_extra_check(monkeypatch):
    from pxx.router import Endpoint

    spec = ModelRef(provider="ollama", model="served")
    ep = Endpoint(provider="ollama", base_url="http://x", models=("served",), reachable=True)
    checks = _endpoint_checks_with(monkeypatch, spec, ep)
    assert not [c for c in checks if c.name.startswith("model:")]


def test_ollama_endpoint_with_no_models_is_flagged(monkeypatch):
    from pxx.router import Endpoint

    spec = ModelRef(provider="ollama", model="qwen2.5-coder:7b")
    ep = Endpoint(provider="ollama", base_url="http://x", models=(), reachable=True)
    checks = _endpoint_checks_with(monkeypatch, spec, ep)
    model_checks = [c for c in checks if c.name.startswith("model:")]
    assert model_checks and not model_checks[0].ok
    assert "ollama pull" in model_checks[0].detail


def test_run_doctor_flags_broken_aider(monkeypatch, tmp_path):
    import pxx.doctor as doctor_mod
    from pxx.config import Settings
    from pxx.doctor import run_doctor

    monkeypatch.setattr(doctor_mod.shutil, "which", lambda tool: f"/fake/{tool}")
    monkeypatch.setattr("pxx.cli._aider_health", lambda path: False)

    async def no_endpoints(settings):
        return []

    monkeypatch.setattr(doctor_mod, "_endpoint_checks", no_endpoints)
    settings = Settings(memory_dir=tmp_path / "m", state_dir=tmp_path / "s")
    checks = asyncio.run(run_doctor(settings, cwd=tmp_path))
    aider_checks = [c for c in checks if c.name == "binary:aider"]
    assert aider_checks and not aider_checks[0].ok
    assert "broken" in aider_checks[0].detail
