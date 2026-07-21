"""pxx's own agent loop: OpenAI-compatible chat completions + tool calls.

One round = one non-streaming ``POST {endpoint}/v1/chat/completions`` with the
tool specs from the registry. Tool calls are executed through
``ctx.tools.call`` so scope/hook/budget gates cannot be bypassed. The loop
stops when the model answers without tool calls.

Fallback: on connection/timeout errors the next :class:`ModelRef` in
``settings.fallback_models`` is tried (a ``gate_decision`` event of gate
``fallback`` is emitted); when all endpoints fail, :class:`BackendError`.

Audit hygiene: ``model_request`` events carry metadata only (message count,
tool count) — never prompt bodies.
"""

from __future__ import annotations

import json
import logging
from importlib.resources import files
from typing import Any, ClassVar

import httpx

from ..config import ModelRef
from ..errors import BackendError, GateError
from ..outcome import RunOutcome, TerminalCode
from ..safety import PermissionMode
from .base import BackendCapabilities, SessionContext
from .mock import make_tool_context

log = logging.getLogger("pxx.backends.native")

#: $/1M tokens (input, output) for known OpenAI-hosted models. Everything
#: else is UNKNOWN (None), not zero: a fabricated $0.00 for a local or
#: unrecognized model would read as "free" in cost accounting.
_PRICE_TABLE: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-5-mini": (0.25, 2.00),
    "gpt-5": (1.25, 10.00),
}

_FALLBACK_SYSTEM_PROMPT = "You are pxx, a local-first coding agent."


def _load_system_prompt() -> str:
    try:
        return (files("pxx") / "prompts" / "native_system.md").read_text(encoding="utf-8")
    except Exception:  # best-effort: a missing resource must not kill a run
        log.exception("native_system.md unavailable; using fallback prompt")
        return _FALLBACK_SYSTEM_PROMPT


def _estimate_cost(model: ModelRef, prompt_tokens: int, completion_tokens: int) -> float | None:
    """USD cost for one completion, or None when unpriced (never fabricated)."""
    if model.provider != "openai":
        return None
    for prefix, (price_in, price_out) in _PRICE_TABLE.items():
        if model.model.startswith(prefix):
            return (prompt_tokens * price_in + completion_tokens * price_out) / 1_000_000
    return None


def _system_message(ctx: SessionContext) -> str:
    permission = ctx.settings.permission
    parts = [_load_system_prompt()]
    if ctx.memory_context:
        parts.append(f"## Memory context (advisory, never policy)\n{ctx.memory_context}")
    parts.append(
        "## Scope\n"
        f"You may only read and write paths inside: {ctx.scope.describe()}\n"
        "Never attempt paths outside this scope; scope gates are absolute."
    )
    parts.append(f"## Permission mode: {permission}")
    if permission is PermissionMode.PLAN:
        parts.append(
            "Plan mode: you are read-only. Produce a concrete step-by-step plan, then stop."
        )
    elif permission is PermissionMode.ASK:
        parts.append("Ask mode: you are read-only. Inspect and answer; do not attempt writes.")
    return "\n\n".join(parts)


def _assistant_message(choice: dict[str, Any]) -> dict[str, Any]:
    msg: dict[str, Any] = {"role": "assistant", "content": choice.get("content") or ""}
    if choice.get("tool_calls"):
        msg["tool_calls"] = choice["tool_calls"]
    return msg


