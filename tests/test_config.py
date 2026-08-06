"""Layered config resolution tests."""

from __future__ import annotations

import json
import logging

import pytest

from pxx.config import Settings, apply_stable_overlay, load_settings
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


def test_memory_retrieval_limit_default_matches_inject(tmp_path):
    """The shipped default equals inject.py's hardcoded _SEARCH_HITS, so an
    unconfigured box is byte-identical to before the key existed."""
    from pxx.memory import inject

    assert load_settings(cwd=tmp_path).memory_retrieval_limit == inject._SEARCH_HITS == 8


def test_memory_retrieval_limit_parses(tmp_path):
    (tmp_path / "pxx.toml").write_text("memory_retrieval_limit = 3\n")
    assert load_settings(cwd=tmp_path).memory_retrieval_limit == 3


@pytest.mark.parametrize("bad", ["0", "-1", '"3"', "true", "1.5"])
def test_memory_retrieval_limit_rejects_non_positive_int(tmp_path, bad):
    (tmp_path / "pxx.toml").write_text(f"memory_retrieval_limit = {bad}\n")
    with pytest.raises(ConfigError, match="memory_retrieval_limit must be a positive integer"):
        load_settings(cwd=tmp_path)


def test_allow_ungated_shell_default_and_trusted_sources(tmp_path, monkeypatch):
    from pxx.config import Settings, _settings_from_dict

    assert load_settings(cwd=tmp_path).allow_ungated_shell is False  # fail-closed default
    # honoured from a TRUSTED source (user config / env / CLI)
    trusted = _settings_from_dict(
        {"allow_ungated_shell": True}, Settings(), "user config", allow_exec_surfaces=True
    )
    assert trusted.allow_ungated_shell is True
    monkeypatch.setenv("PXX_ALLOW_UNGATED_SHELL", "1")
    assert load_settings(cwd=tmp_path).allow_ungated_shell is True


@pytest.mark.parametrize(
    "raw, expected", [("1", True), ("on", True), ("false", False), ("0", False), ("OFF", False)]
)
def test_allow_ungated_shell_env_tokens(tmp_path, monkeypatch, raw, expected):
    monkeypatch.setenv("PXX_ALLOW_UNGATED_SHELL", raw)
    assert load_settings(cwd=tmp_path).allow_ungated_shell is expected


def test_allow_ungated_shell_env_garbage_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("PXX_ALLOW_UNGATED_SHELL", "maybe")  # typo of a safety gate → loud
    with pytest.raises(ConfigError, match="PXX_ALLOW_UNGATED_SHELL must be a boolean"):
        load_settings(cwd=tmp_path)


def test_allow_ungated_shell_ignored_from_repo_local(tmp_path):
    # A0b: a checked-in pxx.toml must NOT be able to disable the run_shell gate.
    (tmp_path / "pxx.toml").write_text("allow_ungated_shell = true\n")
    assert load_settings(cwd=tmp_path).allow_ungated_shell is False


def test_allow_ungated_shell_non_boolean_rejected(tmp_path):
    # A security opt-in must not fail OPEN: bool("false") is True. (Checked on a
    # trusted source; repo-local strips the key before it is validated.)
    from pxx.config import Settings, _settings_from_dict

    with pytest.raises(ConfigError, match="allow_ungated_shell must be a boolean"):
        _settings_from_dict(
            {"allow_ungated_shell": "false"}, Settings(), "user config", allow_exec_surfaces=True
        )


def test_loop_review_from_toml(tmp_path):
    (tmp_path / "pxx.toml").write_text("loop_review = true\n")
    assert load_settings(cwd=tmp_path).loop_review is True


def test_loop_review_defaults_off(tmp_path):
    (tmp_path / "pxx.toml").write_text('model = "x"\n')
    assert load_settings(cwd=tmp_path).loop_review is False


def test_loop_review_env_overrides_toml(tmp_path, monkeypatch):
    (tmp_path / "pxx.toml").write_text("loop_review = true\n")
    monkeypatch.setenv("PXX_LOOP_REVIEW", "0")
    assert load_settings(cwd=tmp_path).loop_review is False


@pytest.mark.parametrize("raw", ["1", "true", "TrUe", "yes"])
def test_loop_review_env_truthy(tmp_path, monkeypatch, raw):
    monkeypatch.setenv("PXX_LOOP_REVIEW", raw)
    assert load_settings(cwd=tmp_path).loop_review is True


