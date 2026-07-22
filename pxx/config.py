"""Layered configuration.

Precedence (highest wins): CLI overrides > ``PXX_*`` env vars >
project TOML (``./pxx.toml`` or ``./.pxx/config.toml``) >
user TOML (``~/.config/pxx/config.toml``) > built-in defaults.

Additionally ``~/.config/pxx/env`` (KEY=VALUE lines) is loaded into the
process environment via ``os.environ.setdefault`` — real env always wins.
Unknown TOML keys raise :class:`ConfigError` (fail-closed, no silent typos).
Nothing here runs at import time.
"""

from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .errors import ConfigError
from .safety import Budgets, Hook, PermissionMode

log = logging.getLogger("pxx.config")


@dataclass(frozen=True)
class ModelRef:
    provider: str = "ollama"  # "ollama" | "openai" | "vllm" | "openai-compatible"
    model: str = "qwen2.5-coder:7b"
    base_url: str | None = None
    api_key: str | None = None

    @property
    def endpoint(self) -> str:
        if self.base_url:
            return self.base_url.rstrip("/")
        return {
            "ollama": "http://localhost:11434",
            "vllm": "http://127.0.0.1:8000",
            "openai": "https://api.openai.com",
        }.get(self.provider, "http://localhost:11434")


@dataclass(frozen=True)
class McpServerSpec:
    name: str
    command: tuple[str, ...]  # stdio transport: argv to spawn


@dataclass(frozen=True)
class Settings:
    model: ModelRef = field(default_factory=ModelRef)
    fallback_models: tuple[ModelRef, ...] = ()
    permission: PermissionMode = PermissionMode.ASK
    scope: tuple[str, ...] = ()
    trusted_paths: tuple[str, ...] = ()
    budgets: Budgets = field(default_factory=Budgets)
    memory_enabled: bool = True
    memory_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("PXX_MEMORY_DIR", "~/.pxx")).expanduser()
    )
    state_dir: Path = field(
        default_factory=lambda: (
            Path(os.environ.get("XDG_STATE_HOME", "~/.local/state")).expanduser() / "pxx"
        )
    )
    hooks: tuple[Hook, ...] = ()
    test_command: str | None = None
    sandbox_shell: bool = False
    mcp_servers: tuple[McpServerSpec, ...] = ()
    safety_net: bool = True  # K5: stash + pxx-pre/<ts> tag on edit-capable starts
    auto_commit: bool = False  # opt-in: commit session work on COMPLETED (the undo tag still points at pre-session HEAD)


_USER_CONFIG = Path("~/.config/pxx/config.toml").expanduser()
_USER_ENV = Path("~/.config/pxx/env").expanduser()
_PROJECT_CONFIGS = ("pxx.toml", os.path.join(".pxx", "config.toml"))

# TOML key -> Settings field (flat keys) handled explicitly below.
_KNOWN_KEYS = {
    "model",
    "provider",
    "base_url",
    "api_key",
    "fallback_models",
    "permission",
    "scope",
    "trusted_paths",
    "memory_enabled",
    "memory_dir",
    "state_dir",
    "test_command",
    "sandbox_shell",
    "safety_net",
    "auto_commit",
    "budgets",
    "hooks",
    "mcp_servers",
}
_KNOWN_BUDGET_KEYS = {
    "max_rounds",
    "max_tokens",
    "max_cost_usd",
    "max_wall_seconds",
    "max_diff_lines",
}
_KNOWN_HOOK_KEYS = {"event", "command", "timeout", "matcher"}
_KNOWN_MCP_KEYS = {"name", "command"}


def _load_env_file() -> None:
    if not _USER_ENV.is_file():
        return
    for raw in _USER_ENV.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc
    unknown = set(data) - _KNOWN_KEYS
    if unknown:
        raise ConfigError(f"unknown config keys in {path}: {sorted(unknown)}")
    return data


