"""Model clients — the ONE seam that separates the deterministic mechanism from the live run.

The runtime asks a model for exactly one typed action given a capsule prompt. A
:class:`ScriptedModel` makes the negative-control suite hermetic (no network); the
:class:`OpenAICompatibleModel` drives a real 4B/8K local model for the Neo receipt.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from typing import Protocol


class ModelClient(Protocol):
    """Return the next typed action as a decoded JSON object, given the capsule prompt."""

    def act(self, capsule_prompt: str) -> dict: ...


class ScriptedModel:
    """Replays a fixed action script (dicts), or calls a function of the last prompt.

    Deterministic and offline — this is what proves the mechanism can fail on the bad case.
    """

    def __init__(self, actions: list[dict] | Callable[[str], dict]) -> None:
        self._actions = actions
        self._i = 0

    def act(self, capsule_prompt: str) -> dict:
        if callable(self._actions):
            return self._actions(capsule_prompt)
        if self._i >= len(self._actions):
            # ran out of script: stop honestly rather than loop
            return {"type": "BLOCKED", "reason": "script_exhausted"}
        action = self._actions[self._i]
        self._i += 1
        return action


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _int_or_none(v) -> int | None:
    """Coerce a usage token count to int; a bool, non-numeric string, or garbage -> None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    # str.isdigit() is True for non-ASCII digits like "²" that int() then REJECTS — require ASCII
    if isinstance(v, str) and v.isascii() and v.isdigit():
        return int(v)
    return None


def consume_sse(lines, started: float):
    """Parse an OpenAI-style SSE stream into (content, ttft_s, usage). Malformed chunks (non-JSON,
    ``[]``, odd ``choices`` shapes) are SKIPPED, never raised — one bad chunk must not crash the
    loop. If no valid content arrives, content is None (act() then returns BLOCKED)."""
    parts: list[str] = []
    ttft_s: float | None = None
    usage: dict | None = None
    for line in lines:
        if not line or not line.startswith("data:"):
            continue
        data = line[len("data:") :].strip()
        if data == "[DONE]":
            break
        try:
            chunk = json.loads(data)
            if not isinstance(chunk, dict):
                continue
            if isinstance(chunk.get("usage"), dict):
                usage = chunk["usage"]
            choices = chunk.get("choices") or []
            first = choices[0] if isinstance(choices, list) and choices else None
            delta = first.get("delta", {}).get("content") if isinstance(first, dict) else None
            if isinstance(delta, str) and delta:  # only text deltas — a non-str would break join
                if ttft_s is None:
                    ttft_s = time.perf_counter() - started
                parts.append(delta)
        except (json.JSONDecodeError, AttributeError, TypeError, IndexError):
            continue
    return ("".join(parts) if parts else None), ttft_s, usage


class OpenAICompatibleModel:
    """Minimal ``POST {base_url}/chat/completions`` client for a local OpenAI-compatible server
    (Ollama, vLLM, llama.cpp). ``base_url`` is the API root **including** ``/v1``
    (e.g. ``http://localhost:11434/v1``). The model is instructed to reply with ONE JSON action;
    the first JSON object in its reply is parsed. httpx is imported lazily so the offline
    negative-control suite has zero network dependency."""

    def __init__(
        self, base_url: str, model: str, timeout: float = 120.0, stream: bool = False
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.stream = stream
        # per-call performance samples, in call order (== action order). Consumed by run_neo to
        # build the receipt's performance block. Each entry: latency_s, ttft_s, prompt_tokens,
        # completion_tokens (ttft/tokens are None when the endpoint doesn't report them).
        self.stats: list[dict] = []

    def act(self, capsule_prompt: str) -> dict:
        import httpx  # lazy: only the live run needs it

        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": capsule_prompt}],
            "temperature": 0,
            "stream": self.stream,
        }
        if self.stream:  # ask servers that support it to include usage in the final chunk
            payload["stream_options"] = {"include_usage": True}
        # base_url is the OpenAI API root INCLUDING /v1 (e.g. http://host:11434/v1); append the
        # path only, so a base already ending in /v1 does not become /v1/v1/chat/completions.
        # Any network / HTTP-status / decode error is an honest stop, not a crash: fail closed.
        started = time.perf_counter()
        try:
            content, ttft_s, usage = (
                self._stream_call(httpx, payload, started)
                if self.stream
                else self._blocking_call(httpx, payload)
            )
        except (httpx.HTTPError, ValueError) as exc:  # ValueError covers a non-JSON body
            self._record(started, None, None)
            return {"type": "BLOCKED", "reason": f"model_endpoint_error:{type(exc).__name__}"}
        self._record(started, ttft_s, usage)
        if content is None:
            return {"type": "BLOCKED", "reason": "malformed_completion_envelope"}
        if not isinstance(content, str):
            return {"type": "BLOCKED", "reason": "completion_content_not_text"}
        match = _JSON_RE.search(content)
        if not match:
            return {"type": "BLOCKED", "reason": "model_returned_no_json_action"}
        try:
            obj = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {"type": "BLOCKED", "reason": "model_returned_invalid_json"}
        return obj if isinstance(obj, dict) else {"type": "BLOCKED", "reason": "action_not_object"}

    def _blocking_call(self, httpx, payload: dict):
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(f"{self.base_url}/chat/completions", json=payload)
            resp.raise_for_status()
            body = resp.json()
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            content = None
        return content, None, body.get("usage") if isinstance(body, dict) else None

    def _stream_call(self, httpx, payload: dict, started: float):
        """Stream deltas so we can time the FIRST content token (TTFT ≈ prefill time)."""
        with httpx.Client(timeout=self.timeout) as client:
            with client.stream("POST", f"{self.base_url}/chat/completions", json=payload) as resp:
                resp.raise_for_status()
                return consume_sse(resp.iter_lines(), started)

    def _record(self, started: float, ttft_s: float | None, usage: dict | None) -> None:
        u = usage if isinstance(usage, dict) else {}  # a non-dict usage must not break .get
        self.stats.append(
            {
                "latency_s": time.perf_counter() - started,
                "ttft_s": ttft_s,
                # coerce token counts to int (some servers send strings); garbage -> None, never
                # a value that would blow up sum() in build_performance
                "prompt_tokens": _int_or_none(u.get("prompt_tokens")),
                "completion_tokens": _int_or_none(u.get("completion_tokens")),
            }
        )
