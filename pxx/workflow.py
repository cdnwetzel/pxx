"""Phase 10.5: the WORKFLOW.md machine contract — load + validate, fail closed.

``WORKFLOW.md`` (repo root) carries a fenced TOML block that is the
repository-owned, machine-readable workflow contract: states, budgets,
commands, permission profiles (consumed by :mod:`pxx.broker`), hooks, and
the protected-paths mirror. It lives in the trusted control plane — hashed
into every agent manifest and protected from the optimizer.

Fail closed like :mod:`pxx.config`: unknown keys, missing required sections,
and type errors raise :class:`~pxx.errors.ConfigError`. There are no silent
defaults for a missing or malformed contract.
"""

from __future__ import annotations

import hashlib
import logging
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .errors import ConfigError
from .safety import Budgets, Hook

log = logging.getLogger("pxx.workflow")

WORKFLOW_FILENAME = "WORKFLOW.md"

_TOML_RE = re.compile(r"```toml\s*\n(.*?)```", re.DOTALL)

#: The action classes the broker understands (see pxx.broker).
ACTION_CLASSES: frozenset[str] = frozenset(
    {"read", "write", "delete", "shell", "network", "memory"}
)
_PERMISSION_MODES: frozenset[str] = frozenset({"ask", "plan", "edit", "auto"})
_HOOK_EVENTS: frozenset[str] = frozenset({"PreToolUse", "PostToolUse"})
_BUDGET_FIELDS: frozenset[str] = frozenset(
    {"max_rounds", "max_tokens", "max_cost_usd", "max_wall_seconds", "max_diff_lines"}
)
_TOP_KEYS: frozenset[str] = frozenset(
    {
        "schema_version",
        "states",
        "budgets",
        "commands",
        "permissions",
        "hooks",
        "protected_paths",
    }
)


@dataclass(frozen=True)
class Workflow:
    """The validated, frozen workflow contract."""

    schema_version: int
    states: tuple[str, ...]
    initial_state: str
    terminal_states: tuple[str, ...]
    budgets: Budgets
    commands: dict[str, str]
    permissions: dict[str, frozenset[str]]  # permission mode -> action classes
    hooks: tuple[Hook, ...]
    protected_paths: tuple[str, ...]
    raw_hash: str  # sha256[:16] of the raw WORKFLOW.md bytes