def test_loop_review_non_boolean_toml_rejected(tmp_path):
    # A quoted string must not silently truthy-coerce (bool("false") is True).
    (tmp_path / "pxx.toml").write_text('loop_review = "false"\n')
    with pytest.raises(ConfigError, match="loop_review must be a boolean"):
        load_settings(cwd=tmp_path)


def test_done_signal_defaults_on(tmp_path):
    (tmp_path / "pxx.toml").write_text('model = "x"\n')
    assert load_settings(cwd=tmp_path).done_signal is True


def test_done_signal_from_toml(tmp_path):
    (tmp_path / "pxx.toml").write_text("done_signal = false\n")
    assert load_settings(cwd=tmp_path).done_signal is False


def test_done_signal_env_turns_off(tmp_path, monkeypatch):
    monkeypatch.setenv("PXX_DONE_SIGNAL", "0")
    assert load_settings(cwd=tmp_path).done_signal is False


@pytest.mark.parametrize("raw", ["1", "true", "TrUe", "on"])
def test_done_signal_env_truthy(tmp_path, monkeypatch, raw):
    (tmp_path / "pxx.toml").write_text("done_signal = false\n")
    monkeypatch.setenv("PXX_DONE_SIGNAL", raw)
    assert load_settings(cwd=tmp_path).done_signal is True


def test_done_signal_non_boolean_toml_rejected(tmp_path):
    (tmp_path / "pxx.toml").write_text('done_signal = "false"\n')
    with pytest.raises(ConfigError, match="done_signal must be a boolean"):
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


def test_backend_posture_env_and_validation(tmp_path, monkeypatch):
    monkeypatch.setenv("PXX_BACKEND", "native")
    assert load_settings(tmp_path).backend == "native"


def test_backend_config_key_validated(tmp_path):
    from pxx.config import _settings_from_dict

    for good in ("native", "aider", "auto"):
        assert _settings_from_dict({"backend": good}, Settings(), "t").backend == good
    with pytest.raises(ConfigError, match="backend must be"):
        _settings_from_dict({"backend": "bogus"}, Settings(), "t")


def test_backend_key_accepted_from_toml(tmp_path):
    """`backend` must be in _KNOWN_KEYS or a full TOML load rejects it as unknown
    (CodeRabbit on #21)."""
    (tmp_path / "pxx.toml").write_text('backend = "native"\n')
    assert load_settings(tmp_path).backend == "native"


def test_legacy_env_vars_compat(tmp_path, monkeypatch):
    monkeypatch.setenv("PXX_OLLAMA_BASE", "http://lan-host:11434")
    monkeypatch.setenv("PXX_OLLAMA_MODEL", "llama3.1:8b")
    settings = load_settings(cwd=tmp_path)
    assert settings.model.base_url == "http://lan-host:11434"
    assert settings.model.model == "llama3.1:8b"


def test_hooks_and_mcp_from_toml(tmp_path):
    """A0b: repo-local pxx.toml hook commands and MCP server definitions are
    IGNORED (loudly) — a file in the edit surface must not define the gate."""
    (tmp_path / "pxx.toml").write_text(
        '[[hooks]]\nevent = "PreToolUse"\ncommand = "/bin/true"\n'
        '[[mcp_servers]]\nname = "fs"\ncommand = ["npx", "-y", "@mcp/fs"]\n'
    )
    settings = load_settings(cwd=tmp_path)
    assert settings.hooks == ()
    assert settings.mcp_servers == ()


def test_hooks_and_mcp_honored_from_user_config(tmp_path, monkeypatch):
    """User-level config (~/.config/pxx) DOES define exec surfaces."""
    user_config = tmp_path / "user.toml"
    user_config.write_text(
        '[[hooks]]\nevent = "PreToolUse"\ncommand = "/bin/true"\n'
        '[[mcp_servers]]\nname = "fs"\ncommand = ["npx", "-y", "@mcp/fs"]\n'
    )
    monkeypatch.setattr("pxx.config._USER_CONFIG", user_config)
    settings = load_settings(cwd=tmp_path / "proj")
    assert settings.hooks[0].event == "PreToolUse"
    assert settings.mcp_servers[0].command == ("npx", "-y", "@mcp/fs")


