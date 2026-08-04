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
import math
import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .errors import ConfigError
from .safety import Budgets, Hook, PermissionMode

# Providers that run on the operator's own hardware, where a token has no
# marginal cost. For these the max_tokens ceiling is pure friction — a run that
# does real work on an 8GB box can burn 200k tokens legitimately — so it is
# lifted to a high finite backstop (runaways are still bounded by max_rounds and
# max_wall_seconds, and paid spend by max_cost_usd, which stays untouched).
_LOCAL_PROVIDERS = frozenset({"ollama", "vllm"})
_LOCAL_TOKEN_BUDGET = 20_000_000

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
    # Optional per-role model overlay for the reviewer/judge. ``review_overlay``
    # holds only the fields explicitly set across ``[roles.review]`` /
    # ``PXX_REVIEW_*`` layers (a *sparse* overlay); ``review_model`` is that
    # overlay RESOLVED against the final coder ``model`` at the end of
    # ``load_settings`` — resolving late so a later ``PXX_MODEL``/``PXX_API_KEY``
    # override still propagates into the reviewer. When no overlay is set,
    # ``review_model`` stays ``None`` and the role reuses ``model`` (behaviour is
    # byte-identical to not setting it). This is the seam the ROADMAP
    # "model-backed boundary roles" item builds on. Reviewer routing is a
    # data-egress surface (the diff + bearer token go to ``base_url``), so the
    # overlay is honoured only from user config, env, or CLI — never repo-local.
    review_overlay: tuple[tuple[str, str], ...] = ()
    review_model: ModelRef | None = None
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
    #: Durable per-box backend posture ("native" | "aider" | "auto"). When set,
    #: it fixes the backend for the auto lane (ask/edit/plan/chat) without a
    #: per-invocation --backend flag; an explicit --backend still wins. None
    #: means "auto" (the historical default).
    backend: str | None = None
    sandbox_shell: bool = False
    #: Explicit risk-acceptance: allow ``run_shell`` in edit/auto mode with NO
    #: PreToolUse hook and NO sandbox. Off by default (fail-closed) — shell is
    #: unconfined by ``scope`` (it has no path target), so a write-capable run
    #: must gate it with a hook, ``sandbox_shell``, or this explicit opt-in.
    allow_ungated_shell: bool = False
    mcp_servers: tuple[McpServerSpec, ...] = ()
    safety_net: bool = True  # K5: stash + pxx-pre/<ts> tag on edit-capable starts
    auto_commit: bool = False  # opt-in: commit session work on COMPLETED (the undo tag still points at pre-session HEAD)
    #: Per-box default for the ``pxx loop`` model-backed review gate. The shipped
    #: default is OFF (review is opt-in via ``--review``); setting this true flips
    #: the default to ON for this box, and ``--no-review`` still turns it off for
    #: a single run. A ``--review``/``--no-review`` flag always wins over this.
    loop_review: bool = False

    @property
    def effective_budgets(self) -> Budgets:
        """Budgets adjusted for the coder provider. On a local provider
        (``ollama``/``vllm``) where tokens are free, the ``max_tokens`` ceiling
        is lifted to a high backstop — *unless* the operator explicitly changed
        it, in which case their value is honoured verbatim. "Explicit" is
        detected as differing from the shipped default: a user who tightened (or
        raised) ``max_tokens`` keeps exactly what they set. Paid providers, and
        every non-token budget (rounds, wall-clock, cost), are never touched, so
        this only ever *relaxes* a free-to-run token cap. Use this instead of
        ``.budgets`` at BudgetGuard construction so the guard sees the resolved
        ceiling."""
        if (
            self.model.provider.lower() in _LOCAL_PROVIDERS
            and self.budgets.max_tokens == Budgets().max_tokens
        ):
            return replace(self.budgets, max_tokens=_LOCAL_TOKEN_BUDGET)
        return self.budgets

    @property
    def effective_review_model(self) -> ModelRef:
        """The model the reviewer/judge runs on: the per-role ``review_model``
        override when set, else the coder ``model``. Centralises the fallback
        so every reviewer construction site shares one contract (and a run
        with no override is byte-identical to before this field existed)."""
        return self.review_model or self.model


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
    "roles",
    "permission",
    "backend",
    "scope",
    "trusted_paths",
    "memory_enabled",
    "memory_dir",
    "state_dir",
    "test_command",
    "sandbox_shell",
    "allow_ungated_shell",
    "safety_net",
    "auto_commit",
    "loop_review",
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
# Roles that may carry a per-role model overlay today. Extensible (coder,
# planner, …) but fail-closed: an unknown role name is a typo, not a silent
# no-op. Only the reviewer/judge role is wired through the runtime so far.
_KNOWN_ROLE_KEYS = {"review"}
_ROLE_MODEL_KEYS = {"provider", "model", "base_url", "api_key"}
_PROVIDERS = ("ollama", "openai", "vllm", "openai-compatible")