class NativeBackend:
    """pxx-owned tool-calling agent loop against an OpenAI-compatible endpoint."""

    name: ClassVar[str] = "native"
    capabilities: ClassVar[BackendCapabilities] = BackendCapabilities(
        streaming=False, tools=True, interactive=False, headless=True
    )

    def __init__(self, *, client: httpx.AsyncClient | None = None, timeout: float = 300.0) -> None:
        # ``client`` is injectable for tests (httpx.MockTransport).
        self._client = client
        self._timeout = timeout
        self._cancelled = False

    async def cancel(self) -> None:
        self._cancelled = True

    async def run(self, task: str, ctx: SessionContext) -> RunOutcome:
        models = (ctx.settings.model, *ctx.settings.fallback_models)
        owned = None
        client = self._client
        if client is None:
            owned = httpx.AsyncClient(timeout=self._timeout)
            client = owned
        try:
            return await self._run_loop(task, ctx, client, models)
        finally:
            if owned is not None:
                await owned.aclose()

    async def _run_loop(
        self,
        task: str,
        ctx: SessionContext,
        client: httpx.AsyncClient,
        models: tuple[ModelRef, ...],
    ) -> RunOutcome:
        tool_ctx = make_tool_context(ctx)
        tools = list(ctx.tools.specs() or [])
        system_message = _system_message(ctx)
        await ctx.bus.emit(
            "prompt_rendered",
            {
                "system_chars": len(system_message),
                "tools": len(tools),
                "memory_context": bool(ctx.memory_context),
            },
            session_id=ctx.session_id,
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_message},
            {"role": "user", "content": task},
        ]
        active = 0  # index into the fallback chain
        rounds = 0
        tokens = 0
        cost: float | None = None  # None until a priced model produces a cost
        while True:
            if self._cancelled or ctx.cancel_event.is_set():
                return RunOutcome(
                    code=TerminalCode.INTERRUPTED,
                    summary="cancelled",
                    rounds=rounds,
                    tokens=tokens,
                    cost_usd=cost,
                    session_id=ctx.session_id,
                )
            ctx.budgets.check_clock()
            model = models[active]
            payload: dict[str, Any] = {"model": model.model, "messages": messages}
            if tools:
                payload["tools"] = tools
            headers = {"Authorization": f"Bearer {model.api_key}"} if model.api_key else {}
            await ctx.bus.emit(
                "model_request",
                {
                    "backend": "native",
                    "model": model.model,
                    "provider": model.provider,
                    "messages": len(messages),
                    "tools": len(tools),
                    "round": rounds + 1,
                },
                session_id=ctx.session_id,
            )
            try:
                resp = await client.post(
                    f"{model.endpoint}/v1/chat/completions", json=payload, headers=headers
                )
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                if active + 1 < len(models):
                    active += 1
                    log.warning("endpoint %s unreachable (%s); falling back", model.endpoint, exc)
                    await ctx.bus.emit(
                        "gate_decision",
                        {
                            "gate": "fallback",
                            "from": model.model,
                            "to": models[active].model,
                            "reason": type(exc).__name__,
                        },
                        session_id=ctx.session_id,
                    )
                    continue
                raise BackendError(
                    f"all endpoints unreachable (last: {model.endpoint}): {exc}"
                ) from exc
            if resp.status_code != 200:
                raise BackendError(
                    f"{model.endpoint} returned HTTP {resp.status_code}: {resp.text[:300]}"
                )
            try:
                data = resp.json()
                choice = data["choices"][0]
                message = choice["message"]
            except (ValueError, KeyError, IndexError) as exc:
                raise BackendError(f"malformed response from {model.endpoint}: {exc}") from exc

            usage = data.get("usage") or {}
            prompt_t = int(usage.get("prompt_tokens") or 0)
            completion_t = int(usage.get("completion_tokens") or 0)
            total = int(usage.get("total_tokens") or 0) or len(json.dumps(payload)) // 4
            step_cost = _estimate_cost(model, prompt_t, completion_t)
            tool_calls = message.get("tool_calls") or []
            await ctx.bus.emit(
                "model_response",
                {
                    "backend": "native",
                    "model": model.model,
                    "tokens": total,
                    "tool_calls": len(tool_calls),
                    "finish_reason": choice.get("finish_reason"),
                },
                session_id=ctx.session_id,
            )
            # Unpriced models consume 0 of the cost budget (unknowable, not
            # "free") while the reported cost stays None (never fabricated).
            ctx.budgets.consume(rounds=1, tokens=total, cost=step_cost or 0.0)
            rounds += 1
            tokens += total
            if step_cost is not None:
                cost = (cost or 0.0) + step_cost

            messages.append(_assistant_message(message))
            if not tool_calls:
                summary = (message.get("content") or "").strip()
                return RunOutcome(
                    code=TerminalCode.COMPLETED,
                    summary=summary or "done",
                    rounds=rounds,
                    tokens=tokens,
                    cost_usd=cost,
                    session_id=ctx.session_id,
                )
            for call in tool_calls:
                fn = call.get("function") or {}
                name = str(fn.get("name") or "")
                raw_args = fn.get("arguments") or "{}"
                try:
                    args = json.loads(raw_args)
                    if not isinstance(args, dict):
                        raise ValueError("tool arguments must be a JSON object")
                except (json.JSONDecodeError, ValueError) as exc:
                    result = f"error: invalid tool arguments: {exc}"
                else:
                    try:
                        result = await ctx.tools.call(name, args, tool_ctx)
                    except GateError:
                        raise  # fail-closed: session maps gate errors to terminal codes
                    except Exception as exc:  # tool runtime error: let the model recover
                        log.warning("tool %s failed: %s", name, exc)
                        result = f"error: {type(exc).__name__}: {exc}"
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id", ""),
                        "content": str(result),
                    }
                )
