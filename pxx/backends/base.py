"""Backend protocol — pxx owns the runtime; backends are pluggable executors.

A backend receives a task and a :class:`SessionContext` (which carries the
gates: scope, hooks, budgets, event bus, tools, memory) and drives one run.
Every model/tool event must be emitted on the bus; tool execution must go
through ``ctx.tools`` so policy cannot be bypassed.
"""

from __future__ import annotations

import asyncio
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


class AgentBackend(Protocol):
    name: str
    capabilities: BackendCapabilities

    async def run(self, task: str, ctx: SessionContext) -> RunOutcome:
        """Execute ``task`` to completion or a terminal condition."""
        ...

    async def cancel(self) -> None:
        """Request cooperative cancellation (SIGINT path)."""
        ...