def _validate_role_overlay(entry: dict[str, Any], source: str) -> dict[str, str]:
    """Validate a ``[roles.review]`` table and return only its explicitly-set
    fields as a sparse ``{key: value}`` dict. Fail-closed on unknown keys and
    unknown providers. Sparse so the reviewer inherits the *final* coder model
    (resolved once, late — see :func:`_merge_model_ref` at finalize)."""
    unknown = set(entry) - _ROLE_MODEL_KEYS
    if unknown:
        raise ConfigError(f"{source}: unknown model keys {sorted(unknown)}")
    pairs: dict[str, str] = {}
    for key in ("provider", "model", "base_url", "api_key"):
        if key in entry:
            value = str(entry[key])
            if key == "provider" and value not in _PROVIDERS:
                raise ConfigError(f"{source}: unknown provider {value!r}")
            pairs[key] = value
    return pairs


def _merge_model_ref(base: ModelRef, entry: dict[str, str], source: str) -> ModelRef:
    """Overlay a validated sparse ``entry`` (see :func:`_validate_role_overlay`)
    onto ``base``. Unspecified fields inherit ``base`` so a partial overlay
    (e.g. only ``base_url``) reuses the rest."""
    ref = base
    if "provider" in entry:
        ref = replace(ref, provider=entry["provider"])
    if "model" in entry:
        ref = replace(ref, model=entry["model"])
    if "base_url" in entry:
        ref = replace(ref, base_url=entry["base_url"])
    if "api_key" in entry:
        ref = replace(ref, api_key=entry["api_key"])
    return ref


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
    if "roles" in data and not allow_exec_surfaces:
        # Reviewer routing is a data-egress surface: `[roles.review] base_url`
        # sends the diff (and any inherited api_key bearer token) to that
        # endpoint. A repo-local file must not be able to redirect the review —
        # honour the overlay only from user config, env, or CLI (same trust
        # boundary as hooks/mcp_servers).
        log.warning(
            "ignoring roles in repo-local config %s (reviewer endpoint routing "
            "is a data-egress surface — set it in user config, env, or CLI)",
            source,
        )
        data = {k: v for k, v in data.items() if k != "roles"}
    kwargs: dict[str, Any] = {}
    model = base.model
    if "model" in data:
        model = replace(model, model=str(data["model"]))
    if "provider" in data:
        provider = str(data["provider"])
        if provider not in _PROVIDERS:
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
    if "roles" in data:
        roles = data["roles"]
        if not isinstance(roles, dict):
            raise ConfigError(f"{source}: roles must be a table")
        unknown = set(roles) - _KNOWN_ROLE_KEYS
        if unknown:
            raise ConfigError(f"{source}: unknown roles {sorted(unknown)}")
        if "review" in roles:
            entry = roles["review"]
            if not isinstance(entry, dict):
                raise ConfigError(f"{source}: roles.review must be a table")
            # Accumulate a SPARSE overlay: later layers override earlier for the
            # same field, but unset fields are NOT filled from the coder model
            # here — that happens once, at finalize, against the *final* model
            # (so a later PXX_MODEL/PXX_API_KEY override still propagates).
            merged = dict(base.review_overlay)
            merged.update(_validate_role_overlay(entry, f"{source}: roles.review"))
            kwargs["review_overlay"] = tuple(merged.items())
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
    if "backend" in data:
        backend = str(data["backend"]).strip().lower()
        if backend not in ("native", "aider", "auto"):
            raise ConfigError(
                f"{source}: backend must be 'native', 'aider', or 'auto' (got {data['backend']!r})"
            )
        kwargs["backend"] = backend
    if "sandbox_shell" in data:
        kwargs["sandbox_shell"] = bool(data["sandbox_shell"])
    if "allow_ungated_shell" in data:
        kwargs["allow_ungated_shell"] = bool(data["allow_ungated_shell"])
    if "safety_net" in data:
        kwargs["safety_net"] = bool(data["safety_net"])
    if "auto_commit" in data:
        kwargs["auto_commit"] = bool(data["auto_commit"])
    if "loop_review" in data:
        # Strict: TOML has native booleans, so reject a quoted string rather than
        # silently truthy-coercing it (bool("false") is True — a fail-open trap
        # for a gate that makes a model call). Enables the review gate by default.
        value = data["loop_review"]
        if not isinstance(value, bool):
            raise ConfigError(f"{source}: loop_review must be a boolean")
        kwargs["loop_review"] = value
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
    "PXX_ALLOW_UNGATED_SHELL": "allow_ungated_shell",
    "PXX_AUTO_COMMIT": "auto_commit",
    "PXX_LOOP_REVIEW": "loop_review",
    "PXX_BACKEND": "backend",
    # 1.x compat
    "PXX_OLLAMA_BASE": "base_url",
    "PXX_OLLAMA_MODEL": "model",
}

