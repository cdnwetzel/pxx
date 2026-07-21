"""Memory tools: recall_memory and remember.

Both operate on ``ctx.memory`` (a MemoryStore, owned by the memory group) and
degrade gracefully to a friendly string when memory is disabled. Neither is
``mutating`` — remembering is telemetry, not a filesystem write, so it works
in read-only permission modes. Memory is context, never policy.
"""

from __future__ import annotations

from typing import Any

from . import ToolContext, ToolSpec, tool_schema

_NO_MEMORY = "memory is not available in this session (disabled or failed to open)"


def _project(ctx: ToolContext) -> str:
    return ctx.cwd.name


class RecallMemory:
    spec = ToolSpec(
        name="recall_memory",
        description="Search long-term memory for observations relevant to a query.",
        parameters=tool_schema(
            {
                "query": {"type": "string", "description": "What to recall."},
                "k": {
                    "type": "integer",
                    "description": "Max observations to return (default 5).",
                    "default": 5,
                },
            },
            required=["query"],
        ),
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        if ctx.memory is None:
            return _NO_MEMORY
        query = str(args.get("query", ""))
        k = int(args.get("k") or 5)
        results = ctx.memory.search(_project(ctx), query, k=k)
        if not results:
            return f"no memories matching {query!r}"
        lines = []
        for obs in results:
            content = str(getattr(obs, "content", obs))
            kind = getattr(obs, "kind", "note")
            lines.append(f"- [{kind}] {content[:500]}")
        return "\n".join(lines)


class Remember:
    spec = ToolSpec(
        name="remember",
        description=(
            "Store a fact/decision/gotcha in long-term memory for future sessions. "
            "Available in every permission mode (memory is telemetry, not a file write)."
        ),
        parameters=tool_schema(
            {
                "content": {"type": "string", "description": "What to remember."},
                "tags": {
                    "type": "string",
                    "description": "Optional comma-separated tags.",
                    "default": "",
                },
            },
            required=["content"],
        ),
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        if ctx.memory is None:
            return _NO_MEMORY
        content = str(args.get("content", ""))
        if not content.strip():
            return "error: content must not be empty"
        tags = [t.strip() for t in str(args.get("tags") or "").split(",") if t.strip()]
        obs_id = ctx.memory.add(
            _project(ctx),
            "note",
            content,
            tags=tags,
            source="tool",
            session_id=ctx.session_id,
        )
        return f"remembered (id {obs_id})"