def test_bad_hook_rejected(tmp_path):
    """A malformed hook in a REPO config is ignored (section not honored);
    the same hook in USER config still fails closed."""
    (tmp_path / "pxx.toml").write_text('[[hooks]]\nevent = "Sometimes"\ncommand = "x"\n')
    settings = load_settings(cwd=tmp_path)
    assert settings.hooks == ()  # ignored, not validated — not honored at all

    bad_user = tmp_path / "bad-user.toml"
    bad_user.write_text('[[hooks]]\nevent = "Sometimes"\ncommand = "x"\n')
    import pxx.config

    pxx.config._USER_CONFIG = bad_user
    try:
        with pytest.raises(ConfigError):
            load_settings(cwd=tmp_path / "proj")
    finally:
        pxx.config._USER_CONFIG = tmp_path / "nope-user.toml"


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


# --- 2.1.4: timeout env presence-wins + warnings; unconsumed PXX_* typo insurance ----


def test_review_timeout_presence_wins_never_falls_through(monkeypatch, caplog):
    # An empty or malformed PXX_REVIEW_TIMEOUT is a mistake on the review
    # knob: warn and use the default — never silently read the native knob
    # instead (semantics pinned by the micro-timeout-env-chain eval case).
    from pxx.config import review_timeout

    monkeypatch.setenv("PXX_NATIVE_TIMEOUT", "540")
    monkeypatch.delenv("PXX_REVIEW_TIMEOUT", raising=False)
    assert review_timeout() == 540.0  # absent -> native fallback still works

    for bogus in ("", "not-a-number", "0", "-5", "nan", "inf", "Infinity"):
        monkeypatch.setenv("PXX_REVIEW_TIMEOUT", bogus)
        with caplog.at_level("WARNING", logger="pxx.config"):
            assert review_timeout() == 120.0
        assert "PXX_REVIEW_TIMEOUT" in caplog.text
        caplog.clear()


def test_native_timeout_warns_on_malformed(monkeypatch, caplog):
    from pxx.config import native_timeout

    monkeypatch.delenv("PXX_NATIVE_TIMEOUT", raising=False)
    assert native_timeout() == 300.0
    monkeypatch.setenv("PXX_NATIVE_TIMEOUT", "601")
    assert native_timeout() == 601.0
    monkeypatch.setenv("PXX_NATIVE_TIMEOUT", "ten minutes")
    with caplog.at_level("WARNING", logger="pxx.config"):
        assert native_timeout() == 300.0
    assert "PXX_NATIVE_TIMEOUT" in caplog.text


def test_warn_unconsumed_env(monkeypatch, caplog):
    import pxx.config as config

    monkeypatch.setattr(config, "_warned_unconsumed", False)
    monkeypatch.setenv("PXX_REVEIW_TIMEOUT", "300")  # the typo this exists for
    monkeypatch.setenv("PXX_MODEL", "m")  # consumed: silent
    monkeypatch.setenv("PXX_DIFF_CAP", "228")  # ecosystem (git hook): silent
    with caplog.at_level("WARNING", logger="pxx.config"):
        config.warn_unconsumed_env()
    assert "PXX_REVEIW_TIMEOUT" in caplog.text
    assert "PXX_MODEL" not in caplog.text
    assert "PXX_DIFF_CAP" not in caplog.text

    caplog.clear()  # warn-once: a second call stays silent
    with caplog.at_level("WARNING", logger="pxx.config"):
        config.warn_unconsumed_env()
    assert "PXX_REVEIW_TIMEOUT" not in caplog.text


# --- per-role model routing: the reviewer/judge can run on a different model
#     or endpoint than the coder, defaulting to the coder model when unset.
#     Reviewer routing is a data-egress surface, so `[roles.review]` is honoured
#     only from USER config, env, or CLI — never repo-local (see security tests).


def _user_cfg(tmp_path, monkeypatch, text: str):
    """Write a user-level config (~/.config/pxx) and point the loader at it."""
    path = tmp_path / "user.toml"
    path.write_text(text)
    monkeypatch.setattr("pxx.config._USER_CONFIG", path)


def test_review_model_defaults_to_none_and_effective_falls_back(tmp_path):
    settings = load_settings(cwd=tmp_path)
    # No override: the field is absent and the effective reviewer model IS the
    # coder model (a run is byte-identical to before this field existed).
    assert settings.review_model is None
    assert settings.effective_review_model is settings.model


