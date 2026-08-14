"""Typed actions — the model replies with exactly ONE; the host validates shape + fields
before executing. An ill-formed action is REJECTED (and the model re-prompted), never guessed.

v0 actions:
- ``NEED_CONTEXT(path)``   — a page fault; the host pages the exact file in.
- ``PATCH(path, expected_sha, old_string, new_string)`` — applied only if ``expected_sha``
  matches the page's current hash AND ``old_string`` occurs exactly once (exact match, no
  fuzz); else REJECT + page fresh source. Never applied blind.
- ``RUN_TEST``            — runs the ledger's acceptance command only (host-owned).
- ``SEARCH(query)`` / ``INSPECT(ref)`` — bounded results, returned by reference.
- ``COMPLETE``           — accepted only after a host RUN_TEST of the ledger command passes.
- ``BLOCKED(reason)``    — an honest stop; never recorded as COMPLETED.
"""

from __future__ import annotations

from dataclasses import dataclass


class ActionError(ValueError):
    """The model's action was ill-formed (bad type or missing/mistyped fields)."""


@dataclass(frozen=True)
class NeedContext:
    path: str


@dataclass(frozen=True)
class Patch:
    path: str
    expected_sha: str
    old_string: str
    new_string: str


@dataclass(frozen=True)
class RunTest:
    pass


@dataclass(frozen=True)
class Search:
    query: str


@dataclass(frozen=True)
class Inspect:
    ref: str


@dataclass(frozen=True)
class Complete:
    pass


@dataclass(frozen=True)
class Blocked:
    reason: str


Action = NeedContext | Patch | RunTest | Search | Inspect | Complete | Blocked


def _require_str(obj: dict, key: str) -> str:
    val = obj.get(key)
    if not isinstance(val, str) or not val:
        raise ActionError(f"action missing/invalid string field: {key!r}")
    return val


def parse_action(obj: object) -> Action:
    """Validate + build a typed action from a decoded JSON object. Fail-closed on any drift."""
    if not isinstance(obj, dict):
        raise ActionError("action must be a JSON object")
    kind = obj.get("type")
    if not isinstance(kind, str):
        raise ActionError("action missing 'type'")
    kind = kind.upper()
    if kind == "NEED_CONTEXT":
        return NeedContext(path=_require_str(obj, "path"))
    if kind == "PATCH":
        return Patch(
            path=_require_str(obj, "path"),
            expected_sha=_require_str(obj, "expected_sha"),
            old_string=_require_str(obj, "old_string"),
            # new_string may legitimately be empty (a deletion), so it is checked as str only
            new_string=obj["new_string"]
            if isinstance(obj.get("new_string"), str)
            else _bad("new_string"),
        )
    if kind == "RUN_TEST":
        return RunTest()
    if kind == "SEARCH":
        return Search(query=_require_str(obj, "query"))
    if kind == "INSPECT":
        return Inspect(ref=_require_str(obj, "ref"))
    if kind == "COMPLETE":
        return Complete()
    if kind == "BLOCKED":
        return Blocked(reason=_require_str(obj, "reason"))
    raise ActionError(f"unknown action type: {kind!r}")


def _bad(field: str) -> str:
    raise ActionError(f"action missing/invalid string field: {field!r}")
