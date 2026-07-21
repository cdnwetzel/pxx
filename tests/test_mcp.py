"""MCP tests — fake stdio servers via `python -c` subprocesses, no network.

Covers the client (handshake, tools/list, tools/call, error paths, registry
bridge) and the memory server (pure dispatch + a real end-to-end round trip
through a spawned subprocess running pxx.mcp.server.run_server).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from pxx.mcp.client import McpClientError, StdioMcpClient, register_mcp_tools
from pxx.mcp.server import TOOLS, handle_line

REPO_ROOT = str(Path(__file__).resolve().parent.parent)

# A minimal newline-delimited JSON-RPC MCP server speaking the subset we need.
FAKE_SERVER = r"""
import json, sys

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    method = msg.get("method", "")
    if method.startswith("notifications/") or "id" not in msg:
        continue
    rid = msg["id"]
    if method == "initialize":
        result = {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "fake", "version": "0.1"}}
    elif method == "tools/list":
        result = {"tools": [
            {"name": "echo", "description": "echo arguments",
             "inputSchema": {"type": "object", "properties": {}}},
            {"name": "fail", "description": "always fails"},
        ]}
    elif method == "tools/call":
        name = msg["params"]["name"]
        if name == "echo":
            result = {"content": [{"type": "text",
                                   "text": json.dumps(msg["params"]["arguments"])}],
                      "isError": False}
        elif name == "fail":
            result = {"content": [{"type": "text", "text": "it broke"}],
                      "isError": True}
        else:
            sys.stdout.write(json.dumps(
                {"jsonrpc": "2.0", "id": rid,
                 "error": {"code": -32602, "message": "unknown tool"}}) + "\n")
            sys.stdout.flush()
            continue
    else:
        sys.stdout.write(json.dumps(
            {"jsonrpc": "2.0", "id": rid,
             "error": {"code": -32601, "message": "method not found"}}) + "\n")
        sys.stdout.flush()
        continue
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}) + "\n")
    sys.stdout.flush()
