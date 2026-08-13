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
import re
from importlib.resources import files
from typing import Any, ClassVar

import httpx

from ..config import ModelRef, native_timeout
from ..errors import BackendError, GateError
from ..outcome import RunOutcome, TerminalCode
from ..safety import PermissionMode
from ..truthfulness import check_quote_grounding
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


#: Public accessor for the native system prompt, so other modules (e.g.
#: ``doctor``'s realistic tool-calling probe) can depend on an explicit contract
#: rather than the module-private ``_load_system_prompt``.
def load_system_prompt() -> str:
    return _load_system_prompt()


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


# A structured tool call the serving layer returned as *text*: the model
# emitted a well-formed <tool_call> block but the response's tool_calls array
# is empty (R-007: an OpenAI tools surface returning calls as prose). Without
# detection the run "completes" having described its edits instead of making
# them.
_PROSE_TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)
_PROSE_NUDGE_LIMIT = 2

#: Tools that mutate the working tree — the only turns worth a done-signal probe
#: (nothing changed on-disk otherwise, so the oracle's verdict cannot have moved).
#: Mirrors the WRITE class in ``broker._TOOL_CLASSES``.
_EDIT_TOOLS = frozenset({"write_file", "edit_file"})


def _prose_tool_call(content: str, tool_names: frozenset[str] = frozenset()) -> bool:
    """True only for a *well-formed* call — prose that merely mentions the
    tag must not trigger (pxx edits its own docs). Three shapes count, each
    observed live: a <tool_call> block whose body is a JSON object with a
    name; a final answer that IS a bare tool-call object (2026-07-28: raw
    edit_file JSON, diff_lines=0); and a tool-call object embedded mid-prose
    (2026-07-29: "please confirm…" followed by the read_file JSON). The
    embedded shape additionally requires the name to be a *registered* tool
    — free-floating JSON in prose is otherwise too common to trust."""
    for match in _PROSE_TOOL_CALL_RE.finditer(content):
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and isinstance(data.get("name"), str) and data["name"]:
            return True
    stripped = content.strip()
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict) and isinstance(data.get("name"), str) and "arguments" in data:
            return True
    if tool_names:
        decoder = json.JSONDecoder()
        # whitespace-tolerant: pretty-printed calls ({\n  "name": …) count too
        for candidate in re.finditer(r'\{\s*"name"', content):
            try:
                data, _ = decoder.raw_decode(content, candidate.start())
            except json.JSONDecodeError:
                data = None
            if isinstance(data, dict) and data.get("name") in tool_names and "arguments" in data:
                return True
    return False


def _assistant_message(choice: dict[str, Any]) -> dict[str, Any]:
    msg: dict[str, Any] = {"role": "assistant", "content": choice.get("content") or ""}
    if choice.get("tool_calls"):
        msg["tool_calls"] = choice["tool_calls"]
    return msg


def _model_not_found(status: int, body: str) -> bool:
    """A REACHABLE endpoint that does not serve the requested model id — an HTTP
    404, or a 400/422 whose body names a missing model (vLLM / Ollama / OpenAI
    phrasings). Distinct from an unreachable endpoint: it should still advance
    the ``[[fallback_models]]`` chain rather than hard-fail (F3)."""
    if status == 404:
        return True
    if status not in (400, 422):
        return False
    low = body.lower()
    return "model" in low and ("not found" in low or "does not exist" in low or "not exist" in low)