# Per-role reviewer overlay via env (maps to the ``[roles.review]`` sub-keys).
# Kept separate from ``_ENV_MAP`` because these merge into a nested table, not
# a flat Settings field.
_REVIEW_ENV_MAP = {
    "PXX_REVIEW_MODEL": "model",
    "PXX_REVIEW_PROVIDER": "provider",
    "PXX_REVIEW_BASE_URL": "base_url",
    "PXX_REVIEW_API_KEY": "api_key",
}


def _settings_from_env(base: Settings) -> Settings:
    data: dict[str, Any] = {}
    for env_key, cfg_key in _ENV_MAP.items():
        value = os.environ.get(env_key)
        if value:
            if cfg_key == "sandbox_shell":
                data[cfg_key] = value.lower() in ("1", "true", "yes")
            elif cfg_key == "allow_ungated_shell":
                data[cfg_key] = value.lower() in ("1", "true", "yes")
            elif cfg_key == "auto_commit":
                data[cfg_key] = value.lower() in ("1", "true", "yes")
            elif cfg_key == "loop_review":
                data[cfg_key] = value.lower() in ("1", "true", "yes")
            else:
                data[cfg_key] = value
    if os.environ.get("PXX_MEMORY_ENABLED", "").lower() in ("0", "false", "no"):
        data["memory_enabled"] = False
    if scope := os.environ.get("PXX_SCOPE"):
        data["scope"] = [s.strip() for s in scope.split(",") if s.strip()]
    review: dict[str, Any] = {}
    for env_key, sub in _REVIEW_ENV_MAP.items():
        value = os.environ.get(env_key)
        if value:
            review[sub] = value
    if review:
        data["roles"] = {"review": review}
    if not data:
        return base
    return _settings_from_dict(data, base, "environment")


def load_settings(
    cwd: Path | None = None,
    cli_overrides: dict[str, Any] | None = None,
) -> Settings:
    """Resolve the effective settings for a run in ``cwd``."""
    _load_env_file()
    warn_unconsumed_env()  # after the env file loads, so its typos warn too
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
    # Resolve the reviewer overlay ONCE, now that every layer (and any
    # PXX_MODEL/PXX_API_KEY override) has landed on the final coder model.
    if settings.review_overlay:
        settings = replace(
            settings,
            review_model=_merge_model_ref(
                settings.model, dict(settings.review_overlay), "roles.review"
            ),
        )
    return settings


