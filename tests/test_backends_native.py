"""Tests for pxx.backends.native — httpx.MockTransport, no network."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from pxx.backends.base import SessionContext
from pxx.backends.native import NativeBackend
from pxx.config import ModelRef, Settings
from pxx.errors import BackendError, BudgetExceeded
from pxx.events import EventBus
from pxx.outcome import TerminalCode
from pxx.safety import BudgetGuard, Budgets, HookRunner, ScopeGate


class FakeRegistry:
    def __init__(self, result: str = "tool output") -> None:
        self.calls: list[tuple[str, dict]] = []
        self.result = result

    def specs(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {"name": "read_file", "parameters": {"type": "object"}},
            }
        ]

    async def call(self, name: str, args: dict, ctx) -> str:
        self.calls.append((name, args))
        return self.result


def make_ctx(tmp_path, tools=None, *, settings=None, budgets=None) -> SessionContext:
    settings = settings or Settings(model=ModelRef(base_url="http://test.local"))
    return SessionContext(
        settings=settings,
        bus=EventBus(),
        scope=ScopeGate(tmp_path),
        hooks=HookRunner(),
        budgets=BudgetGuard(budgets or settings.budgets),
        tools=tools or FakeRegistry(),
        memory=None,
        session_id="test",
        project=tmp_path.name,
        cwd=tmp_path,
        cancel_event=asyncio.Event(),
    )


def completion(content: str, *, total_tokens: int = 10) -> dict:
    return {
        "choices": [
            {"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
        ],
        "usage": {
            "prompt_tokens": total_tokens - 4,
            "completion_tokens": 4,
            "total_tokens": total_tokens,
        },
    }


def tool_call_round(call_id: str, name: str, arguments: str) -> dict:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": name, "arguments": arguments},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"total_tokens": 12},
    }


def make_backend(handler) -> NativeBackend:
    return NativeBackend(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def test_single_round_completion(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "http://test.local/v1/chat/completions"
        payload = json.loads(request.content)
        assert payload["tools"]  # registry specs are advertised
        assert payload["messages"][0]["role"] == "system"
        assert payload["messages"][-1] == {"role": "user", "content": "do it"}
        return httpx.Response(200, json=completion("finished the task"))

    ctx = make_ctx(tmp_path)
    outcome = asyncio.run(make_backend(handler).run("do it", ctx))
    assert outcome.code is TerminalCode.COMPLETED
    assert outcome.summary == "finished the task"
    assert outcome.rounds == 1
    assert outcome.tokens == 10
    # metadata-only model_request: no prompt bodies on the bus
    req = next(e for e in ctx.bus.history if e.kind == "model_request")
    assert "messages" in req.data and isinstance(req.data["messages"], int)
    assert "do it" not in json.dumps(req.data)


def test_tool_call_round_trip(tmp_path):
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(200, json=tool_call_round("c1", "read_file", '{"path": "x.py"}'))
        return httpx.Response(200, json=completion("all done"))

    tools = FakeRegistry(result="file contents here")
    ctx = make_ctx(tmp_path, tools)
    outcome = asyncio.run(make_backend(handler).run("inspect", ctx))
    assert outcome.code is TerminalCode.COMPLETED
    assert outcome.rounds == 2
    assert tools.calls == [("read_file", {"path": "x.py"})]
    # tool result was appended as a role=tool message keyed by tool_call_id
    tool_msgs = [m for m in requests[1]["messages"] if m["role"] == "tool"]
    assert tool_msgs == [{"role": "tool", "tool_call_id": "c1", "content": "file contents here"}]
    # assistant tool_calls preserved in history
    assert requests[1]["messages"][2]["tool_calls"][0]["id"] == "c1"


def test_invalid_tool_arguments_are_fed_back_not_fatal(tmp_path):
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(200, json=tool_call_round("c9", "read_file", "{not json"))
        return httpx.Response(200, json=completion("recovered"))

    tools = FakeRegistry()
    ctx = make_ctx(tmp_path, tools)
    outcome = asyncio.run(make_backend(handler).run("inspect", ctx))
    assert outcome.code is TerminalCode.COMPLETED
    assert tools.calls == []  # never invoked with garbage args
    tool_msgs = [m for m in requests[1]["messages"] if m["role"] == "tool"]
    assert tool_msgs[0]["tool_call_id"] == "c9"
    assert "error" in tool_msgs[0]["content"]


def test_fallback_chain_on_connect_error(tmp_path):
    settings = Settings(
        model=ModelRef(model="m1", base_url="http://down.local"),
        fallback_models=(ModelRef(model="m2", base_url="http://up.local"),),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if "down.local" in str(request.url):
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200, json=completion("via fallback"))

    ctx = make_ctx(tmp_path, settings=settings)
    outcome = asyncio.run(make_backend(handler).run("task", ctx))
    assert outcome.code is TerminalCode.COMPLETED
    assert outcome.summary == "via fallback"
    fallbacks = [
        e for e in ctx.bus.history if e.kind == "gate_decision" and e.data.get("gate") == "fallback"
    ]
    assert len(fallbacks) == 1
    assert fallbacks[0].data["to"] == "m2"


def test_all_endpoints_down_raises_backend_error(tmp_path):
    settings = Settings(
        model=ModelRef(model="m1", base_url="http://down1.local"),
        fallback_models=(ModelRef(model="m2", base_url="http://down2.local"),),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope", request=request)

    with pytest.raises(BackendError, match="all endpoints unreachable"):
        asyncio.run(make_backend(handler).run("task", make_ctx(tmp_path, settings=settings)))


def test_http_error_raises_backend_error(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal boom")

    with pytest.raises(BackendError, match="HTTP 500"):
        asyncio.run(make_backend(handler).run("task", make_ctx(tmp_path)))


def test_round_budget_trips(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=tool_call_round("c1", "read_file", "{}"))

    ctx = make_ctx(tmp_path, budgets=Budgets(max_rounds=1))
    with pytest.raises(BudgetExceeded):
        asyncio.run(make_backend(handler).run("task", ctx))


def test_cancel_event_interrupts(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("must not be called")

    ctx = make_ctx(tmp_path)
    ctx.cancel_event.set()
    outcome = asyncio.run(make_backend(handler).run("task", ctx))
    assert outcome.code is TerminalCode.INTERRUPTED


def test_openai_cost_estimated_local_free(tmp_path):
    settings = Settings(
        model=ModelRef(provider="openai", model="gpt-4o-mini", base_url="http://test.local")
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=completion("done", total_tokens=2000))

    outcome = asyncio.run(make_backend(handler).run("task", make_ctx(tmp_path, settings=settings)))
    assert outcome.cost_usd > 0

    ctx_local = make_ctx(tmp_path)  # default provider ollama -> unpriced
    outcome_local = asyncio.run(make_backend(handler).run("task", ctx_local))
    assert outcome_local.cost_usd is None  # unknown cost is None, never fabricated


# --- B10.3: prompt_rendered emitted when the native backend renders the prompt --------


def test_prompt_rendered_emitted(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=completion("hello"))

    ctx = make_ctx(tmp_path)
    asyncio.run(make_backend(handler).run("hi", ctx))
    events = [e for e in ctx.bus.history if e.kind == "prompt_rendered"]
    assert len(events) == 1
    assert events[0].data["system_chars"] > 0
