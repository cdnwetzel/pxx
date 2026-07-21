"""Expose pxx memory as an MCP stdio server (spec 2025-11-25 subset).

Other agents (Claude Code, goose, opencode) can then search/add/list pxx
observations over newline-delimited JSON-RPC 2.0 on stdin/stdout. Only
``initialize``, ``tools/list`` and ``tools/call`` are implemented; anything
else gets JSON-RPC error ``-32601``. Notifications never get a response.

stdout carries RPC traffic only — diagnostics go to stderr via logging.
This module is the ``pxx-mcp`` console script (``pxx mcp``).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

from .. import __version__
from .client import PROTOCOL_VERSION

log = logging.getLogger("pxx.mcp.server")

SERVER_NAME = "pxx-memory"

PARSE_ERROR = -32700
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602

TOOLS: list[dict[str, Any]] = [
    {
        "name": "memory_search",
        "description": "Search pxx persistent memory for prior observations.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "search query"},
                "k": {"type": "integer", "description": "max results", "default": 8},
            },
            "required": ["query"],
        },
    },
    {
        "name": "memory_add",
        "description": "Store an observation in pxx persistent memory.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "observation text"},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "optional tags",
                },
            },
            "required": ["content"],
        },
    },
    {
        "name": "memory_list",
        "description": "List recent pxx memory observations.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "max results", "default": 20},
            },
        },
    },
]


class _RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        self.code = code
        super().__init__(message)


def _format_observation(obs: Any) -> str:
    content = getattr(obs, "content", None)
    if content is None:
        return str(obs)
    tags = getattr(obs, "tags", None) or []
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except json.JSONDecodeError:
            tags = [tags]
    suffix = f"  [tags: {', '.join(str(t) for t in tags)}]" if tags else ""
    return f"- {content}{suffix}"


async def _call_tool(
    store: Any, project: str, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Dispatch a tool call to the memory store; returns an MCP tool result."""
    try:
        if name == "memory_search":
            query = str(arguments.get("query", ""))
            k = int(arguments.get("k", 8))
            hits = await asyncio.to_thread(store.search, project, query, k=k)
            text = "\n".join(_format_observation(h) for h in hits) or "no matches"
        elif name == "memory_add":
            content = str(arguments.get("content", ""))
            if not content:
                raise _RpcError(INVALID_PARAMS, "memory_add requires 'content'")
            tags = [str(t) for t in arguments.get("tags") or []]
            obs_id = await asyncio.to_thread(
                store.add, project, "note", content, tags=tags, source="mcp"
            )
            text = f"stored observation {obs_id}"
        elif name == "memory_list":
            limit = int(arguments.get("limit", 20))
            items = await asyncio.to_thread(store.list, project)
            text = "\n".join(_format_observation(o) for o in items[:limit]) or "empty"
        else:
            raise _RpcError(INVALID_PARAMS, f"unknown tool {name!r}")
    except _RpcError:
        raise
    except Exception as exc:  # tool-level failure -> MCP isError, not RPC error
        log.exception("memory tool %s failed", name)
        return {
            "content": [{"type": "text", "text": f"error: {exc}"}],
            "isError": True,
        }
    return {"content": [{"type": "text", "text": text}], "isError": False}


async def _handle_request(
    store: Any, project: str, method: str, params: dict[str, Any]
) -> dict[str, Any]:
    """Resolve an RPC method to a result; raises _RpcError when unknown."""
    if method == "initialize":
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": __version__},
        }
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        return await _call_tool(
            store,
            project,
            str(params.get("name", "")),
            dict(params.get("arguments") or {}),
        )
    raise _RpcError(METHOD_NOT_FOUND, f"method not found: {method}")


async def handle_line(store: Any, project: str, line: bytes) -> dict[str, Any] | None:
    """Handle one input line; returns the response message, or None for
    notifications. Pure dispatch — no I/O — so it is unit-testable."""
    try:
        message = json.loads(line)
    except json.JSONDecodeError:
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": PARSE_ERROR, "message": "parse error"},
        }
    if not isinstance(message, dict) or "method" not in message:
        return {
            "jsonrpc": "2.0",
            "id": message.get("id") if isinstance(message, dict) else None,
            "error": {"code": INVALID_PARAMS, "message": "invalid request"},
        }
    method = str(message["method"])
    req_id = message.get("id")
    if method.startswith("notifications/") or req_id is None:
        return None  # notifications never get a response
    try:
        result = await _handle_request(store, project, method, dict(message.get("params") or {}))
    except _RpcError as exc:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": exc.code, "message": str(exc)},
        }
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


async def _stdio_streams() -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
    transport, protocol = await loop.connect_write_pipe(
        asyncio.streams.FlowControlMixin, sys.stdout
    )
    writer = asyncio.StreamWriter(transport, protocol, reader, loop)
    return reader, writer


async def run_server(
    memory_db_path: str | Path,
    *,
    project: str | None = None,
    store: Any = None,
    reader: asyncio.StreamReader | None = None,
    writer: asyncio.StreamWriter | None = None,
) -> None:
    """Serve memory tools over stdio until EOF on stdin.

    ``store``/``reader``/``writer`` are injection points for tests; by
    default a real :class:`MemoryStore` is opened and process stdio is used.
    """
    if store is None:
        from ..memory.store import MemoryStore

        store = MemoryStore(Path(memory_db_path))
    if project is None:
        project = Path.cwd().name
    if reader is None or writer is None:
        reader, writer = await _stdio_streams()
    log.info("pxx-mcp serving project %r from %s", project, memory_db_path)
    while True:
        line = await reader.readline()
        if not line:
            break
        if not line.strip():
            continue
        try:
            response = await handle_line(store, project, line)
        except Exception:
            log.exception("unhandled error processing request")
            continue
        if response is not None:
            writer.write((json.dumps(response) + "\n").encode())
            await writer.drain()


def main(argv: list[str] | None = None) -> None:
    """Console entry point (``pxx-mcp``)."""
    parser = argparse.ArgumentParser(
        prog="pxx-mcp",
        description="Expose pxx persistent memory as an MCP stdio server.",
    )
    parser.add_argument(
        "--db",
        default="~/.pxx/memory.db",
        help="path to the memory SQLite database (default: ~/.pxx/memory.db)",
    )
    parser.add_argument(
        "--project",
        default=None,
        help="project namespace for observations (default: current directory name)",
    )
    args = parser.parse_args(argv)
    db_path = Path(args.db).expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        asyncio.run(run_server(db_path, project=args.project))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