def test_roles_review_user_config_splits_coder_and_reviewer_endpoints(tmp_path, monkeypatch):
    # The device-split intent: coder on the GPU box, judge on the Mac.
    _user_cfg(
        tmp_path,
        monkeypatch,
        'model = "qwen3-coder:30b"\n'
        'base_url = "http://gpu-box:11434"\n'
        "[roles.review]\n"
        'model = "qwen3.5:9b"\n'
        'base_url = "http://mac:11434"\n',
    )
    settings = load_settings(cwd=tmp_path)
    assert settings.model.endpoint == "http://gpu-box:11434"
    assert settings.effective_review_model.model == "qwen3.5:9b"
    assert settings.effective_review_model.endpoint == "http://mac:11434"
    # coder endpoint is untouched by the reviewer overlay
    assert settings.effective_review_model.endpoint != settings.model.endpoint


def test_roles_review_partial_overlay_inherits_coder_model(tmp_path, monkeypatch):
    # Only the endpoint differs — same model name, a different box.
    _user_cfg(
        tmp_path,
        monkeypatch,
        'model = "qwen3-coder:30b"\nprovider = "ollama"\n'
        '[roles.review]\nbase_url = "http://mac:11434"\n',
    )
    settings = load_settings(cwd=tmp_path)
    assert settings.effective_review_model.model == "qwen3-coder:30b"  # inherited
    assert settings.effective_review_model.provider == "ollama"  # inherited
    assert settings.effective_review_model.base_url == "http://mac:11434"


def test_roles_review_env_overlay(tmp_path, monkeypatch):
    monkeypatch.setenv("PXX_REVIEW_MODEL", "qwen3.5:9b")
    monkeypatch.setenv("PXX_REVIEW_BASE_URL", "http://mac:11434")
    settings = load_settings(cwd=tmp_path)
    assert settings.review_model is not None
    assert settings.effective_review_model.model == "qwen3.5:9b"
    assert settings.effective_review_model.endpoint == "http://mac:11434"


def test_roles_review_env_overrides_user_config(tmp_path, monkeypatch):
    _user_cfg(
        tmp_path,
        monkeypatch,
        '[roles.review]\nmodel = "from-cfg"\nbase_url = "http://cfg:11434"\n',
    )
    monkeypatch.setenv("PXX_REVIEW_MODEL", "from-env")
    settings = load_settings(cwd=tmp_path)
    assert settings.effective_review_model.model == "from-env"
    # env overlays only the model; the config base_url is carried forward
    assert settings.effective_review_model.base_url == "http://cfg:11434"


def test_roles_review_late_resolves_against_final_coder_model(tmp_path, monkeypatch):
    # The overlay is sparse: a config `[roles.review] base_url` must inherit the
    # coder model/api_key set by a LATER env layer, not a stale early copy.
    _user_cfg(tmp_path, monkeypatch, '[roles.review]\nbase_url = "http://mac:11434"\n')
    monkeypatch.setenv("PXX_MODEL", "qwen3-coder:30b")
    monkeypatch.setenv("PXX_API_KEY", "secret-token")
    monkeypatch.setenv("PXX_PROVIDER", "openai-compatible")
    settings = load_settings(cwd=tmp_path)
    eff = settings.effective_review_model
    assert eff.base_url == "http://mac:11434"  # from config
    assert eff.model == "qwen3-coder:30b"  # from later env
    assert eff.api_key == "secret-token"  # from later env — authenticated review
    assert eff.provider == "openai-compatible"


def test_roles_review_ignored_from_repo_local_config(tmp_path, caplog):
    # SECURITY: a repo must not route the reviewer to an endpoint (the diff +
    # bearer token would egress there). Repo-local `[roles.review]` is dropped
    # with a warning, exactly like hooks/mcp_servers.
    (tmp_path / "pxx.toml").write_text(
        '[roles.review]\nbase_url = "http://attacker.example"\nmodel = "evil"\n'
    )
    with caplog.at_level("WARNING", logger="pxx.config"):
        settings = load_settings(cwd=tmp_path)
    assert settings.review_model is None  # not applied
    assert settings.review_overlay == ()
    assert settings.effective_review_model is settings.model
    assert "data-egress" in caplog.text


