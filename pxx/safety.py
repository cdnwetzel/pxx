"""Safety gates: permission modes, scope, hooks, budgets.

The trusted control plane. All decisions are fail-closed and deterministic —
they cannot be overridden by model judgment. Every path is canonicalized
(symlinks resolved) before any decision; model output is untrusted input.
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .errors import BudgetExceeded, HookDenied, ScopeViolation


class PermissionMode(StrEnum):
    ASK = "ask"  # read-only: no writes, no shell
    PLAN = "plan"  # read-only, model produces a plan
    EDIT = "edit"  # writes allowed in scope; shell only via hook
    AUTO = "auto"  # unattended: writes + shell in scope, budgets enforced

    @property
    def can_write(self) -> bool:
        return self in (PermissionMode.EDIT, PermissionMode.AUTO)

    @property
    def can_shell(self) -> bool:
        return self is PermissionMode.AUTO


def canonicalize(path: str | Path, *, cwd: Path | None = None) -> Path:
    """Resolve a path to its canonical absolute form (symlinks resolved).

    Relative paths resolve against ``cwd``. ``..`` traversal and symlink
    escapes are exposed by realpath and caught by :class:`ScopeGate`.
    """
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = (cwd or Path.cwd()) / p
    return Path(os.path.realpath(p))


class ScopeGate:
    """Decides which paths a session may read and write.

    ``root`` is the project root (canonicalized). ``scope`` entries are
    root-relative prefixes; empty means the whole root. ``trusted`` are
    additional canonical absolute roots that are read/write allowed.
    Everything else raises :class:`ScopeViolation`.
    """

    def __init__(
        self,
        root: Path,
        scope: tuple[str, ...] = (),
        trusted: tuple[str, ...] = (),
    ) -> None:
        self.root = canonicalize(root)
        if scope:
            self._prefixes = tuple(canonicalize(self.root / s) for s in scope)
        else:
            self._prefixes = (self.root,)
        self._trusted = tuple(canonicalize(t) for t in trusted)

    def _allowed_roots(self) -> tuple[Path, ...]:
        return self._prefixes + self._trusted

    def check(self, path: str | Path) -> Path:
        """Return the canonical path or raise ScopeViolation."""
        canon = canonicalize(path, cwd=self.root)
        for base in self._allowed_roots():
            if canon == base or base in canon.parents:
                return canon
        raise ScopeViolation(f"path outside scope: {path} (scope: {self.describe()})")

    def in_scope(self, path: str | Path) -> bool:
        try:
            self.check(path)
            return True
        except ScopeViolation:
            return False

    def check_write(self, path: str | Path, permission: PermissionMode) -> Path:
        """Write gate: permission must allow writes AND path must be in scope."""
        if not permission.can_write:
            raise ScopeViolation(f"writes not allowed in permission mode '{permission}'")
        return self.check(path)

    def describe(self) -> str:
        parts = [str(p) for p in self._prefixes]
        parts += [str(t) for t in self._trusted]
        return ", ".join(parts)


@dataclass(frozen=True)
class Hook:
    """A deterministic gate: shell command fired at a lifecycle point.

    Receives JSON on stdin ({"tool": name, "args": {...}, ...}).
    Exit 0 = allow, exit 2 = deny (raises HookDenied), other non-zero =
    hook error, treated as deny (fail-closed).
    """

    event: str  # "PreToolUse" | "PostToolUse"
    command: str
    timeout: float = 10.0
    matcher: str = ""  # optional tool-name substring filter


class HookRunner:
    def __init__(self, hooks: tuple[Hook, ...] = ()) -> None:
        self._pre = [h for h in hooks if h.event == "PreToolUse"]
        self._post = [h for h in hooks if h.event == "PostToolUse"]

    @property
    def has_pre_hooks(self) -> bool:
        """True when at least one PreToolUse hook is configured."""
        return bool(self._pre)

    @staticmethod
    async def _fire(hook: Hook, payload: dict) -> None:
        proc = await asyncio.create_subprocess_exec(
            *shlex.split(hook.command),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(
                proc.communicate(json.dumps(payload).encode()), timeout=hook.timeout
            )
        except TimeoutError as exc:
            proc.kill()
            await proc.wait()  # reap the child: never leak the transport
            raise HookDenied(f"hook timed out ({hook.timeout}s): {hook.command}") from exc
        if proc.returncode == 0:
            return
        detail = stderr.decode(errors="replace").strip()[:300]
        raise HookDenied(
            f"hook denied ({hook.event}, exit {proc.returncode}): {hook.command} {detail} "
            "— adjust or remove the hook in pxx.toml (see docs/CONFIG.md §hooks)"
        )

    async def _run(self, hooks: list[Hook], tool_name: str, payload: dict) -> None:
        for hook in hooks:
            if hook.matcher and hook.matcher not in tool_name:
                continue
            await self._fire(hook, payload)

    async def run_pre(self, tool_name: str, args: dict) -> None:
        await self._run(self._pre, tool_name, {"tool": tool_name, "args": args})

    async def run_post(self, tool_name: str, args: dict, result: str) -> None:
        await self._run(
            self._post,
            tool_name,
            {"tool": tool_name, "args": args, "result_preview": result[:1000]},
        )


@dataclass(frozen=True)
class Budgets:
    max_rounds: int = 25
    max_tokens: int = 200_000
    max_cost_usd: float = 5.0
    max_wall_seconds: float = 1800.0
    max_diff_lines: int = 400


class BudgetGuard:
    """Cumulative budget accounting. Hard-stops via BudgetExceeded."""

    def __init__(self, budgets: Budgets) -> None:
        self.budgets = budgets
        self.rounds = 0
        self.tokens = 0
        self.cost_usd = 0.0
        self.diff_lines = 0
        self._deadline = time.monotonic() + budgets.max_wall_seconds

    @property
    def deadline(self) -> float:
        return self._deadline

    def remaining_seconds(self) -> float:
        return max(0.0, self._deadline - time.monotonic())

    def consume(
        self, *, rounds: int = 0, tokens: int = 0, cost: float = 0.0, diff_lines: int = 0
    ) -> None:
        self.rounds += rounds
        if self.rounds > self.budgets.max_rounds:
            raise BudgetExceeded("max_rounds", str(self.budgets.max_rounds))
        self.tokens += tokens
        if self.tokens > self.budgets.max_tokens:
            raise BudgetExceeded("max_tokens", str(self.budgets.max_tokens))
        self.cost_usd += cost
        if self.cost_usd > self.budgets.max_cost_usd:
            raise BudgetExceeded("max_cost_usd", str(self.budgets.max_cost_usd))
        self.diff_lines += diff_lines
        if self.diff_lines > self.budgets.max_diff_lines:
            raise BudgetExceeded("max_diff_lines", str(self.budgets.max_diff_lines))

    def check_clock(self) -> None:
        if time.monotonic() > self._deadline:
            raise BudgetExceeded("max_wall_seconds", str(self.budgets.max_wall_seconds))

    def snapshot(self) -> dict:
        return {
            "rounds": self.rounds,
            "tokens": self.tokens,
            "cost_usd": round(self.cost_usd, 6),
            "diff_lines": self.diff_lines,
            "remaining_seconds": round(self.remaining_seconds(), 1),
        }
