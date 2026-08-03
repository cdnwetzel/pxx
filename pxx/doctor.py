"""Health checks: ``pxx doctor``.

Reports on the Python runtime, loaded config files, directory writability,
endpoint reachability + tool-calling capability, and optional binaries. Hard
checks (python, config, directories) failing make the CLI exit non-zero; soft
checks (endpoints, tool calling, optional binaries) are warnings only.
Nothing here crashes: every probe is best-effort and reported, never raised.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

from .config import ModelRef, Settings


def _client_factory(timeout: float) -> httpx.AsyncClient:
    """Build the probe client. Monkeypatched by tests to use MockTransport."""
    return httpx.AsyncClient(timeout=timeout)


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    hard: bool = False  # hard failures make `pxx doctor` exit non-zero


def _dir_check(name: str, path: Path) -> Check:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".doctor-probe"
        probe.write_text("ok")
        probe.unlink()
        return Check(name, True, f"writable ({path})", hard=True)
    except OSError as exc:
        return Check(name, False, f"not writable: {path} ({exc})", hard=True)


def _config_check(cwd: Path) -> Check:
    candidates = [
        Path("~/.config/pxx/config.toml").expanduser(),
        cwd / "pxx.toml",
        cwd / ".pxx" / "config.toml",
    ]
    loaded = [str(p) for p in candidates if p.is_file()]
    # Settings were already loaded successfully by the caller, so config
    # parses by construction; this check reports which files contributed.
    detail = ", ".join(loaded) if loaded else "defaults only (no config files)"
    return Check("config", True, detail, hard=True)


#: Substring of the vLLM 400 body when the server was launched without tool
#: calling (`--enable-auto-tool-choice --tool-call-parser`).
_TOOL_CHOICE_ERROR = "tool choice requires --enable-auto-tool-choice"

#: A realistic tool for the probe — a file read is the most common first move a
#: coding agent makes, so a model that can drive `pxx loop` will reach for it.
_PROBE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a UTF-8 text file from the repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "repo-relative file path"}
                },
                "required": ["path"],
            },
        },
    }
]
#: An unambiguous task that a tool-using agent answers with a call, never prose.
_PROBE_USER = (
    "Read the file README.md so you can summarize it. Use the read_file tool "
    'with path "README.md". Do not answer in prose — make the tool call.'
)
_PROBE_FALLBACK_SYSTEM = (
    "You are pxx, a local-first coding agent. You act only by calling the "
    "provided tools, never by describing actions in prose."
)


def _probe_system_prompt() -> str:
    """The real native-backend system prompt, so the probe puts the model under
    the same instruction load a `pxx loop` round does (F2). Best-effort: if the
    resource can't be imported, fall back to a compact equivalent."""
    try:
        from .backends.native import _load_system_prompt

        return _load_system_prompt()
    except Exception:
        return _PROBE_FALLBACK_SYSTEM


async def _tool_calling_check(
    spec: ModelRef,
    *,
    timeout: float = 15.0,  # noqa: ASYNC109 - httpx probe timeout (a real generation), not asyncio scope
) -> Check | None:
    """Probe one endpoint for *usable* tool-calling (F2/F8).

    The native backend — and therefore every ``pxx loop`` run — needs a model
    that emits a structured ``tool_call`` under a real agent context, not just
    an endpoint that accepts a ``tools`` array. A toy one-token probe lies:
    some models (notably small instruct models on constrained hardware) accept
    ``tools`` and return HTTP 200, yet answer in PROSE once the context is the
    size of an actual loop prompt — which strands the loop. So this probe sends
    the real system prompt plus an unambiguous file-read task and requires the
    response to contain a tool call. Runs for every provider, ollama included
    (that is where the degradation shows up). Fail-soft: any probe failure is a
    warning line, never a doctor failure.
    """
    name = f"tool-calling:{spec.model}"
    headers = {"Authorization": f"Bearer {spec.api_key}"} if spec.api_key else {}
    payload = {
        "model": spec.model,
        "messages": [
            {"role": "system", "content": _probe_system_prompt()},
            {"role": "user", "content": _PROBE_USER},
        ],
        "tools": _PROBE_TOOLS,
        "tool_choice": "auto",
        "max_tokens": 256,
    }
    try:
        async with _client_factory(timeout) as client:
            resp = await client.post(
                f"{spec.endpoint}/v1/chat/completions", json=payload, headers=headers
            )
    except Exception as exc:
        return Check(name, False, f"probe failed ({exc!r:.120})", hard=False)
    if resp.status_code == 400 and _TOOL_CHOICE_ERROR in resp.text:
        return Check(
            name,
            False,
            "reachable, but tool calling is DISABLED — native backend and 'pxx loop' "
            "will fail. vLLM: relaunch with --enable-auto-tool-choice "
            "--tool-call-parser <parser>",
            hard=False,
        )
    if resp.status_code != 200:
        return Check(
            name,
            False,
            f"probe returned HTTP {resp.status_code} ({resp.text[:120]})",
            hard=False,
        )
    try:
        message = resp.json()["choices"][0]["message"]
    except (ValueError, KeyError, IndexError, TypeError):
        return Check(name, False, "probe returned an unparseable 200 body", hard=False)
    if message.get("tool_calls"):
        return Check(name, True, "tool-calling verified under a realistic context", hard=False)
    return Check(
        name,
        False,
        "accepts `tools` but returned PROSE under a realistic context — this model "
        "may not reliably drive the native backend / 'pxx loop' on this hardware (F2). "
        "Pick a model verified to tool-call here, or serve it with a larger context.",
        hard=False,
    )


