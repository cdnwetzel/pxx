"""Built-in tool surface for agent backends.

A *tool* is a small async callable with a JSON-schema spec. Backends never
execute model tool calls directly — everything goes through
:class:`ToolRegistry.call`, which enforces the trusted control plane in order:

1. ``PreToolUse`` hooks (raise :class:`~pxx.errors.HookDenied` — propagates),
2. mutating tools require ``permission.can_write`` (raise ScopeViolation),
3. ``tool_call`` / ``tool_result`` events on the bus (previews truncated),
4. ``PostToolUse`` hooks.

Tool *failures* (missing file, bad regex, non-zero exit) are returned as
strings — they are data for the model, not crashes. Gate errors
(``ScopeViolation`` / ``HookDenied`` / ``BudgetExceeded``) always propagate.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from ..errors import GateError
from ..events import EventBus
from ..safety import HookRunner, PermissionMode, ScopeGate

if TYPE_CHECKING:
    from ..memory.store import MemoryStore

log = logging.getLogger("pxx.tools")

#: Max chars of a tool result/args preview placed into event data.
EVENT_PREVIEW_CHARS = 500
#: Max chars per string value of tool args placed into event data
#: (audit is metadata-only: file contents must not land in the audit log).
ARGS_VALUE_PREVIEW_CHARS = 200


@dataclass(frozen=True)
class ToolSpec:
    """Static description of a tool, advertised to the model."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON-schema object
    mutating: bool = False


@dataclass
class ToolContext:
    """Everything a tool needs: the gates plus ambient session state.

    Constructed by the session layer. ``sandbox_shell`` mirrors
    ``Settings.sandbox_shell`` (see pxx/tools/shell.py). ``profile`` is the
    broker's permission profile (None -> built-in defaults).
    """

    scope: ScopeGate
    hooks: HookRunner
    permission: PermissionMode
    bus: EventBus
    cwd: Path
    memory: MemoryStore | None = None
    session_id: str = ""
    sandbox_shell: bool = False
    profile: Any = None  # pxx.broker.PermissionProfile (lazy to avoid cycle)


@runtime_checkable
class Tool(Protocol):
    spec: ToolSpec

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        """Execute the tool. Return a string result for the model."""
        ...


def _preview_args(args: dict[str, Any]) -> dict[str, Any]:
    """Truncate long string arg values so events/audit stay metadata-only."""

    def _trim(value: Any) -> Any:
        if isinstance(value, str) and len(value) > ARGS_VALUE_PREVIEW_CHARS:
            return value[:ARGS_VALUE_PREVIEW_CHARS] + "…[truncated]"
        return value

    return {k: _trim(v) for k, v in args.items()}


class ToolRegistry:
    """Registry + gated dispatcher for all tools available to a backend."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        name = tool.spec.name
        if name in self._tools:
            raise ValueError(f"duplicate tool registration: {name}")
        self._tools[name] = tool

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def specs(self) -> list[dict[str, Any]]:
        """OpenAI function-tool schema for every registered tool."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.spec.name,
                    "description": t.spec.description,
                    "parameters": t.spec.parameters,
                },
            }
            for t in self._tools.values()
        ]

    async def call(self, name: str, args: dict[str, Any], ctx: ToolContext) -> str:
        """Run one tool call through the action broker (the SINGLE
        authorization authority) and then execute it.

        Gate errors propagate; ordinary tool failures are returned as strings.
        """
        from ..broker import ActionBroker, PermissionProfile, classify

        tool = self._tools.get(name)
        if tool is None:
            return f"error: unknown tool {name!r} (available: {', '.join(sorted(self._tools))})"

        profile = ctx.profile or PermissionProfile.defaults()
        broker = ActionBroker(profile)
        # classify + authorize: profile check, scope, PreToolUse hooks, and the
        # tool_action_proposed / policy_decision events all live in the broker.
        # Denials raise ScopeViolation/HookDenied and propagate.
        action = classify(name, tool.spec, args)
        await broker.authorize(action, ctx)

        await ctx.bus.emit(
            "tool_call",
            {"tool": name, "args": _preview_args(args)},
            session_id=ctx.session_id,
        )

        error: str | None = None
        try:
            result = await tool.run(args, ctx)
        except GateError:
            raise  # gate denials are never swallowed into model data
        except Exception as exc:  # tool failure is data for the model
            log.debug("tool %s failed", name, exc_info=True)
            error = f"error: {type(exc).__name__}: {exc}"
            result = error

        await ctx.bus.emit(
            "tool_result",
            {
                "tool": name,
                "result_preview": result[:EVENT_PREVIEW_CHARS],
                "error": error is not None,
            },
            session_id=ctx.session_id,
        )

        await ctx.hooks.run_post(name, args, result)  # HookDenied propagates
        return result


def default_registry() -> ToolRegistry:
    """Registry with all built-in tools (~8; small models degrade past ~10)."""
    from .fs import EditFile, ListFiles, ReadFile, SearchFiles, WriteFile
    from .memory_tools import RecallMemory, Remember
    from .shell import RunShell

    registry = ToolRegistry()
    for tool in (
        ReadFile(),
        WriteFile(),
        EditFile(),
        ListFiles(),
        SearchFiles(),
        RunShell(),
        RecallMemory(),
        Remember(),
    ):
        registry.register(tool)
    return registry


def tool_schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    """Small helper for building JSON-schema parameter objects."""
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


__all__ = [
    "EVENT_PREVIEW_CHARS",
    "Tool",
    "ToolContext",
    "ToolRegistry",
    "ToolSpec",
    "default_registry",
    "tool_schema",
]
