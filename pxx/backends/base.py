"""Backend protocol — pxx owns the runtime; backends are pluggable executors.

A backend receives a task and a :class:`SessionContext` (which carries the
gates: scope, hooks, budgets, event bus, tools, memory) and drives one run.
Every model/tool event must be emitted on the bus; tool execution must go
through ``ctx.tools`` so policy cannot be bypassed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple, Protocol

from ..config import Settings
from ..events import EventBus
from ..outcome import RunOutcome
from ..safety import BudgetGuard, HookRunner, ScopeGate

if TYPE_CHECKING:
    from ..memory.store import MemoryStore
    from ..tools import ToolRegistry

#: An objective "is this run already done?" oracle a driver (e.g. ``run_loop``)
#: may inject. Returns True when the current on-disk edit passes every mandatory
#: gate the driver would enforce, so a backend that keeps calling tools past a
#: finished solution can stop instead of burning the rest of its budget. The
#: oracle is authoritative and side-effect-light (it may run the test command);
#: a backend must never *infer* done — it only asks. ``None`` == no early exit
#: (byte-identical to before this seam existed).
DoneCheck = Callable[[], Awaitable[bool]]


class BackendCapabilities(NamedTuple):
    streaming: bool
    tools: bool
    interactive: bool
    headless: bool


@dataclass
class SessionContext:
    """Everything a backend needs for one run. Constructed by Session."""

    settings: Settings
    bus: EventBus
    scope: ScopeGate
    hooks: HookRunner
    budgets: BudgetGuard
    tools: ToolRegistry
    memory: MemoryStore | None
    session_id: str
    project: str
    cwd: Path
    cancel_event: asyncio.Event
    memory_context: str = ""  # deterministic session-start injection
    profile: Any = None  # pxx.broker.PermissionProfile (resolved by Session)
    #: Optional done-signal oracle injected by a driving loop (see ``DoneCheck``).
    #: Off by default; only ``run_loop`` sets it, so a single-shot session is
    #: unchanged.
    done_check: DoneCheck | None = None


class AgentBackend(Protocol):
    name: str
    capabilities: BackendCapabilities

    async def run(self, task: str, ctx: SessionContext) -> RunOutcome:
        """Execute ``task`` to completion or a terminal condition."""
        ...

    async def cancel(self) -> None:
        """Request cooperative cancellation (SIGINT path)."""
        ...
