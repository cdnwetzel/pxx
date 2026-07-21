"""run_shell — the most dangerous tool, gated hardest.

Policy (fail-closed, per DESIGN.md):
- ``AUTO``: allowed (registry already ran PreToolUse hooks).
- ``EDIT``: allowed only when at least one PreToolUse hook is configured —
  the hook ran before this tool executed, so reaching ``run()`` means it
  allowed the call. No hook configured -> denied.
- ``ASK`` / ``PLAN``: always denied.

Execution: ``/bin/sh -c`` via asyncio subprocess, combined stdout+stderr
capped at 32 KiB, 60s default timeout. When ``ctx.sandbox_shell`` is set the
command is wrapped in ``sandbox-exec`` (macOS) or ``bubblewrap`` (Linux) when
the binary is available, denying writes outside the scope root.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from ..errors import HooksMissing, ScopeViolation
from ..safety import PermissionMode
from . import ToolContext, ToolSpec, tool_schema

#: Combined stdout+stderr cap.
MAX_OUTPUT_BYTES = 32 * 1024
DEFAULT_TIMEOUT = 60


def _has_pre_hooks(ctx: ToolContext) -> bool:
    """Whether any PreToolUse hook is configured (fail-closed: unknown -> False)."""
    return bool(getattr(ctx.hooks, "has_pre_hooks", False))


def seatbelt_profile(root: Path) -> str:
    """macOS sandbox-exec profile: deny writes outside ``root`` and tmp."""
    return "\n".join(
        [
            "(version 1)",
            "(allow default)",
            "(deny file-write*)",
            f'(allow file-write* (subpath "{root}"))',
            '(allow file-write* (subpath "/tmp"))',
            '(allow file-write* (subpath "/private/tmp"))',
            '(allow file-write* (literal "/dev/null"))',
        ]
    )


def _wrap_sandbox(ctx: ToolContext, command: str, profile_dir: Path) -> list[str]:
    """Build the argv for a sandboxed invocation, or plain /bin/sh -c."""
    root = ctx.scope.root
    if sys.platform == "darwin" and shutil.which("sandbox-exec"):
        profile = profile_dir / "pxx-seatbelt.sb"
        profile.write_text(seatbelt_profile(root))
        return ["sandbox-exec", "-f", str(profile), "/bin/sh", "-c", command]
    if sys.platform.startswith("linux") and shutil.which("bwrap"):
        return [
            "bwrap",
            "--ro-bind",
            "/",
            "/",
            "--bind",
            str(root),
            str(root),
            "--tmpfs",
            "/tmp",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--",
            "/bin/sh",
            "-c",
            command,
        ]
    # sandbox requested but no sandboxer available: run unsandboxed.
    return ["/bin/sh", "-c", command]


class RunShell:
    spec = ToolSpec(
        name="run_shell",
        description=(
            "Run a shell command via /bin/sh -c. Combined stdout+stderr is "
            "returned (capped at 32 KiB) with the exit code. Availability "
            "depends on the session permission mode."
        ),
        parameters=tool_schema(
            {
                "command": {"type": "string", "description": "Shell command to run."},
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default 60).",
                    "default": DEFAULT_TIMEOUT,
                },
            },
            required=["command"],
        ),
        mutating=True,
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        mode = ctx.permission
        if mode in (PermissionMode.ASK, PermissionMode.PLAN):
            raise ScopeViolation(f"run_shell is never allowed in permission mode '{mode}'")
        if mode is PermissionMode.EDIT and not _has_pre_hooks(ctx):
            raise HooksMissing(
                "run_shell in permission mode 'edit' requires a configured "
                "PreToolUse hook (fail-closed); none is configured. "
                'Add a [[hooks]] entry (event="PreToolUse", matcher="run_shell", '
                "command=...) to pxx.toml — see docs/CONFIG.md §hooks"
            )

        command = str(args.get("command", ""))
        timeout = float(args.get("timeout") or DEFAULT_TIMEOUT)

        with tempfile.TemporaryDirectory(prefix="pxx-sandbox-") as tmp:
            if ctx.sandbox_shell:
                argv = _wrap_sandbox(ctx, command, Path(tmp))
            else:
                argv = ["/bin/sh", "-c", command]
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(ctx.scope.root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            timed_out = False
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except TimeoutError:
                timed_out = True
                proc.kill()
                stdout, _ = await proc.communicate()

        output = stdout.decode(errors="replace")
        if len(output.encode()) > MAX_OUTPUT_BYTES:
            output = output.encode()[:MAX_OUTPUT_BYTES].decode(errors="replace")
            output += f"\n… output truncated at {MAX_OUTPUT_BYTES} bytes"
        if timed_out:
            return f"{output}\n[timed out after {timeout:g}s]"
        return f"{output}\n[exit {proc.returncode}]"
