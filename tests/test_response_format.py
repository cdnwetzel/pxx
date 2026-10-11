"""Tests for Settings.response_format — provider-enforced structured output
(2.7.0, ACP F-001).

``pxx ask --response-format schema.json`` sends an OpenAI-style
``response_format`` json_schema object in the native backend's
chat-completions payload. Ask-mode only, bare-mode only (refused at
Session.run entry before any request), no markdown fallback: invalid JSON or
finish_reason=length is a hard BackendError. Audit stamps stay body-free:
presence, canonical schema SHA-256, and schema name — never schema content
beyond metadata. Default UNSET: the payload is byte-identical to before.

No network, no Ollama: the backend is driven through httpx.MockTransport.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import httpx
import pytest
from test_bare import capture_handler, completion, make_backend, make_ctx

import pxx.cli as cli
from pxx.backends.mock import MockBackend
from pxx.config import ModelRef, Settings, load_settings
from pxx.errors import BackendError, ConfigError
from pxx.safety import PermissionMode
from pxx.session import Session

SCHEMA = {
    "title": "verification_v1",
    "type": "object",
    "properties": {
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "criterion": {"type": "string"},
                    "evidence": {"type": "string"},
                    "verdict": {"type": "string", "enum": ["PASS", "FAIL"]},
                },
                "required": ["criterion", "evidence", "verdict"],
                "additionalProperties": False,
            },
        },
        "limitations": {"type": "array", "items": {"type": "string"}, "minItems": 1},
    },
    "required": ["rows", "limitations"],
    "additionalProperties": False,
}

CANONICAL_SHA256 = hashlib.sha256(
    json.dumps(SCHEMA, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()


def _settings(response_format: dict | None, *, bare: bool = True) -> Settings:
    return Settings(
        model=ModelRef(provider="ollama", model="test-model", base_url="http://test.local"),
        bare=bare,
        response_format=response_format,
    )


# --- CLI flag + schema file loading ------------------------------------------


def test_ask_flag_loads_schema_into_overrides(tmp_path: Path) -> None:
    schema_file = tmp_path / "schema.json"
    schema_file.write_text(json.dumps(SCHEMA))
    args = cli._build_parser().parse_args(
        ["ask", "-m", "hi", "--response-format", str(schema_file)]
    )
    overrides = cli._cli_overrides(args, PermissionMode.ASK)
    assert overrides["response_format"] == SCHEMA


@pytest.mark.parametrize("command", ["edit", "plan", "run", "chat"])
def test_response_format_flag_rejected_outside_ask(command) -> None:
    """The flag is registered on the ask subparser only, and because the 1.x
    shim warns-and-ignores unknown flags, main() hard-refuses it on any other
    verb BEFORE parsing — no silently ignored structured-output request."""
    with pytest.raises(SystemExit):
        cli.main([command, "-m", "do it", "--response-format", "schema.json"])


def test_load_response_format_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read"):
        cli._load_response_format(str(tmp_path / "nope.json"))


def test_load_response_format_invalid_json(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(ConfigError, match="not valid JSON"):
        cli._load_response_format(str(bad))


def test_load_response_format_non_object(tmp_path: Path) -> None:
    arr = tmp_path / "arr.json"
    arr.write_text('["not", "an", "object"]')
    with pytest.raises(ConfigError, match="non-empty JSON object"):
        cli._load_response_format(str(arr))


def test_load_response_format_oversized(tmp_path: Path) -> None:
    big = tmp_path / "big.json"
    big.write_text('{"type": "object", "padding": "' + "x" * 70000 + '"}')
    with pytest.raises(ConfigError, match="cap"):
        cli._load_response_format(str(big))


def test_response_format_toml_key_rejected(tmp_path: Path) -> None:
    """TOML carries no response_format key — a schema is a FILE, resolved by
    the CLI. Config is fail-closed on unknown keys."""
    (tmp_path / "pxx.toml").write_text('model = "x"\nresponse_format = "schema.json"\n')
    with pytest.raises(ConfigError, match="unknown"):
        load_settings(cwd=tmp_path)


def test_cli_override_reaches_settings(tmp_path: Path) -> None:
    """Regression (first 2.7.0 live probe, 2026-10-10): the flag parsed and
    the session ran, but _settings_from_dict had no response_format handler,
    so the override was SILENTLY DROPPED and the payload went out
    unstructured (the audit receipt honestly showed
    response_format_present=false — the receipt is what caught it)."""
    settings = load_settings(cwd=tmp_path, cli_overrides={"response_format": SCHEMA})
    assert settings.response_format == SCHEMA


# --- session refusal (before any request) --------------------------------------


def _session_settings(tmp_path: Path, mode: PermissionMode, *, bare: bool) -> Settings:
    return Settings(
        model=ModelRef(provider="ollama", model="test-model", base_url="http://test.local"),
        permission=mode,
        memory_enabled=False,
        memory_dir=tmp_path / "mem",
        state_dir=tmp_path / "state",
        bare=bare,
        response_format=SCHEMA,
    )


@pytest.mark.parametrize("mode", [PermissionMode.PLAN, PermissionMode.EDIT, PermissionMode.AUTO])
def test_response_format_refused_outside_ask(tmp_path: Path, mode: PermissionMode) -> None:
    """response_format outside ask mode: ConfigError, no HTTP request."""
    called: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        called.append(request)
        return httpx.Response(200, json=completion("done"))

    session = Session(
        _session_settings(tmp_path, mode, bare=True), make_backend(handler), cwd=tmp_path
    )
    with pytest.raises(ConfigError, match="valid only for"):
        asyncio.run(session.run("say hello"))
    assert called == []


def test_response_format_requires_bare(tmp_path: Path) -> None:
    """ask mode without bare: ConfigError — the tools + response_format
    interaction is deliberately unsupported; no silent fallback."""
    session = Session(
        _session_settings(tmp_path, PermissionMode.ASK, bare=False),
        MockBackend([]),
        cwd=tmp_path,
    )
    with pytest.raises(ConfigError, match="requires bare mode"):
        asyncio.run(session.run("say hello"))


# --- payload + audit -----------------------------------------------------------


def _run_once(tmp_path: Path, settings: Settings) -> tuple[list[dict], list]:
    captured: list[dict] = []
    # Under response_format the reply MUST parse as JSON, so the mock answers
    # with a valid JSON body even for the unset case (harmless there).
    backend = make_backend(capture_handler(captured, response=completion('{"ok": true}')))
    ctx = make_ctx(tmp_path, bare=settings.bare, settings=settings)
    outcome = asyncio.run(backend.run("the task text", ctx))
    assert outcome.code.name == "COMPLETED"
    return captured, ctx.bus.history


def test_unset_response_format_keeps_payload_shape_unchanged(tmp_path: Path) -> None:
    captured, events = _run_once(tmp_path, _settings(None))
    assert "response_format" not in captured[0]
    for kind in ("prompt_rendered", "model_request"):
        event = next(e for e in events if e.kind == kind)
        assert event.data["response_format_present"] is False
        assert event.data["response_format_sha256"] is None
        assert event.data["json_schema_name"] is None


def test_set_response_format_is_sent_and_audited(tmp_path: Path) -> None:
    captured, events = _run_once(tmp_path, _settings(SCHEMA))
    rf = captured[0]["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["name"] == "verification_v1"  # schema title
    assert rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["schema"] == SCHEMA
    assert captured[0]["messages"] == [{"role": "user", "content": "the task text"}]
    assert "tools" not in captured[0]  # bare composition
    for kind in ("prompt_rendered", "model_request"):
        event = next(e for e in events if e.kind == kind)
        assert event.data["response_format_present"] is True
        assert event.data["response_format_sha256"] == CANONICAL_SHA256
        assert event.data["json_schema_name"] == "verification_v1"


def test_response_format_name_defaults_when_no_title(tmp_path: Path) -> None:
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
    captured, events = _run_once(tmp_path, _settings(schema))
    assert captured[0]["response_format"]["json_schema"]["name"] == "pxx_response"
    request = next(e for e in events if e.kind == "model_request")
    assert request.data["json_schema_name"] == "pxx_response"


def test_audit_metadata_stays_body_free(tmp_path: Path) -> None:
    _, events = _run_once(tmp_path, _settings(SCHEMA))
    blob = json.dumps([e.data for e in events])
    assert "the task text" not in blob
    assert "criterion" not in blob  # schema content is not audit metadata


# --- loud failure classes (no markdown fallback) -------------------------------


def _run_with_response(tmp_path: Path, response: httpx.Response):
    def handler(request: httpx.Request) -> httpx.Response:
        return response

    backend = make_backend(handler)
    ctx = make_ctx(tmp_path, bare=True, settings=_settings(SCHEMA))
    return asyncio.run(backend.run("the task text", ctx))


def test_finish_reason_length_is_a_hard_failure(tmp_path: Path) -> None:
    truncated = {
        "choices": [
            {
                "message": {"role": "assistant", "content": '{"rows": ['},
                "finish_reason": "length",
            }
        ]
    }
    with pytest.raises(BackendError, match="finish_reason=length"):
        _run_with_response(tmp_path, httpx.Response(200, json=truncated))


def test_invalid_json_content_is_a_hard_failure(tmp_path: Path) -> None:
    with pytest.raises(BackendError, match="not valid JSON"):
        _run_with_response(
            tmp_path, httpx.Response(200, json=completion("sure! here is your table: | a | b |"))
        )
