"""Phase 13.1: TOML eval case loading and validation.

A case is a single ``.toml`` file describing a tiny, self-contained coding
task plus the checks that decide pass/fail. Cases fail closed: malformed
TOML, missing required fields, or unsafe fixture paths raise
:class:`EvalError` naming the offending case path.

Case TOML shape::

    id = "micro-off-by-one"
    tier = "micro"            # micro | regression | adversarial
    task = "Fix the off-by-one in total()."
    allowed_scope = ["calc.py"]
    honest_patch = '''...unified diff...'''
    cheat_patch = '''...unified diff...'''

    [budgets]                 # optional; keys match pxx.safety.Budgets
    max_rounds = 3

    [fixture]
    starting_tests = ["test_calc.py"]

    [fixture.files]
    "calc.py" = "..."
    "test_calc.py" = "..."

    [checks]
    commands = ["{python} test_calc.py"]
    forbidden_patterns = ["noqa"]
    allowed_files = ["calc.py"]
    tests_unchanged = true
    hidden_commands = ["{python} hidden_check.py"]

``{python}`` inside commands is substituted with ``sys.executable`` by the
harness, so fixture repos need nothing beyond the stdlib.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from ..errors import PxxError
from ..safety import Budgets

__all__ = [
    "Case",
    "Checks",
    "EvalError",
    "Family",
    "Fixture",
    "Partition",
    "Tier",
    "load_case",
    "load_cases",
]


class EvalError(PxxError):
    """Malformed eval case or corpus. Fail-closed."""


class Tier(StrEnum):
    MICRO = "micro"
    REGRESSION = "regression"
    ADVERSARIAL = "adversarial"


class Family(StrEnum):
    """The five independent evaluation families (Phase 13 amend)."""

    CAPABILITY = "capability"
    SAFETY = "safety"
    RECOVERY = "recovery"
    CONTEXT = "context"
    ECONOMIC = "economic"


class Partition(StrEnum):
    """Corpus partition (Phase 17.4): candidates are judged on held-out
    cases they did not inspire, never on development cases."""

    DEV = "dev"
    HELD_OUT = "held-out"


_BUDGET_KEYS = {
    "max_rounds",
    "max_tokens",
    "max_cost_usd",
    "max_wall_seconds",
    "max_diff_lines",
}


@dataclass(frozen=True)
class Fixture:
    """Files written into the disposable repo before the agent acts."""

    files: tuple[tuple[str, str], ...] = ()  # (relpath, content), sorted by path
    starting_tests: tuple[str, ...] = ()  # relpaths pinned by tests_unchanged


@dataclass(frozen=True)
class Checks:
    """Deterministic gates evaluated against the patched repo."""

    commands: tuple[str, ...] = ()
    forbidden_patterns: tuple[str, ...] = ()  # regexes scanned against the diff
    allowed_files: tuple[str, ...] = ()  # whitelist of changeable relpaths
    tests_unchanged: bool = False
    hidden_commands: tuple[str, ...] = ()
    no_new_dependencies: bool = False  # diff must not add imports/dep files


@dataclass(frozen=True)
class Case:
    """One eval case. Frozen; ``content_hash`` is stable across processes."""

    id: str
    tier: Tier
    task: str
    allowed_scope: tuple[str, ...] = ()
    budgets: Budgets = field(default_factory=Budgets)
    fixture: Fixture = field(default_factory=Fixture)
    checks: Checks = field(default_factory=Checks)
    honest_patch: str = ""
    cheat_patch: str = ""
    family: Family = Family.CAPABILITY
    partition: Partition = Partition.DEV

    @property
    def content_hash(self) -> str:
        """sha256 over a canonical JSON projection of every field."""
        payload = {
            "id": self.id,
            "tier": str(self.tier),
            "task": self.task,
            "allowed_scope": list(self.allowed_scope),
            "budgets": {
                "max_rounds": self.budgets.max_rounds,
                "max_tokens": self.budgets.max_tokens,
                "max_cost_usd": self.budgets.max_cost_usd,
                "max_wall_seconds": self.budgets.max_wall_seconds,
                "max_diff_lines": self.budgets.max_diff_lines,
            },
            "fixture": {
                "files": [list(f) for f in self.fixture.files],
                "starting_tests": list(self.fixture.starting_tests),
            },
            "checks": {
                "commands": list(self.checks.commands),
                "forbidden_patterns": list(self.checks.forbidden_patterns),
                "allowed_files": list(self.checks.allowed_files),
                "tests_unchanged": self.checks.tests_unchanged,
                "hidden_commands": list(self.checks.hidden_commands),
                "no_new_dependencies": self.checks.no_new_dependencies,
            },
            "honest_patch": self.honest_patch,
            "cheat_patch": self.cheat_patch,
            "family": str(self.family),
            "partition": str(self.partition),
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _err(path: Path, msg: str) -> EvalError:
    return EvalError(f"{path}: {msg}")


def _req_str(path: Path, data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _err(path, f"missing or invalid required field {key!r}")
    return value


def _str_tuple(path: Path, value: Any, key: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise _err(path, f"field {key!r} must be a list of strings")
    return tuple(value)


def _check_relpath(path: Path, rel: str) -> None:
    p = Path(rel)
    if p.is_absolute() or ".." in p.parts or rel.startswith("/"):
        raise _err(path, f"unsafe fixture path {rel!r}")


def _parse_budgets(path: Path, data: Any) -> Budgets:
    if data is None:
        return Budgets()
    if not isinstance(data, dict):
        raise _err(path, "'budgets' must be a table")
    unknown = set(data) - _BUDGET_KEYS
    if unknown:
        raise _err(path, f"unknown budget keys: {sorted(unknown)}")
    kwargs: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _err(path, f"budget {key!r} must be numeric")
        kwargs[key] = value
    return Budgets(**kwargs)


def _parse_fixture(path: Path, data: Any) -> Fixture:
    if data is None:
        return Fixture()
    if not isinstance(data, dict):
        raise _err(path, "'fixture' must be a table")
    unknown = set(data) - {"files", "starting_tests"}
    if unknown:
        raise _err(path, f"unknown fixture keys: {sorted(unknown)}")
    raw_files = data.get("files") or {}
    if not isinstance(raw_files, dict):
        raise _err(path, "'fixture.files' must be a table of path = content")
    files: list[tuple[str, str]] = []
    for rel, content in raw_files.items():
        if not isinstance(rel, str) or not isinstance(content, str):
            raise _err(path, "'fixture.files' keys and values must be strings")
        _check_relpath(path, rel)
        files.append((rel, content))
    starting = _str_tuple(path, data.get("starting_tests"), "fixture.starting_tests")
    for rel in starting:
        _check_relpath(path, rel)
    return Fixture(files=tuple(sorted(files)), starting_tests=starting)


def _parse_checks(path: Path, data: Any) -> Checks:
    if data is None:
        return Checks()
    if not isinstance(data, dict):
        raise _err(path, "'checks' must be a table")
    known = {
        "commands",
        "forbidden_patterns",
        "allowed_files",
        "tests_unchanged",
        "hidden_commands",
        "no_new_dependencies",
    }
    unknown = set(data) - known
    if unknown:
        raise _err(path, f"unknown checks keys: {sorted(unknown)}")
    tests_unchanged = data.get("tests_unchanged", False)
    if not isinstance(tests_unchanged, bool):
        raise _err(path, "'checks.tests_unchanged' must be a boolean")
    no_new_dependencies = data.get("no_new_dependencies", False)
    if not isinstance(no_new_dependencies, bool):
        raise _err(path, "'checks.no_new_dependencies' must be a boolean")
    return Checks(
        commands=_str_tuple(path, data.get("commands"), "checks.commands"),
        forbidden_patterns=_str_tuple(
            path, data.get("forbidden_patterns"), "checks.forbidden_patterns"
        ),
        allowed_files=_str_tuple(path, data.get("allowed_files"), "checks.allowed_files"),
        tests_unchanged=tests_unchanged,
        hidden_commands=_str_tuple(path, data.get("hidden_commands"), "checks.hidden_commands"),
        no_new_dependencies=no_new_dependencies,
    )


def load_case(path: str | Path) -> Case:
    """Load and validate one case TOML. Raises :class:`EvalError` on any
    malformed input (fail-closed); the message names the case path."""
    p = Path(path)
    try:
        raw = p.read_bytes()
    except OSError as exc:
        raise _err(p, f"unreadable case file: {exc}") from exc
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise _err(p, f"malformed TOML: {exc}") from exc
    if not isinstance(data, dict):
        raise _err(p, "case must be a TOML table")

    tier_raw = _req_str(p, data, "tier")
    try:
        tier = Tier(tier_raw)
    except ValueError:
        raise _err(p, f"unknown tier {tier_raw!r}") from None

    family_raw = data.get("family", "capability")
    try:
        family = Family(family_raw)
    except ValueError:
        raise _err(p, f"unknown family {family_raw!r}") from None

    partition_raw = data.get("partition", "dev")
    try:
        partition = Partition(partition_raw)
    except ValueError:
        raise _err(p, f"unknown partition {partition_raw!r}") from None

    allowed_scope = _str_tuple(p, data.get("allowed_scope"), "allowed_scope")
    for rel in allowed_scope:
        _check_relpath(p, rel)

    return Case(
        id=_req_str(p, data, "id"),
        tier=tier,
        task=_req_str(p, data, "task"),
        allowed_scope=allowed_scope,
        budgets=_parse_budgets(p, data.get("budgets")),
        fixture=_parse_fixture(p, data.get("fixture")),
        checks=_parse_checks(p, data.get("checks")),
        honest_patch=_req_str(p, data, "honest_patch"),
        cheat_patch=_req_str(p, data, "cheat_patch"),
        family=family,
        partition=partition,
    )


def load_cases(directory: str | Path) -> list[Case]:
    """Load every ``*.toml`` case in a directory, sorted by case id.

    Duplicate ids fail closed."""
    d = Path(directory)
    if not d.is_dir():
        raise EvalError(f"{d}: not a case directory")
    cases = [load_case(p) for p in sorted(d.glob("*.toml"))]
    seen: set[str] = set()
    for case in cases:
        if case.id in seen:
            raise EvalError(f"{d}: duplicate case id {case.id!r}")
        seen.add(case.id)
    return cases
