"""Layered config resolution tests."""

from __future__ import annotations

import pytest

from pxx.config import Settings, load_settings
from pxx.errors import ConfigError
from pxx.safety import PermissionMode


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """Keep user-level config/env files and PXX_* vars out of these tests."""
    for key in list(__import__("os").environ):
        if key.startswith("PXX_") or key == "XDG_STATE_HOME":
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("pxx.config._USER_CONFIG", tmp_path / "nope-user.toml")
    monkeypatch.setattr("pxx.config._USER_ENV", tmp_path / "nope-env")
    return tmp_path


def test_defaults(tmp_path):
    settings = load_settings(cwd=tmp_path)
    assert settings.permission is PermissionMode.ASK
    assert settings.model.provider == "ollama"
    assert settings.budgets.max_rounds == 25


def test_project_toml_applies(tmp_path):
    (tmp_path / "pxx.toml").write_text(
        'model = "devstral:24b"\npermission = "edit"\nscope = ["src", "tests"]\n'
        "[budgets]\nmax_rounds = 5\n"
    )
    settings = load_settings(cwd=tmp_path)
    assert settings.model.model == "devstral:24b"
    assert settings.permission is PermissionMode.EDIT
    assert settings.scope == ("src", "tests")
    assert settings.budgets.max_rounds == 5
    # untouched budget fields keep defaults
    assert settings.budgets.max_tokens == 200_000


def test_unknown_key_rejected(tmp_path):
    (tmp_path / "pxx.toml").write_text('modle = "typo"\n')
    with pytest.raises(ConfigError, match="unknown config keys"):
        load_settings(cwd=tmp_path)


def test_invalid_toml_rejected(tmp_path):
    (tmp_path / "pxx.toml").write_text("not = = toml\n")
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_settings(cwd=tmp_path)


def test_invalid_permission_rejected(tmp_path):
    (tmp_path / "pxx.toml").write_text('permission = "yolo"\n')
    with pytest.raises(ConfigError, match="invalid permission"):
        load_settings(cwd=tmp_path)


def test_env_overrides_project_toml(tmp_path, monkeypatch):
    (tmp_path / "pxx.toml").write_text('model = "from-toml"\n')
    monkeypatch.setenv("PXX_MODEL", "from-env")
    settings = load_settings(cwd=tmp_path)
    assert settings.model.model == "from-env"


def test_cli_overrides_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PXX_MODEL", "from-env")
    settings = load_settings(cwd=tmp_path, cli_overrides={"model": "from-cli"})
    assert settings.model.model == "from-cli"


def test_legacy_env_vars_compat(tmp_path, monkeypatch):
    monkeypatch.setenv("PXX_OLLAMA_BASE", "http://lan-host:11434")
    monkeypatch.setenv("PXX_OLLAMA_MODEL", "llama3.1:8b")
    settings = load_settings(cwd=tmp_path)
    assert settings.model.base_url == "http://lan-host:11434"
    assert settings.model.model == "llama3.1:8b"


def test_hooks_and_mcp_from_toml(tmp_path):
    (tmp_path / "pxx.toml").write_text(
        '[[hooks]]\nevent = "PreToolUse"\ncommand = "/bin/true"\n'
        '[[mcp_servers]]\nname = "fs"\ncommand = ["npx", "-y", "@mcp/fs"]\n'
    )
    settings = load_settings(cwd=tmp_path)
    assert settings.hooks[0].event == "PreToolUse"
    assert settings.mcp_servers[0].command == ("npx", "-y", "@mcp/fs")


def test_bad_hook_rejected(tmp_path):
    (tmp_path / "pxx.toml").write_text('[[hooks]]\nevent = "Sometimes"\ncommand = "x"\n')
    with pytest.raises(ConfigError):
        load_settings(cwd=tmp_path)


def test_fallback_models(tmp_path):
    (tmp_path / "pxx.toml").write_text(
        '[[fallback_models]]\nmodel = "qwen2.5-coder:7b"\nprovider = "ollama"\n'
        '[[fallback_models]]\nmodel = "served"\nprovider = "vllm"\n'
        'base_url = "http://gpu-box:8000"\n'
    )
    settings = load_settings(cwd=tmp_path)
    assert len(settings.fallback_models) == 2
    assert settings.fallback_models[1].provider == "vllm"


def test_dot_pxx_config_dir(tmp_path):
    cfg = tmp_path / ".pxx"
    cfg.mkdir()
    (cfg / "config.toml").write_text('model = "dotted"\n')
    assert load_settings(cwd=tmp_path).model.model == "dotted"


def test_settings_is_frozen():
    with pytest.raises(AttributeError):
        Settings().permission = PermissionMode.AUTO  # type: ignore[misc]
