"""Tests for pxx.doctor — httpx.MockTransport, no network."""

from __future__ import annotations

import asyncio
import json

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
        # The probe must carry a realistic context, not a toy "ping": the real
        # system prompt, a tool definition, and tool_choice=auto (F2). Assert on
        # the decoded structure, not raw-text substrings (serialization-agnostic).
        payload = json.loads(request.read())
        assert payload["tool_choice"] == "auto"
        assert any(t["function"]["name"] == "read_file" for t in payload["tools"])
        roles = [m["role"] for m in payload["messages"]]
        assert "system" in roles and "user" in roles
        # The system message carries real instruction load, not a one-liner.
        system = next(m["content"] for m in payload["messages"] if m["role"] == "system")
        assert len(system) > 100
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
    assert "answered in prose" in check.detail
    assert "F2" in check.detail


def test_empty_message_under_realistic_context_is_distinct_warning(monkeypatch):
    # 200 with neither tool_calls nor content — not "prose", a degenerate reply.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant"}}]})

    mock_client(monkeypatch, handler)
    check = asyncio.run(_tool_calling_check(SPEC))
    assert check is not None
    assert not check.ok and not check.hard
    assert "no tool call and no content" in check.detail
    assert "answered in prose" not in check.detail


def test_non_dict_message_is_a_warning(monkeypatch):
    # A parseable 200 whose message isn't an object must stay fail-soft, not raise.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": "oops"}]})

    mock_client(monkeypatch, handler)
    check = asyncio.run(_tool_calling_check(SPEC))
    assert check is not None
    assert not check.ok and not check.hard
    assert "unexpected 200 message shape" in check.detail


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


def test_probe_system_prompt_uses_real_native_prompt():
    # The probe must load the actual native system prompt (realistic load),
    # not silently fall back to the compact stub.
    from pxx.backends.native import load_system_prompt
    from pxx.doctor import _PROBE_FALLBACK_SYSTEM, _probe_system_prompt

    prompt = _probe_system_prompt()
    assert prompt == load_system_prompt()
    assert prompt != _PROBE_FALLBACK_SYSTEM  # the real resource, not the stub
    assert len(prompt) > 100


def test_probe_system_prompt_falls_back_and_logs(monkeypatch, caplog):
    # If the native prompt can't be imported/read, the probe degrades to the
    # compact stub AND leaves a diagnostic trail (never silent).
    import pxx.backends.native as native
    from pxx.doctor import _PROBE_FALLBACK_SYSTEM, _probe_system_prompt

    def boom():
        raise RuntimeError("resource unavailable")

    monkeypatch.setattr(native, "load_system_prompt", boom)
    with caplog.at_level("ERROR", logger="pxx.doctor"):
        assert _probe_system_prompt() == _PROBE_FALLBACK_SYSTEM
    assert "using fallback" in caplog.text


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


# --- review independence (author != reviewer) --------------------------------
#
# The default posture — no [roles.review] overlay — resolves the reviewer to the
# coder model, so `pxx run` blocks on a gate the same model can only self-approve.
# These lock the check to the behaviour, including the negative control that it
# actually FIRES on the bad case rather than passing vacuously.


def test_review_independence_ok_with_a_distinct_reviewer():
    from pxx.config import ModelRef, Settings
    from pxx.doctor import _review_independence_check

    settings = Settings(
        model=ModelRef(model="qwen3-coder:30b"),
        review_model=ModelRef(model="qwen2.5:14b-instruct-q4_k_m"),
    )
    check = _review_independence_check(settings)
    assert check.ok and not check.hard
    assert "qwen2.5:14b-instruct-q4_k_m" in check.detail


def test_review_independence_fires_on_the_default_self_review_posture():
    """Negative control: the bad case is the SHIPPED DEFAULT, so a check that
    cannot fail here would be worthless."""
    from pxx.config import ModelRef, Settings
    from pxx.doctor import _review_independence_check

    check = _review_independence_check(Settings(model=ModelRef(model="qwen2.5-coder:7b")))
    assert not check.ok  # fires
    assert not check.hard  # ...as a warning, not a doctor failure
    assert "SELF-review" in check.detail
    assert "qwen2.5-coder:7b" in check.detail
    assert "PXX_REVIEW_MODEL" in check.detail  # the warning names the fix


def test_review_independence_same_model_on_a_second_box_still_warns():
    """Separate hardware is not separate judgement: identical weights carry
    identical blind spots, so the two-box rig alone does not satisfy the
    invariant."""
    from pxx.config import ModelRef, Settings
    from pxx.doctor import _review_independence_check

    settings = Settings(
        model=ModelRef(model="qwen3-coder:30b", base_url="http://a.local"),
        review_model=ModelRef(model="qwen3-coder:30b", base_url="http://b.local"),
    )
    check = _review_independence_check(settings)
    assert not check.ok and not check.hard
    assert "separate endpoint" in check.detail
    assert "http://b.local" in check.detail


def test_review_independence_is_wired_into_run_doctor(monkeypatch, tmp_path):
    """A check nobody calls is the vacuous case for the check itself."""
    import pxx.doctor as doctor_mod
    from pxx.config import Settings
    from pxx.doctor import run_doctor

    async def no_endpoints(settings):
        return []

    monkeypatch.setattr(doctor_mod, "_endpoint_checks", no_endpoints)
    settings = Settings(memory_dir=tmp_path / "m", state_dir=tmp_path / "s")
    checks = asyncio.run(run_doctor(settings, cwd=tmp_path))
    named = [c for c in checks if c.name == "review:independence"]
    assert len(named) == 1 and not named[0].ok