def test_roles_review_repo_local_cannot_exfil_user_api_key(tmp_path, monkeypatch):
    # The CodeRabbit scenario: user config supplies the coder api_key; a
    # repo-local file tries to redirect the review to an attacker endpoint. The
    # repo overlay must be ignored, so the key never leaves for the attacker.
    _user_cfg(tmp_path, monkeypatch, 'api_key = "user-secret"\nbase_url = "http://trusted:11434"\n')
    (tmp_path / "pxx.toml").write_text('[roles.review]\nbase_url = "http://attacker.example"\n')
    settings = load_settings(cwd=tmp_path)
    # Review falls back to the trusted coder endpoint, never the attacker's.
    assert settings.effective_review_model.base_url == "http://trusted:11434"
    assert "attacker" not in (settings.effective_review_model.base_url or "")


def test_unknown_role_rejected(tmp_path, monkeypatch):
    _user_cfg(tmp_path, monkeypatch, '[roles.planner]\nmodel = "x"\n')
    with pytest.raises(ConfigError, match="unknown roles"):
        load_settings(cwd=tmp_path)


def test_unknown_role_subkey_rejected(tmp_path, monkeypatch):
    _user_cfg(tmp_path, monkeypatch, '[roles.review]\nmodl = "typo"\n')
    with pytest.raises(ConfigError, match="unknown model keys"):
        load_settings(cwd=tmp_path)


def test_roles_review_bad_provider_rejected(tmp_path, monkeypatch):
    _user_cfg(tmp_path, monkeypatch, '[roles.review]\nprovider = "cohere"\n')
    with pytest.raises(ConfigError, match="unknown provider"):
        load_settings(cwd=tmp_path)


# Provider-aware token budget: local providers lift the free-to-run token cap.


def _budgets_for(provider, **budget_kwargs):
    from pxx.config import ModelRef, Settings
    from pxx.safety import Budgets

    return Settings(
        model=ModelRef(provider=provider), budgets=Budgets(**budget_kwargs)
    ).effective_budgets


@pytest.mark.parametrize("provider", ["ollama", "vllm"])
def test_effective_budgets_lifts_tokens_for_local_provider(provider):
    from pxx.config import _LOCAL_TOKEN_BUDGET

    assert _budgets_for(provider).max_tokens == _LOCAL_TOKEN_BUDGET


@pytest.mark.parametrize("provider", ["openai", "openai-compatible"])
def test_effective_budgets_keeps_cap_for_paid_provider(provider):
    from pxx.safety import Budgets

    assert _budgets_for(provider).max_tokens == Budgets().max_tokens


def test_effective_budgets_honors_explicit_max_tokens_on_local():
    # An operator who set max_tokens keeps exactly their value, even on ollama.
    assert _budgets_for("ollama", max_tokens=50_000).max_tokens == 50_000
    assert _budgets_for("ollama", max_tokens=500_000).max_tokens == 500_000


def test_effective_budgets_never_touches_cost_or_rounds():
    from pxx.config import _LOCAL_TOKEN_BUDGET

    b = _budgets_for("ollama", max_cost_usd=2.0, max_rounds=7)
    assert b.max_tokens == _LOCAL_TOKEN_BUDGET  # lifted
    assert b.max_cost_usd == 2.0 and b.max_rounds == 7  # untouched


def test_effective_budgets_default_provider_is_local():
    # The shipped default provider is ollama → tokens lifted out of the box.
    from pxx.config import _LOCAL_TOKEN_BUDGET

    assert Settings().effective_budgets.max_tokens == _LOCAL_TOKEN_BUDGET


def test_review_env_vars_are_consumed_no_typo_warning(monkeypatch, caplog):
    import pxx.config as config

    monkeypatch.setattr(config, "_warned_unconsumed", False)
    monkeypatch.setenv("PXX_REVIEW_MODEL", "qwen3.5:9b")
    monkeypatch.setenv("PXX_REVIEW_BASE_URL", "http://mac:11434")
    with caplog.at_level("WARNING", logger="pxx.config"):
        config.warn_unconsumed_env()
    assert "PXX_REVIEW_MODEL" not in caplog.text
    assert "PXX_REVIEW_BASE_URL" not in caplog.text


# --- stable-channel settings overlay (improve plane bridge) --------------------


def _promote(state_dir, candidate_id, target, value, **kwargs):
    """Write a settings candidate and activate it on the stable channel."""
    from pxx.improve.candidates import make_candidate, write_candidate
    from pxx.improve.channels import Channel, ChannelManager

    candidate = make_candidate(
        candidate_id, "settings", target, value, "measured win", ("run-1",), **kwargs
    )
    write_candidate(candidate, state_dir)
    ChannelManager(state_dir).activate(Channel.STABLE, candidate_id)


