"""Model clients — the ONE seam that separates the deterministic mechanism from the live run.

The runtime asks a model for exactly one typed action given a capsule prompt. A
:class:`ScriptedModel` makes the negative-control suite hermetic (no network); the
:class:`OpenAICompatibleModel` drives a real 4B/8K local model for the Neo receipt.
"""

from __future__ import annotations

import json
import re
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


class OpenAICompatibleModel:
    """Minimal ``POST {base_url}/chat/completions`` client for a local OpenAI-compatible server
    (Ollama, vLLM, llama.cpp). ``base_url`` is the API root **including** ``/v1``
    (e.g. ``http://localhost:11434/v1``). The model is instructed to reply with ONE JSON action;
    the first JSON object in its reply is parsed. httpx is imported lazily so the offline
    negative-control suite has zero network dependency."""

    def __init__(self, base_url: str, model: str, timeout: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def act(self, capsule_prompt: str) -> dict:
        import httpx  # lazy: only the live run needs it

        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": capsule_prompt}],
            "temperature": 0,
            "stream": False,
        }
        # base_url is the OpenAI API root INCLUDING /v1 (e.g. http://host:11434/v1); append the
        # path only, so a base already ending in /v1 does not become /v1/v1/chat/completions.
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(f"{self.base_url}/chat/completions", json=payload)
            resp.raise_for_status()
            body = resp.json()
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            # fail closed on a malformed envelope — never let it crash the loop
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