async def _endpoint_checks(settings: Settings) -> list[Check]:
    specs = [settings.model, *settings.fallback_models]
    try:
        from .router import probe_endpoints
    except ImportError:
        return [Check("endpoints", False, "router unavailable (skipped)", hard=False)]
    try:
        endpoints = await probe_endpoints(specs)
    except Exception as exc:  # probe failures are reported, never raised
        return [
            Check(f"endpoint:{s.model}", False, f"unreachable ({exc!r:.120})", hard=False)
            for s in specs
        ]
    checks = []
    for spec, endpoint in zip(specs, endpoints, strict=False):
        ok = bool(getattr(endpoint, "reachable", getattr(endpoint, "ok", False)))
        url = getattr(endpoint, "base_url", None) or spec.endpoint
        checks.append(
            Check(
                f"endpoint:{spec.model}",
                ok,
                f"reachable ({url})" if ok else f"unreachable ({url})",
                hard=False,
            )
        )
        if ok:
            # reachable != usable: the endpoint may not serve the configured
            # model at all. Mirror resolve_model's semantics: a single-model
            # endpoint auto-corrects at session start, so that's a pass with
            # a note; multi-model without ours is a failure.
            served = tuple(getattr(endpoint, "models", ()) or ())
            if not served and spec.provider == "ollama":
                # Ollama reliably lists models; empty means nothing pulled —
                # sessions will fail with BackendUnavailable despite the
                # green "reachable" line above. (Other providers may simply
                # not expose a list; stay quiet there.)
                checks.append(
                    Check(
                        f"model:{spec.model}",
                        False,
                        f"endpoint serves no models — ollama pull {spec.model}",
                        hard=False,
                    )
                )
            elif served and spec.model not in served:
                if len(served) == 1:
                    detail = f"not served; sessions auto-correct to {served[0]!r}"
                    model_ok = True
                else:
                    shown = ", ".join(served[:3]) + ("…" if len(served) > 3 else "")
                    detail = f"not served by {url} (has: {shown}) — pull or reconfigure"
                    model_ok = False
                checks.append(Check(f"model:{spec.model}", model_ok, detail, hard=False))
            tool_check = await _tool_calling_check(spec)
            if tool_check is not None:
                checks.append(tool_check)
    return checks


def _hook_coverage_check(settings: Settings) -> Check:
    """run_shell in edit mode fails closed without a matching PreToolUse hook
    (K9). Only edit mode is subject to HOOKS_MISSING; other modes are fine."""
    from .safety import PermissionMode

    if settings.permission is not PermissionMode.EDIT:
        return Check("hooks:run_shell", True, f"permission '{settings.permission}'", hard=False)
    covered = any(
        h.event == "PreToolUse" and (not h.matcher or h.matcher in "run_shell")
        for h in settings.hooks
    )
    if covered:
        return Check("hooks:run_shell", True, "PreToolUse hook covers run_shell", hard=False)
    return Check(
        "hooks:run_shell",
        False,
        "permission 'edit' but no PreToolUse hook matches run_shell — run_shell "
        "will fail closed (HOOKS_MISSING); see docs/CONFIG.md §hooks",
        hard=False,
    )


async def run_doctor(settings: Settings, cwd: Path | None = None) -> list[Check]:
    """Run all health checks against resolved ``settings``."""
    cwd = cwd or Path.cwd()
    checks: list[Check] = []

    py = sys.version_info
    checks.append(
        Check(
            "python",
            py >= (3, 11),
            f"{py.major}.{py.minor}.{py.micro} (>= 3.11 required)",
            hard=True,
        )
    )
    checks.append(_config_check(cwd))
    checks.append(_dir_check("memory_dir", settings.memory_dir))
    checks.append(_dir_check("state_dir", settings.state_dir))
    checks.append(_hook_coverage_check(settings))
    checks.extend(await _endpoint_checks(settings))

    for tool, hint in (
        ("aider", "optional: aider backend"),
        ("git", "optional: diff capture"),
        ("rg", "optional: fast search"),
    ):
        path = shutil.which(tool)
        if tool == "aider" and path:
            # present != working: a py3.13 aider install can crash on import.
            from .cli import _aider_health

            if not await asyncio.to_thread(_aider_health, path):
                checks.append(
                    Check(
                        "binary:aider",
                        False,
                        f"{path} is broken (--version fails) — auto backend "
                        "selection falls back to native",
                        hard=False,
                    )
                )
                continue
        checks.append(
            Check(f"binary:{tool}", bool(path), path or f"not found ({hint})", hard=False)
        )
    return checks


def print_report(checks: list[Check]) -> bool:
    """Print ✅/❌/⚠️ lines. Returns False when a hard check failed."""
    hard_ok = True
    for check in checks:
        if check.ok:
            icon = "✅"
        elif check.hard:
            icon = "❌"
            hard_ok = False
        else:
            icon = "⚠️"
        print(f"{icon} {check.name}: {check.detail}")
    return hard_ok
