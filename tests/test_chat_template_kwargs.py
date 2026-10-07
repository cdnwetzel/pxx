"""Tests for Settings.chat_template_kwargs — vLLM-style template switches.

Default UNSET (None): the key is not sent and the serving layer's default
applies, so an unconfigured box is byte-identical to before the key existed
(the golden default payload fixture in test_bare.py pins that). Set, the
native backend includes ``chat_template_kwargs`` in the chat-completions
payload and in the body-free ``model_request`` audit metadata. The key
carries no safety semantics — generation only, never
scope/permissions/budgets/hooks/routing — so it is honoured from every
config layer including repo-local.

No network, no Ollama: the backend is driven through httpx.MockTransport.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from test_bare import capture_handler, make_backend, make_ctx

from pxx.config import ModelRef, Settings, load_settings
from pxx.errors import ConfigError

# --- config parsing ----------------------------------------------------------


def test_chat_template_kwargs_defaults_unset(tmp_path: Path) -> None:
    (tmp_path / "pxx.toml").write_text('model = "x"\n')
    assert load_settings(cwd=tmp_path).chat_template_kwargs is None


@pytest.mark.parametrize(
    "text,want",
    [
        ("chat_template_kwargs = { enable_thinking = false }\n", {"enable_thinking": False}),
        ("chat_template_kwargs = {}\n", {}),
        (
            "[chat_template_kwargs]\nenable_thinking = false\nreasoning_effort = 2\n",
            {"enable_thinking": False, "reasoning_effort": 2},
        ),
        (
            'chat_template_kwargs = { mode = "fast", flags = ["a", "b"] }\n',
            {"mode": "fast", "flags": ["a", "b"]},
        ),
    ],
)
def test_chat_template_kwargs_valid_tables(tmp_path: Path, text: str, want: dict) -> None:
    (tmp_path / "pxx.toml").write_text(text)
    got = load_settings(cwd=tmp_path).chat_template_kwargs
    assert got == want and isinstance(got, dict)


@pytest.mark.parametrize(
    "text",
    [
        'chat_template_kwargs = "enable_thinking=false"\n',  # no string coercion
        "chat_template_kwargs = true\n",  # bool is not a table
        "chat_template_kwargs = 1\n",  # int is not a table
        "chat_template_kwargs = 2026-10-07\n",  # bare TOML date is not a table
    ],
)
def test_chat_template_kwargs_non_tables_rejected(tmp_path: Path, text: str) -> None:
    (tmp_path / "pxx.toml").write_text(text)
    with pytest.raises(ConfigError, match="chat_template_kwargs must be a table"):
        load_settings(cwd=tmp_path)


def test_chat_template_kwargs_non_json_values_rejected(tmp_path: Path) -> None:
    """A TOML datetime inside the table is valid TOML but not valid JSON —
    it must fail closed rather than blow up at request-serialization time."""
    (tmp_path / "pxx.toml").write_text("chat_template_kwargs = { when = 2026-10-07T09:30:00Z }\n")
    with pytest.raises(ConfigError, match="chat_template_kwargs must be JSON-serializable"):
        load_settings(cwd=tmp_path)


# --- payload + audit ----------------------------------------------------------


def _run_once(tmp_path: Path, settings: Settings) -> tuple[list[dict], list]:
    captured: list[dict] = []
    backend = make_backend(capture_handler(captured))
    ctx = make_ctx(tmp_path, bare=settings.bare, settings=settings)
    outcome = asyncio.run(backend.run("the task text", ctx))
    assert outcome.code.name == "COMPLETED"
    return captured, ctx.bus.history


def _settings(chat_template_kwargs: dict | None, *, bare: bool = False) -> Settings:
    return Settings(
        model=ModelRef(provider="ollama", model="test-model", base_url="http://test.local"),
        bare=bare,
        chat_template_kwargs=chat_template_kwargs,
    )


def test_unset_chat_template_kwargs_keeps_payload_shape_unchanged(tmp_path: Path) -> None:
    captured, events = _run_once(tmp_path, _settings(None))
    assert "chat_template_kwargs" not in captured[0]
    request = next(e for e in events if e.kind == "model_request")
    assert request.data["chat_template_kwargs"] is None


def test_set_chat_template_kwargs_is_sent_and_audited(tmp_path: Path) -> None:
    want = {"enable_thinking": False}
    captured, events = _run_once(tmp_path, _settings(want))
    assert captured[0]["chat_template_kwargs"] == want
    request = next(e for e in events if e.kind == "model_request")
    assert request.data["chat_template_kwargs"] == want


def test_chat_template_kwargs_composes_with_bare_mode(tmp_path: Path) -> None:
    """bare + template kwargs: single user message, no tools key, PLUS the
    template switch — the two reductions are orthogonal."""
    want = {"enable_thinking": False}
    captured, events = _run_once(tmp_path, _settings(want, bare=True))
    payload = captured[0]
    assert payload["messages"] == [{"role": "user", "content": "the task text"}]
    assert "tools" not in payload
    assert payload["chat_template_kwargs"] == want
    request = next(e for e in events if e.kind == "model_request")
    assert request.data["payload_mode"] == "bare"
    assert request.data["chat_template_kwargs"] == want


def test_audit_metadata_stays_body_free(tmp_path: Path) -> None:
    _, events = _run_once(tmp_path, _settings({"enable_thinking": False}))
    blob = json.dumps([e.data for e in events])
    assert "the task text" not in blob