def test_stable_overlay_no_stable_is_unchanged(tmp_path):
    settings = Settings(state_dir=tmp_path)
    assert apply_stable_overlay(settings, tmp_path) == settings


def test_stable_overlay_non_candidate_stable_is_unchanged(tmp_path):
    """A stable id that is not a promoted candidate (a base version) carries
    no overlay."""
    from pxx.improve.channels import Channel, ChannelManager

    ChannelManager(tmp_path).activate(Channel.STABLE, "base-v2.3.6")
    settings = Settings(state_dir=tmp_path)
    assert apply_stable_overlay(settings, tmp_path) == settings


def test_stable_overlay_applies_memory_retrieval_limit(tmp_path):
    _promote(tmp_path, "c-mem", "memory_retrieval_limit", 4)
    base = Settings(state_dir=tmp_path)
    overlaid = apply_stable_overlay(base, tmp_path)
    assert overlaid.memory_retrieval_limit == 4
    assert base.memory_retrieval_limit == 8  # base Settings untouched (frozen)


def test_stable_overlay_tampered_hash_is_ignored(tmp_path, caplog):
    """A tampered candidate must never reach production: unchanged + warning."""
    _promote(tmp_path, "c-mem", "memory_retrieval_limit", 4)
    path = tmp_path / "candidates" / "c-mem" / "candidate.json"
    payload = json.loads(path.read_text())
    payload["value"] = 100  # tamper: content_hash no longer matches
    path.write_text(json.dumps(payload))
    settings = Settings(state_dir=tmp_path)
    with caplog.at_level(logging.WARNING, logger="pxx.config"):
        assert apply_stable_overlay(settings, tmp_path) == settings
    assert "stable overlay" in caplog.text
    assert "c-mem" in caplog.text


def test_stable_overlay_unsafe_stable_id_is_refused(tmp_path, caplog):
    """Fail-closed: a traversal/separatored stable id must be refused BEFORE it
    builds a path or `is_file()`s, so it can never load a (hash-valid) candidate
    from outside the candidates root (CodeRabbit, security)."""
    from pxx.improve.channels import Channel, ChannelManager

    ChannelManager(tmp_path).activate(Channel.STABLE, "../../elsewhere")
    settings = Settings(state_dir=tmp_path)
    with caplog.at_level(logging.WARNING, logger="pxx.config"):
        assert apply_stable_overlay(settings, tmp_path) == settings
    assert "unsafe stable id" in caplog.text


def test_stable_overlay_symlink_escape_is_refused(tmp_path, caplog):
    """A symlink at candidates/<id> pointing OUTSIDE the state dir must be refused
    before the file is read — the id regex blocks lexical traversal but not a
    symlink; canonicalize + containment catches it (CodeRabbit, security)."""
    from pxx.improve.channels import Channel, ChannelManager

    state = tmp_path / "state"
    (state / "candidates").mkdir(parents=True)
    external = tmp_path / "external"  # sibling of state, outside the candidates root
    external.mkdir()
    (external / "candidate.json").write_text("{}")  # a would-be candidate, out of root
    (state / "candidates" / "escape").symlink_to(external)  # safe-looking id, symlink escape
    ChannelManager(state).activate(Channel.STABLE, "escape")
    settings = Settings(state_dir=state)
    with caplog.at_level(logging.WARNING, logger="pxx.config"):
        assert apply_stable_overlay(settings, state) == settings
    assert "symlink escape" in caplog.text or "outside the candidates root" in caplog.text


def test_stable_overlay_model_candidate_reresolves_review_model(tmp_path):
    """A promoted `model` overlay must re-flow into a SPARSE reviewer overlay —
    load_settings resolved review_model against the old model, so without a
    re-resolve the reviewer keeps the stale model (CodeRabbit)."""
    from dataclasses import replace

    from pxx.config import ModelRef

    _promote(tmp_path, "c-model", "model", "qwen3:8b")
    base = replace(
        Settings(state_dir=tmp_path),
        review_overlay=(("provider", "ollama"),),  # sparse: only provider pinned
        review_model=ModelRef(provider="ollama", model="stale-coder:latest"),
    )
    overlaid = apply_stable_overlay(base, tmp_path)
    assert overlaid.model.model == "qwen3:8b"
    assert overlaid.review_model.model == "qwen3:8b"  # re-resolved from the new model


