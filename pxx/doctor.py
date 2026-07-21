"""Health checks: ``pxx doctor``.

Reports on the Python runtime, loaded config files, directory writability,
endpoint reachability + tool-calling capability, and optional binaries. Hard
checks (python, config, directories) failing make the CLI exit non-zero; soft
checks (endpoints, tool calling, optional binaries) are warnings only.
Nothing here crashes: every probe is best-effort and reported, never raised.
"""

from __future__ import annotations

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


async def _tool_calling_check(
    spec: ModelRef,
    *,
    timeout: float = 2.0,  # noqa: ASYNC109 - httpx probe timeout, not asyncio scope
) -> Check | None:
    """Probe one endpoint for tool-calling support (F8).

    The native backend — and therefore every ``pxx loop`` run — needs an
    endpoint that accepts a ``tools`` array. Ollama supports tool calling out
    of the box (skipped). Fail-soft: any probe failure is a warning line,
    never a doctor failure.
    """
    if spec.provider == "ollama":
        return None
    name = f"tool-calling:{spec.model}"
    headers = {"Authorization": f"Bearer {spec.api_key}"} if spec.api_key else {}
    payload = {
        "model": spec.model,
        "messages": [{"role": "user", "content": "ping"}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "noop",
                    "description": "no-op probe",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        "max_tokens": 1,
    }
    try:
        async with _client_factory(timeout) as client:
            resp = await client.post(
                f"{spec.endpoint}/v1/chat/completions", json=payload, headers=headers
            )
    except Exception as exc:
        return Check(name, False, f"probe failed ({exc!r:.120})", hard=False)
    if resp.status_code == 200:
        return Check(name, True, "tool calling supported", hard=False)
    if resp.status_code == 400 and _TOOL_CHOICE_ERROR in resp.text:
        return Check(
            name,
            False,
            "reachable, but tool calling is DISABLED — native backend and 'pxx loop' "
            "will fail. vLLM: relaunch with --enable-auto-tool-choice "
            "--tool-call-parser <parser>",
            hard=False,
        )
    return Check(
        name,
        False,
        f"probe returned HTTP {resp.status_code} ({resp.text[:120]})",
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