def _settings_from_dict(
    data: dict[str, Any],
    base: Settings,
    source: str,
    *,
    allow_exec_surfaces: bool = True,
) -> Settings:
    """Merge one config source. ``allow_exec_surfaces=False`` (repo-local
    project configs) means hook commands and MCP server definitions are
    IGNORED with a loud warning: a file inside the edit surface must not be
    able to define the gate that guards the edit surface (A0b)."""
    for key in ("hooks", "mcp_servers"):
        if key in data and not allow_exec_surfaces:
            log.warning(
                "ignoring %s in repo-local config %s (exec surfaces are honored "
                "only from user config, env, or CLI — a repo must not define "
                "the gate that guards it)",
                key,
                source,
            )
            data = {k: v for k, v in data.items() if k != key}
    kwargs: dict[str, Any] = {}
    model = base.model
    if "model" in data:
        model = replace(model, model=str(data["model"]))
    if "provider" in data:
        provider = str(data["provider"])
        if provider not in ("ollama", "openai", "vllm", "openai-compatible"):
            raise ConfigError(f"{source}: unknown provider {provider!r}")
        model = replace(model, provider=provider)
    if "base_url" in data:
        model = replace(model, base_url=str(data["base_url"]))
    if "api_key" in data:
        model = replace(model, api_key=str(data["api_key"]))
    kwargs["model"] = model
    if "fallback_models" in data:
        refs = []
        for i, entry in enumerate(data["fallback_models"]):
            if not isinstance(entry, dict) or "model" not in entry:
                raise ConfigError(f"{source}: fallback_models[{i}] needs at least 'model'")
            refs.append(
                ModelRef(
                    provider=str(entry.get("provider", "ollama")),
                    model=str(entry["model"]),
                    base_url=entry.get("base_url"),
                    api_key=entry.get("api_key"),
                )
            )
        kwargs["fallback_models"] = tuple(refs)
    if "permission" in data:
        try:
            kwargs["permission"] = PermissionMode(str(data["permission"]))
        except ValueError as exc:
            raise ConfigError(f"{source}: invalid permission {data['permission']!r}") from exc
    for key in ("scope", "trusted_paths"):
        if key in data:
            kwargs[key] = tuple(str(s) for s in data[key])
    for key in ("memory_dir", "state_dir"):
        if key in data:
            kwargs[key] = Path(str(data[key])).expanduser()
    if "memory_enabled" in data:
        kwargs["memory_enabled"] = bool(data["memory_enabled"])
    if "test_command" in data:
        kwargs["test_command"] = str(data["test_command"])
    if "sandbox_shell" in data:
        kwargs["sandbox_shell"] = bool(data["sandbox_shell"])
    if "safety_net" in data:
        kwargs["safety_net"] = bool(data["safety_net"])
    if "auto_commit" in data:
        kwargs["auto_commit"] = bool(data["auto_commit"])
    if "budgets" in data:
        b = data["budgets"]
        unknown = set(b) - _KNOWN_BUDGET_KEYS
        if unknown:
            raise ConfigError(f"{source}: unknown budget keys {sorted(unknown)}")
        merged = {**base.budgets.__dict__, **b}
        kwargs["budgets"] = Budgets(**merged)
    if "hooks" in data:
        hooks = []
        for i, h in enumerate(data["hooks"]):
            unknown = set(h) - _KNOWN_HOOK_KEYS
            if unknown or not {"event", "command"} <= set(h):
                raise ConfigError(f"{source}: invalid hooks[{i}] {sorted(h)}")
            if h["event"] not in ("PreToolUse", "PostToolUse"):
                raise ConfigError(f"{source}: hooks[{i}].event must be PreToolUse/PostToolUse")
            hooks.append(
                Hook(
                    event=str(h["event"]),
                    command=str(h["command"]),
                    timeout=float(h.get("timeout", 10.0)),
                    matcher=str(h.get("matcher", "")),
                )
            )
        kwargs["hooks"] = tuple(hooks)
    if "mcp_servers" in data:
        specs = []
        for i, s in enumerate(data["mcp_servers"]):
            unknown = set(s) - _KNOWN_MCP_KEYS
            if unknown or not {"name", "command"} <= set(s):
                raise ConfigError(f"{source}: invalid mcp_servers[{i}] {sorted(s)}")
            cmd = s["command"]
            specs.append(
                McpServerSpec(
                    name=str(s["name"]),
                    command=tuple(str(c) for c in (cmd if isinstance(cmd, list) else [cmd])),
                )
            )
        kwargs["mcp_servers"] = tuple(specs)
    return replace(base, **kwargs)


_ENV_MAP = {
    "PXX_MODEL": "model",
    "PXX_PROVIDER": "provider",
    "PXX_BASE_URL": "base_url",
    "PXX_API_KEY": "api_key",
    "PXX_PERMISSION": "permission",
    "PXX_TEST_COMMAND": "test_command",
    "PXX_SANDBOX_SHELL": "sandbox_shell",
    "PXX_AUTO_COMMIT": "auto_commit",
    # 1.x compat
    "PXX_OLLAMA_BASE": "base_url",
    "PXX_OLLAMA_MODEL": "model",
}


def _settings_from_env(base: Settings) -> Settings:
    data: dict[str, Any] = {}
    for env_key, cfg_key in _ENV_MAP.items():
        value = os.environ.get(env_key)
        if value:
            if cfg_key == "sandbox_shell":
                data[cfg_key] = value.lower() in ("1", "true", "yes")
            elif cfg_key == "auto_commit":
                data[cfg_key] = value.lower() in ("1", "true", "yes")
            else:
                data[cfg_key] = value
    if os.environ.get("PXX_MEMORY_ENABLED", "").lower() in ("0", "false", "no"):
        data["memory_enabled"] = False
    if scope := os.environ.get("PXX_SCOPE"):
        data["scope"] = [s.strip() for s in scope.split(",") if s.strip()]
    if not data:
        return base
    return _settings_from_dict(data, base, "environment")


def load_settings(
    cwd: Path | None = None,
    cli_overrides: dict[str, Any] | None = None,
) -> Settings:
    """Resolve the effective settings for a run in ``cwd``."""
    _load_env_file()
    settings = Settings()
    if _USER_CONFIG.is_file():
        settings = _settings_from_dict(_read_toml(_USER_CONFIG), settings, str(_USER_CONFIG))
    root = cwd or Path.cwd()
    for name in _PROJECT_CONFIGS:
        path = root / name
        if path.is_file():
            settings = _settings_from_dict(
                _read_toml(path), settings, str(path), allow_exec_surfaces=False
            )
    settings = _settings_from_env(settings)
    if cli_overrides:
        settings = _settings_from_dict(
            {k: v for k, v in cli_overrides.items() if v is not None}, settings, "CLI"
        )
    return settings