def _timeout_from_env(names: tuple[str, ...], default: float) -> float:
    """First *present* name in ``names`` wins — presence, not truthiness.

    A variable that is set but empty/malformed/non-positive is a
    configuration mistake: warn and use ``default``, never fall through to
    the next variable (a silently different timeout on the wrong knob is
    worse than the default). Semantics pinned by the
    ``micro-timeout-env-chain`` eval case.
    """
    for name in names:
        if name not in os.environ:
            continue
        raw = os.environ[name]
        try:
            value = float(raw)
        except ValueError:
            value = None
        if value is not None and math.isfinite(value) and value > 0:
            # finite and positive: rejects 0, negatives, NaN, and inf —
            # float("inf") would silently disable the HTTP timeout entirely
            return value
        log.warning(
            "%s=%r is not a positive number of seconds — using the %.0fs default",
            name,
            raw,
            default,
        )
        return default
    return default


def review_timeout(default: float = 120.0) -> float:
    """Reviewer HTTP timeout in seconds (2.1.2). ``PXX_REVIEW_TIMEOUT`` wins
    whenever it is set, falling back to ``PXX_NATIVE_TIMEOUT`` only when
    unset — hardware slow enough to need a longer agent round is slow on
    review prefill too. Malformed and non-positive/NaN values warn and use
    ``default`` (2.1.4: previously they fell back silently, and an empty
    ``PXX_REVIEW_TIMEOUT`` fell through to the native knob). Lives here
    because config.py is the sanctioned environment boundary (golden
    principle no-os-environ-outside-config).

    Real-world calibration: a ~930-line review diff on 8 GB hardware died at
    exactly the old fixed 120 s ceiling (2026-07-26, first usage-found defect
    after 2.1.1).
    """
    return _timeout_from_env(("PXX_REVIEW_TIMEOUT", "PXX_NATIVE_TIMEOUT"), default)


def native_timeout(default: float = 300.0) -> float:
    """Native per-round HTTP timeout in seconds (``PXX_NATIVE_TIMEOUT``).

    Local models on memory-constrained hardware can legitimately need
    >300 s for a round; a too-low value surfaces as a misleading
    MODEL_UNAVAILABLE. Moved here from backends/native.py (2.1.4) so the
    env read lives at the sanctioned boundary and malformed values warn
    instead of falling back silently.
    """
    return _timeout_from_env(("PXX_NATIVE_TIMEOUT",), default)


# Every PXX_* variable some part of the ecosystem consumes. Python readers
# stay in this module plus the server-token check; the second set is read by
# the git hooks and the release workflow, not this process.
_CONSUMED_ENV = (
    frozenset(_ENV_MAP)
    | frozenset(_REVIEW_ENV_MAP)
    | {
        "PXX_MEMORY_ENABLED",
        "PXX_MEMORY_DIR",
        "PXX_SCOPE",
        "PXX_REVIEW_TIMEOUT",
        "PXX_NATIVE_TIMEOUT",
        "PXX_SERVER_TOKEN",
    }
)
_ECOSYSTEM_ENV = frozenset({"PXX_DIFF_CAP", "PXX_PRECOMMIT_SKIP", "PXX_CONTENT_DENYLIST"})
_warned_unconsumed = False


def warn_unconsumed_env() -> None:
    """Warn once per process about set-but-never-consumed ``PXX_*`` vars.

    Typo insurance (deferred from 2.1.1): ``PXX_TIMEOUT`` or
    ``PXX_REVEIW_TIMEOUT`` silently does nothing today. Warn-only — an
    unknown variable must never fail a run (users legitimately export
    experimental or future-version knobs).
    """
    global _warned_unconsumed
    if _warned_unconsumed:
        return
    _warned_unconsumed = True
    known = _CONSUMED_ENV | _ECOSYSTEM_ENV
    for key in sorted(os.environ):
        if key.startswith("PXX_") and key not in known:
            log.warning("%s is set but nothing in pxx consumes it — possible typo", key)