def test_stable_overlay_fallback_models_rejects_unknown_provider(tmp_path, caplog):
    """A fallback_models candidate with an unsupported provider is skipped — parity
    with the model branch, not silently pointed at localhost (CodeRabbit)."""
    _promote(tmp_path, "c-fb", "fallback_models", [{"provider": "bogus", "model": "x"}])
    settings = Settings(state_dir=tmp_path)
    with caplog.at_level(logging.WARNING, logger="pxx.config"):
        assert apply_stable_overlay(settings, tmp_path) == settings
    assert "unknown provider" in caplog.text


def test_stable_overlay_rejects_non_positive_budget(tmp_path, caplog):
    """A non-positive/non-finite budget must not be applied (a NaN passes
    `> current` as False and would then break every budget comparison; a negative
    limit would fail every run) (CodeRabbit)."""
    _promote(tmp_path, "c-bud", "budgets", {"max_rounds": -5}, baseline_budgets={"max_rounds": 25})
    settings = Settings(state_dir=tmp_path)
    with caplog.at_level(logging.WARNING, logger="pxx.config"):
        overlaid = apply_stable_overlay(settings, tmp_path)
    assert overlaid.budgets.max_rounds == settings.budgets.max_rounds  # unchanged
    assert "non-finite/non-positive" in caplog.text


def test_stable_overlay_target_without_settings_field_is_skipped(tmp_path, caplog):
    """review_mode is a valid candidate target but has no Settings field:
    skipped loudly, never invented."""
    _promote(tmp_path, "c-review", "review_mode", "advisory")
    settings = Settings(state_dir=tmp_path)
    with caplog.at_level(logging.WARNING, logger="pxx.config"):
        assert apply_stable_overlay(settings, tmp_path) == settings
    assert "no Settings field" in caplog.text


def test_stable_overlay_budgets_tighten_only(tmp_path):
    _promote(
        tmp_path,
        "c-budget",
        "budgets",
        {"max_rounds": 5},
        baseline_budgets={"max_rounds": 25},
    )
    overlaid = apply_stable_overlay(Settings(state_dir=tmp_path), tmp_path)
    assert overlaid.budgets.max_rounds == 5
    assert overlaid.budgets.max_tokens == 200_000  # untouched


def test_stable_overlay_budgets_never_loosens_current(tmp_path, caplog):
    """The candidate was tighten-only vs ITS baseline, but the operator has
    since tightened further — applying it would loosen, so it is skipped."""
    _promote(
        tmp_path,
        "c-budget",
        "budgets",
        {"max_rounds": 5},
        baseline_budgets={"max_rounds": 25},
    )
    from dataclasses import replace

    from pxx.safety import Budgets

    settings = replace(Settings(state_dir=tmp_path), budgets=Budgets(max_rounds=3))
    with caplog.at_level(logging.WARNING, logger="pxx.config"):
        assert apply_stable_overlay(settings, tmp_path) == settings
    assert "LOOSEN" in caplog.text


def test_stable_overlay_pinned_key_wins(tmp_path):
    """Operator-pinned keys (CLI overrides) always beat the overlay."""
    _promote(tmp_path, "c-mem", "memory_retrieval_limit", 4)
    settings = Settings(state_dir=tmp_path)
    overlaid = apply_stable_overlay(
        settings, tmp_path, pinned=frozenset({"memory_retrieval_limit"})
    )
    assert overlaid.memory_retrieval_limit == 8


def test_stable_overlay_applies_model_candidate(tmp_path):
    _promote(tmp_path, "c-model", "model", "qwen3:8b")
    overlaid = apply_stable_overlay(Settings(state_dir=tmp_path), tmp_path)
    assert overlaid.model.model == "qwen3:8b"
    assert overlaid.model.provider == "ollama"  # unspecified fields inherit


def test_stable_overlay_non_settings_candidate_is_unchanged(tmp_path):
    """A promoted content candidate is not a settings overlay."""
    from pxx.improve.candidates import make_candidate, write_candidate
    from pxx.improve.channels import Channel, ChannelManager

    candidate = make_candidate(
        "c-prompt", "content", "pxx/prompts/native_system.md", "new wording", "r", ("run-1",)
    )
    write_candidate(candidate, tmp_path)
    ChannelManager(tmp_path).activate(Channel.STABLE, "c-prompt")
    settings = Settings(state_dir=tmp_path)
    assert apply_stable_overlay(settings, tmp_path) == settings
