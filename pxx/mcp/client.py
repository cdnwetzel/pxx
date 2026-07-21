"""Minimal MCP stdio client (spec 2025-11-25 subset).

Newline-delimited JSON-RPC 2.0 over a spawned subprocess' stdin/stdout.
Only ``initialize``, ``tools/list`` and ``tools/call`` are implemented —
enough to surface remote tools through pxx's ``ToolRegistry`` with
namespaced names ``mcp__<server>__<tool>``.

Protocol failures raise :class:`McpClientError`; the session layer treats
them as best-effort telemetry (an unavailable MCP server never gates a run).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

from .. import __version__
from ..errors import PxxError

log = logging.getLogger("pxx.mcp.client")

PROTOCOL_VERSION = "2025-11-25"
REQUEST_TIMEOUT = 30.0


class McpClientError(PxxError):
    """An MCP server failed to start, timed out, or returned an error."""


class StdioMcpClient:
    """JSON-RPC 2.0 client over a subprocess' stdio."""

    def __init__(self, name: str, process: asyncio.subprocess.Process) -> None:
        self.name = name
        self._proc = process
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._write_lock = asyncio.Lock()
        self._closed = False
        self._reader_task = asyncio.create_task(self._read_loop())

    # -- lifecycle ------------------------------------------------------

    @classmethod
    async def connect(
        cls,
        name: str,
        command: tuple[str, ...],
        timeout: float = REQUEST_TIMEOUT,  # noqa: ASYNC109 - RPC deadline, not asyncio scope
    ) -> StdioMcpClient:
        """Spawn ``command``, perform the MCP handshake, return the client."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError as exc:
            raise McpClientError(f"cannot start MCP server {name!r} ({command!r}): {exc}") from exc
        client = cls(name, proc)
        try:
            await client._request(
                "initialize",
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "pxx", "version": __version__},
                },
                timeout=timeout,
            )
        except Exception:
            await client.close()
            raise
        await client._notify("notifications/initialized", {})
        return client

    async def close(self) -> None:
        """Terminate the subprocess and stop the reader task."""
        if self._closed:
            return
        self._closed = True
        self._reader_task.cancel()
        self._fail_all(McpClientError(f"MCP server {self.name!r} closed"))
        if self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except TimeoutError:
                self._proc.kill()
                await self._proc.wait()

    # -- MCP operations --------------------------------------------------

    async def list_tools(self) -> list[dict[str, Any]]:
        """Return the server's tool descriptors (``tools/list``)."""
        result = await self._request("tools/list", {})
        return list(result.get("tools", []))

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        """Call a remote tool; returns concatenated text content."""
        result = await self._request("tools/call", {"name": name, "arguments": arguments or {}})
        text = "\n".join(
            str(c.get("text", ""))
            for c in result.get("content", [])
            if isinstance(c, dict) and c.get("type") == "text"
        )
        if result.get("isError"):
            raise McpClientError(f"tool {name!r} failed: {text}")
        return text

    # -- JSON-RPC plumbing ------------------------------------------------

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})

    async def _request(
        self,
        method: str,
        params: dict[str, Any],
        timeout: float = REQUEST_TIMEOUT,  # noqa: ASYNC109 - RPC deadline, not asyncio scope
    ) -> dict[str, Any]:
        if self._closed:
            raise McpClientError(f"MCP client {self.name!r} is closed")
        self._next_id += 1
        req_id = self._next_id
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[req_id] = future
        try:
            await self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
            return await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError as exc:
            raise McpClientError(
                f"MCP server {self.name!r} timed out on {method!r} ({timeout}s)"
            ) from exc
        finally:
            self._pending.pop(req_id, None)

    async def _send(self, message: dict[str, Any]) -> None:
        assert self._proc.stdin is not None
        data = (json.dumps(message) + "\n").encode()
        try:
            async with self._write_lock:
                self._proc.stdin.write(data)
                await self._proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise McpClientError(f"MCP server {self.name!r} closed stdin: {exc}") from exc

    async def _read_loop(self) -> None:
        assert self._proc.stdout is not None
        try:
            while True:
                line = await self._proc.stdout.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    log.warning("MCP server %s sent non-JSON line: %.200r", self.name, line)
                    continue
                self._dispatch(message)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("MCP reader for %s failed", self.name)
        finally:
            if not self._closed:
                self._fail_all(McpClientError(f"MCP server {self.name!r} closed the connection"))

    def _dispatch(self, message: dict[str, Any]) -> None:
        req_id = message.get("id")
        if req_id is None or not isinstance(req_id, int):
            return  # server->client request/notification: not supported, ignore
        future = self._pending.get(req_id)
        if future is None or future.done():
            return
        if "error" in message:
            err = message["error"] or {}
            future.set_exception(
                McpClientError(f"MCP error {err.get('code')}: {err.get('message', 'unknown')}")
            )
        else:
            future.set_result(message.get("result") or {})

    def _fail_all(self, exc: Exception) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(exc)


# -- ToolRegistry bridge ---------------------------------------------------


@dataclass(frozen=True)
class _McpToolSpec:
    """Fallback when pxx.tools.ToolSpec is unavailable (duck-typed)."""

    name: str
    description: str
    parameters: dict[str, Any]
    mutating: bool


def _make_tool_spec(name: str, description: str, parameters: dict[str, Any], mutating: bool) -> Any:
    """Build a pxx ToolSpec when available, else a duck-typed equivalent."""
    try:
        from ..tools import ToolSpec
    except Exception:  # pxx.tools not present / not ready
        return _McpToolSpec(name, description, parameters, mutating)
    try:
        return ToolSpec(
            name=name, description=description, parameters=parameters, mutating=mutating
        )
    except TypeError:
        return _McpToolSpec(name, description, parameters, mutating)


class _McpTool:
    """A pxx Tool that proxies to a remote MCP tool."""

    def __init__(self, client: StdioMcpClient, descriptor: dict[str, Any]) -> None:
        self._client = client
        self._remote_name = str(descriptor.get("name", ""))
        self.spec = _make_tool_spec(
            name=f"mcp__{client.name}__{self._remote_name}",
            description=str(descriptor.get("description") or f"MCP tool {self._remote_name}"),
            parameters=dict(descriptor.get("inputSchema") or {"type": "object", "properties": {}}),
            mutating=True,  # conservative: remote side effects are unknown
        )

    async def run(self, args: dict[str, Any], ctx: Any = None) -> str:
        return await self._client.call_tool(self._remote_name, args or {})


async def register_mcp_tools(client: StdioMcpClient, registry: Any) -> int:
    """Wrap each remote tool as a pxx Tool on ``registry``; returns count."""
    tools = await client.list_tools()
    for descriptor in tools:
        registry.register(_McpTool(client, descriptor))
    return len(tools)