class NativeBackend:
    """pxx-owned tool-calling agent loop against an OpenAI-compatible endpoint."""

    name: ClassVar[str] = "native"
    capabilities: ClassVar[BackendCapabilities] = BackendCapabilities(
        streaming=False, tools=True, interactive=False, headless=True
    )

    def __init__(
        self, *, client: httpx.AsyncClient | None = None, timeout: float | None = None
    ) -> None:
        # ``client`` is injectable for tests (httpx.MockTransport).
        if timeout is None:
            timeout = native_timeout()  # PXX_NATIVE_TIMEOUT; config.py owns the env read
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
        tool_names = frozenset(str((t.get("function") or {}).get("name") or "") for t in tools) - {
            ""
        }
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
        prose_nudges = 0
        cost: float | None = None  # None until a priced model produces a cost
        # Grounding sources for the content-truthfulness check: everything the model reads
        # (tool results) or writes (edit-tool args), accumulated across this run's rounds.
        grounding: list[str] = []
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
                body = resp.text[:300]
                if resp.status_code == 400 and "exceed_context_size" in resp.text:
                    # Ollama >= 0.32 fails loud when the request overflows
                    # num_ctx (older versions silently truncated). Surface
                    # the fix, not the raw JSON.
                    raise BackendError(
                        f"request exceeds the model's context window on {model.endpoint}. "
                        f"Raise the model's num_ctx (Modelfile: PARAMETER num_ctx 12288+) "
                        f"or use a larger-context model — see docs/TUTORIAL.md "
                        f"troubleshooting. [{body}]"
                    )
                # F3: a reachable endpoint that doesn't serve this model id (404,
                # or a "model not found" body) is not a dead end — advance the
                # fallback chain, same as an unreachable endpoint, instead of
                # hard-failing MODEL_UNAVAILABLE.
                if _model_not_found(resp.status_code, body) and active + 1 < len(models):
                    log.warning(
                        "endpoint %s does not serve %s (HTTP %s); falling back",
                        model.endpoint,
                        model.model,
                        resp.status_code,
                    )
                    active += 1
                    await ctx.bus.emit(
                        "gate_decision",
                        {
                            "gate": "fallback",
                            "from": model.model,
                            "to": models[active].model,
                            "reason": f"model_not_found_{resp.status_code}",
                        },
                        session_id=ctx.session_id,
                    )
                    continue
                raise BackendError(f"{model.endpoint} returned HTTP {resp.status_code}: {body}")
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
                if _prose_tool_call(summary, tool_names):
                    prose_nudges += 1
                    log.warning(
                        "tool call returned as prose by %s (nudge %d/%d)",
                        model.endpoint,
                        prose_nudges,
                        _PROSE_NUDGE_LIMIT,
                    )
                    await ctx.bus.emit(
                        "tool_call_prose",
                        {
                            "backend": "native",
                            "model": model.model,
                            "round": rounds,
                            "nudges": prose_nudges,
                        },
                        session_id=ctx.session_id,
                    )
                    if prose_nudges > _PROSE_NUDGE_LIMIT:
                        raise BackendError(
                            f"{model.endpoint} keeps returning tool calls as prose — the "
                            f"serving layer is dropping tool_calls. Verify the endpoint's "
                            f"tool-call support (vLLM: --enable-auto-tool-choice "
                            f"--tool-call-parser <parser>); see docs/TUTORIAL.md "
                            f"troubleshooting (“describes edits instead of making them”)."
                        )
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "Your tool call arrived as plain text, not as a structured "
                                "tool call — it was NOT executed. Re-issue it through the "
                                "tools API; never print <tool_call> blocks or raw tool-call "
                                "JSON in your answer."
                            ),
                        }
                    )
                    continue
                # Content-truthfulness (advisory): the model's final narration must not quote
                # code absent from everything it read or wrote. FAIL-SAFE (an advisory check
                # must never break a run) and non-blocking (a warning event, not a gate).
                try:
                    ungrounded = check_quote_grounding(summary, grounding)
                    if ungrounded:
                        await ctx.bus.emit(
                            "content_truthfulness",
                            {
                                "backend": "native",
                                "model": model.model,
                                "round": rounds,
                                "ungrounded": len(ungrounded),
                                "samples": [f.quote[:120] for f in ungrounded[:3]],
                                "advisory": True,
                            },
                            session_id=ctx.session_id,
                        )
                        log.warning(
                            "content-truthfulness (advisory): %d quoted span(s) in the summary "
                            "grounded in neither read nor written content (%s)",
                            len(ungrounded),
                            model.endpoint,
                        )
                except Exception as exc:  # advisory MUST NOT affect the run's outcome
                    log.debug("content-truthfulness check skipped: %s", exc)
                return RunOutcome(
                    code=TerminalCode.COMPLETED,
                    summary=summary or "done",
                    rounds=rounds,
                    tokens=tokens,
                    cost_usd=cost,
                    session_id=ctx.session_id,
                )
            edited = False
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
                    # written args (edit-tool content etc.) ground the model's later quotes
                    grounding.extend(str(v) for v in args.values() if isinstance(v, str))
                if name in _EDIT_TOOLS and not str(result).startswith("error:"):
                    edited = True
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id", ""),
                        "content": str(result),
                    }
                )
                grounding.append(str(result))  # tool-read content grounds later quotes

            # Done-signal early-exit: this turn changed files, so ask the injected
            # oracle whether the on-disk edit already passes every mandatory gate
            # the driving loop would enforce. If it does, stop now instead of
            # burning the rest of the per-turn budget — the coder often keeps
            # calling tools past a finished solution (over-work, R-014/R-015). The
            # loop re-verifies and runs its own review gate on this COMPLETED
            # result, so this only ever ends a run that is objectively done.
            if edited and ctx.done_check is not None and await ctx.done_check():
                await ctx.bus.emit(
                    "gate_decision",
                    {"gate": "done_signal", "round": rounds, "allowed": True},
                    session_id=ctx.session_id,
                )
                return RunOutcome(
                    code=TerminalCode.COMPLETED,
                    summary="done-signal: verified edit — exited before budget exhaustion",
                    rounds=rounds,
                    tokens=tokens,
                    cost_usd=cost,
                    session_id=ctx.session_id,
                )