"""


def _spawn(script: str) -> tuple[str, ...]:
    return (sys.executable, "-u", "-c", script)


def test_client_handshake_list_and_call() -> None:
    async def go() -> None:
        client = await StdioMcpClient.connect("fake", _spawn(FAKE_SERVER))
        try:
            tools = await client.list_tools()
            assert [t["name"] for t in tools] == ["echo", "fail"]
            out = await client.call_tool("echo", {"hello": "world"})
            assert json.loads(out) == {"hello": "world"}
        finally:
            await client.close()

    asyncio.run(go())


def test_client_tool_iserror_raises() -> None:
    async def go() -> None:
        client = await StdioMcpClient.connect("fake", _spawn(FAKE_SERVER))
        try:
            with pytest.raises(McpClientError, match="it broke"):
                await client.call_tool("fail", {})
        finally:
            await client.close()

    asyncio.run(go())


def test_client_rpc_error_raises() -> None:
    async def go() -> None:
        client = await StdioMcpClient.connect("fake", _spawn(FAKE_SERVER))
        try:
            with pytest.raises(McpClientError, match="-32602"):
                await client.call_tool("nonexistent", {})
        finally:
            await client.close()

    asyncio.run(go())


def test_client_connect_bad_command() -> None:
    async def go() -> None:
        with pytest.raises(McpClientError, match="cannot start MCP server"):
            await StdioMcpClient.connect("nope", ("pxx-no-such-binary-zzz",))

    asyncio.run(go())


def test_register_mcp_tools_wraps_remote_tools() -> None:
    class FakeRegistry:
        def __init__(self) -> None:
            self.tools = []

        def register(self, tool) -> None:
            self.tools.append(tool)

    async def go() -> None:
        client = await StdioMcpClient.connect("fake", _spawn(FAKE_SERVER))
        try:
            registry = FakeRegistry()
            count = await register_mcp_tools(client, registry)
            assert count == 2
            names = [t.spec.name for t in registry.tools]
            assert names == ["mcp__fake__echo", "mcp__fake__fail"]
            assert all(t.spec.mutating for t in registry.tools)  # conservative
            echo = registry.tools[0]
            out = await echo.run({"x": 1}, ctx=None)
            assert json.loads(out) == {"x": 1}
        finally:
            await client.close()

    asyncio.run(go())


# -- server-side dispatch (pure, no I/O) ------------------------------------


class FakeStore:
    def __init__(self) -> None:
        self.items: list[SimpleNamespace] = []

    def add(self, project, kind, content, *, tags=(), source="", **kwargs):
        self.items.append(SimpleNamespace(content=content, tags=list(tags)))
        return len(self.items)

    def search(self, project, query, *, k=8):
        return [o for o in self.items if query.lower() in o.content.lower()][:k]

    def list(self, project):
        return list(self.items)


def _req(rid: int, method: str, params: dict | None = None) -> bytes:
    return json.dumps(
        {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
    ).encode()


def test_server_initialize_and_tool_listing() -> None:
    async def go() -> None:
        store = FakeStore()
        resp = await handle_line(store, "proj", _req(1, "initialize"))
        assert resp["result"]["serverInfo"]["name"] == "pxx-memory"
        assert resp["result"]["protocolVersion"] == "2025-11-25"
        resp = await handle_line(store, "proj", _req(2, "tools/list"))
        assert [t["name"] for t in resp["result"]["tools"]] == [
            "memory_search",
            "memory_add",
            "memory_list",
        ]
        assert TOOLS  # module-level schema table is what we serve

    asyncio.run(go())


def test_server_tool_roundtrip_dispatch() -> None:
    async def go() -> None:
        store = FakeStore()
        resp = await handle_line(
            store,
            "proj",
            _req(
                1,
                "tools/call",
                {"name": "memory_add", "arguments": {"content": "use ruff", "tags": ["style"]}},
            ),
        )
        assert resp["result"]["isError"] is False
        assert "stored observation 1" in resp["result"]["content"][0]["text"]

        resp = await handle_line(
            store,
            "proj",
            _req(2, "tools/call", {"name": "memory_search", "arguments": {"query": "ruff"}}),
        )
        text = resp["result"]["content"][0]["text"]
        assert "use ruff" in text and "style" in text

        resp = await handle_line(
            store,
            "proj",
            _req(3, "tools/call", {"name": "memory_list", "arguments": {"limit": 5}}),
        )
        assert "use ruff" in resp["result"]["content"][0]["text"]

    asyncio.run(go())


def test_server_unknown_method_and_tool_errors() -> None:
    async def go() -> None:
        store = FakeStore()
        resp = await handle_line(store, "proj", _req(1, "resources/list"))
        assert resp["error"]["code"] == -32601

        resp = await handle_line(
            store, "proj", _req(2, "tools/call", {"name": "nope", "arguments": {}})
        )
        assert resp["error"]["code"] == -32602

        # store failure -> tool-level isError, not an RPC error
        class BoomStore(FakeStore):
            def list(self, project):
                raise RuntimeError("db gone")

        resp = await handle_line(
            BoomStore(),
            "proj",
            _req(3, "tools/call", {"name": "memory_list", "arguments": {}}),
        )
        assert resp["result"]["isError"] is True
        assert "db gone" in resp["result"]["content"][0]["text"]

    asyncio.run(go())


def test_server_notifications_and_parse_errors() -> None:
    async def go() -> None:
        store = FakeStore()
        assert (
            await handle_line(
                store, "proj", b'{"jsonrpc":"2.0","method":"notifications/initialized"}'
            )
            is None
        )
        resp = await handle_line(store, "proj", b"not json at all")
        assert resp["error"]["code"] == -32700

    asyncio.run(go())


# -- end-to-end: real client <-> real server subprocess ----------------------

# Runs pxx.mcp.server.run_server with an in-process stub store (avoids any
# dependency on the real MemoryStore, which is built by another group).
SERVER_SCRIPT = f"""import asyncio, sys; sys.path.insert(0, {REPO_ROOT!r})
from types import SimpleNamespace
from pxx.mcp.server import run_server
class FakeStore:
    def __init__(self): self.items = []
    def add(self, project, kind, content, *, tags=(), source=''):
        self.items.append(SimpleNamespace(content=content, tags=list(tags)))
        return len(self.items)
    def search(self, project, query, *, k=8):
        return [o for o in self.items if query in o.content][:k]
    def list(self, project): return list(self.items)
asyncio.run(run_server('unused.db', store=FakeStore(), project='e2e'))
"""


def test_end_to_end_client_to_real_server() -> None:
    async def go() -> None:
        client = await StdioMcpClient.connect("pxx-memory", _spawn(SERVER_SCRIPT))
        try:
            tools = await client.list_tools()
            assert [t["name"] for t in tools] == [
                "memory_search",
                "memory_add",
                "memory_list",
            ]
            assert "stored observation 1" in await client.call_tool(
                "memory_add", {"content": "remember this", "tags": ["e2e"]}
            )
            assert "remember this" in await client.call_tool("memory_search", {"query": "remember"})
            assert "remember this" in await client.call_tool("memory_list", {})
        finally:
            await client.close()

    asyncio.run(go())