def _sha16(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()[:16]


def workflow_hash(root: Path) -> str:
    """sha256[:16] of the raw WORKFLOW.md under ``root``; ``""`` when absent.

    Absence degrades to an empty hash (identity stays deterministic) — the
    fail-closed loader is :func:`load_workflow`.
    """
    try:
        return _sha16((Path(root) / WORKFLOW_FILENAME).read_bytes())
    except OSError:
        return ""


def _require(cond: bool, message: str) -> None:
    if not cond:
        raise ConfigError(f"WORKFLOW.md: {message}")


def _str_list(value: object, where: str) -> tuple[str, ...]:
    _require(
        isinstance(value, list) and all(isinstance(v, str) for v in value),
        f"{where} must be a list of strings",
    )
    return tuple(value)  # type: ignore[arg-type]


def load_workflow(root: Path | str) -> Workflow:
    """Load and validate ``<root>/WORKFLOW.md``. Raises ConfigError on any
    missing/malformed contract element (fail closed)."""
    root = Path(root)
    path = root / WORKFLOW_FILENAME
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ConfigError(f"WORKFLOW.md: not found at {path} ({exc})") from exc
    text = raw.decode("utf-8", errors="replace")
    match = _TOML_RE.search(text)
    _require(match is not None, "no fenced ```toml block found")
    try:
        data = tomllib.loads(match.group(1))  # type: ignore[union-attr]
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"WORKFLOW.md: invalid TOML: {exc}") from exc
    _require(isinstance(data, dict), "contract must be a TOML table")
    unknown = set(data) - _TOP_KEYS
    _require(not unknown, f"unknown keys: {sorted(unknown)}")

    version = data.get("schema_version")
    _require(isinstance(version, int) and version == 1, "schema_version must be 1")

    states = data.get("states")
    _require(isinstance(states, dict), "[states] section is required")
    unknown = set(states) - {"initial", "names", "terminal"}
    _require(not unknown, f"[states] unknown keys: {sorted(unknown)}")
    names = _str_list(states.get("names"), "states.names")
    initial = states.get("initial")
    _require(
        isinstance(initial, str) and initial in names, "states.initial must be one of states.names"
    )
    terminal = _str_list(states.get("terminal"), "states.terminal")
    _require(
        bool(names) and set(terminal) <= set(names),
        "states.terminal must be a subset of states.names",
    )

    budgets_raw = data.get("budgets")
    _require(isinstance(budgets_raw, dict), "[budgets] section is required")
    unknown = set(budgets_raw) - _BUDGET_FIELDS
    _require(not unknown, f"[budgets] unknown keys: {sorted(unknown)}")
    budgets_kwargs: dict[str, object] = {}
    for key, value in budgets_raw.items():
        expected = float if key in ("max_cost_usd", "max_wall_seconds") else int
        _require(
            isinstance(value, (int, float)) and not isinstance(value, bool),
            f"budgets.{key} must be numeric",
        )
        budgets_kwargs[key] = expected(value)
    budgets = Budgets(**budgets_kwargs)  # type: ignore[arg-type]

    commands = data.get("commands")
    _require(isinstance(commands, dict), "[commands] section is required")
    for key, value in commands.items():
        _require(
            isinstance(key, str) and isinstance(value, str) and value.strip(),
            f"commands.{key} must be a non-empty command string",
        )

    permissions = data.get("permissions")
    _require(isinstance(permissions, dict), "[permissions] section is required")
    unknown = set(permissions) - _PERMISSION_MODES
    _require(not unknown, f"[permissions] unknown modes: {sorted(unknown)}")
    profiles: dict[str, frozenset[str]] = {}
    for mode, classes in permissions.items():
        parsed = _str_list(classes, f"permissions.{mode}")
        bad = set(parsed) - ACTION_CLASSES
        _require(not bad, f"permissions.{mode} unknown action classes: {sorted(bad)}")
        profiles[mode] = frozenset(parsed)

    hooks_raw = data.get("hooks", [])
    _require(isinstance(hooks_raw, list), "hooks must be an array of tables")
    hooks: list[Hook] = []
    for i, entry in enumerate(hooks_raw):
        _require(isinstance(entry, dict), f"hooks[{i}] must be a table")
        unknown = set(entry) - {"event", "command", "timeout", "matcher"}
        _require(not unknown, f"hooks[{i}] unknown keys: {sorted(unknown)}")
        event = entry.get("event")
        command = entry.get("command")
        _require(event in _HOOK_EVENTS, f"hooks[{i}].event must be one of {sorted(_HOOK_EVENTS)}")
        _require(
            isinstance(command, str) and command.strip(),
            f"hooks[{i}].command must be a non-empty string",
        )
        hooks.append(
            Hook(
                event=event,  # type: ignore[arg-type]
                command=command,  # type: ignore[arg-type]
                timeout=float(entry.get("timeout", 10.0)),
                matcher=str(entry.get("matcher", "")),
            )
        )

    protected = data.get("protected_paths")
    _require(isinstance(protected, dict), "[protected_paths] section is required")
    unknown = set(protected) - {"paths"}
    _require(not unknown, f"[protected_paths] unknown keys: {sorted(unknown)}")
    protected_paths = _str_list(protected.get("paths"), "protected_paths.paths")

    return Workflow(
        schema_version=version,
        states=names,
        initial_state=initial,  # type: ignore[arg-type]
        terminal_states=terminal,
        budgets=budgets,
        commands={str(k): str(v) for k, v in commands.items()},
        permissions=profiles,
        hooks=tuple(hooks),
        protected_paths=protected_paths,
        raw_hash=_sha16(raw),
    )


def validate_workflow(root: Path | str) -> Workflow:
    """Alias for :func:`load_workflow` (the CLI verb reads better)."""
    return load_workflow(root)


__all__ = [
    "ACTION_CLASSES",
    "WORKFLOW_FILENAME",
    "Workflow",
    "load_workflow",
    "validate_workflow",
    "workflow_hash",
]
