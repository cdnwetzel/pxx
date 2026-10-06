"""Tests for Settings.temperature — the deterministic-generation-surface key.

Default UNSET (None): the key is not sent and the serving layer's default
applies, so an unconfigured box is byte-identical to before the key existed
(the golden default payload fixture in test_bare.py pins that). Set, the
native backend includes ``temperature`` in the chat-completions payload and
in the body-free ``model_request`` audit metadata. The key carries no safety
semantics — generation only, never scope/permissions/budgets/hooks/routing —
so it is honoured from every config layer including repo-local.

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


def test_temperature_defaults_unset(tmp_path: Path) -> None:
    (tmp_path / "pxx.toml").write_text('model = "x"\n')
    assert load_settings(cwd=tmp_path).temperature is None


@pytest.mark.parametrize(
    "text,want",
    [
        ("temperature = 0\n", 0.0),  # int coerces to float
        ("temperature = 0.0\n", 0.0),
        ("temperature = 1.5\n", 1.5),
        ("temperature = 2.0\n", 2.0),
    ],
)
def test_temperature_valid_values(tmp_path: Path, text: str, want: float) -> None:
    (tmp_path / "pxx.toml").write_text(text)
    got = load_settings(cwd=tmp_path).temperature
    assert got == want and isinstance(got, float)


@pytest.mark.parametrize(
    "text",
    [
        "temperature = true\n",  # bool is not a temperature
        'temperature = "0"\n',  # no silent string coercion
        "temperature = -0.1\n",
        "temperature = 2.1\n",
    ],
)
def test_temperature_invalid_values_rejected(tmp_path: Path, text: str) -> None:
    (tmp_path / "pxx.toml").write_text(text)
    with pytest.raises(ConfigError, match="temperature must be a number"):
        load_settings(cwd=tmp_path)


# --- payload + audit ----------------------------------------------------------


def _run_once(tmp_path: Path, settings: Settings) -> tuple[list[dict], list]:
    captured: list[dict] = []
    backend = make_backend(capture_handler(captured))
    ctx = make_ctx(tmp_path, bare=settings.bare, settings=settings)
    outcome = asyncio.run(backend.run("the task text", ctx))
    assert outcome.code.name == "COMPLETED"
    return captured, ctx.bus.history


def _settings(temperature: float | None, *, bare: bool = False) -> Settings:
    return Settings(
        model=ModelRef(provider="ollama", model="test-model", base_url="http://test.local"),
        bare=bare,
        temperature=temperature,
    )


def test_unset_temperature_keeps_payload_shape_unchanged(tmp_path: Path) -> None:
    captured, events = _run_once(tmp_path, _settings(None))
    assert "temperature" not in captured[0]
    request = next(e for e in events if e.kind == "model_request")
    assert request.data["temperature"] is None


def test_set_temperature_is_sent_and_audited(tmp_path: Path) -> None:
    captured, events = _run_once(tmp_path, _settings(0.0))
    assert captured[0]["temperature"] == 0.0
    request = next(e for e in events if e.kind == "model_request")
    assert request.data["temperature"] == 0.0


def test_temperature_composes_with_bare_mode(tmp_path: Path) -> None:
    """temperature = 0 for contract-bound phases: bare payload (single user
    message, no tools key) PLUS the sampling key — the two reductions are
    orthogonal."""
    captured, events = _run_once(tmp_path, _settings(0.0, bare=True))
    payload = captured[0]
    assert payload["messages"] == [{"role": "user", "content": "the task text"}]
    assert "tools" not in payload
    assert payload["temperature"] == 0.0
    request = next(e for e in events if e.kind == "model_request")
    assert request.data["payload_mode"] == "bare" and request.data["temperature"] == 0.0


def test_audit_metadata_stays_body_free(tmp_path: Path) -> None:
    _, events = _run_once(tmp_path, _settings(0.0))
    blob = json.dumps([e.data for e in events])
    assert "the task text" not in blob
