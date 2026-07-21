"""aider subprocess backend (optional dependency).

Delegates the edit to the ``aider`` CLI as an async subprocess. pxx still owns
policy: permission mode maps to aider's chat mode, scope/memory context is
passed via a temp ``--read`` file, stdout lines are streamed as
``model_response`` events, and file changes are captured via a pre/post
``git rev-parse HEAD`` diff (``git diff --stat pre..HEAD``) feeding
``file_changed`` events and the diff-lines budget.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import shutil
import tempfile
from asyncio.subprocess import PIPE, Process
from pathlib import Path
from typing import ClassVar

from ..config import ModelRef
from ..errors import BackendError, BackendUnavailable
from ..outcome import RunOutcome, TerminalCode
from ..safety import PermissionMode
from .base import BackendCapabilities, SessionContext

log = logging.getLogger("pxx.backends.aider")

INSTALL_HINT = "pip install pxx-orchestrator[aider]"

#: aider's own stdout marker for an applied edit (its reporting, not ours).
_APPLIED_EDIT_RE = re.compile(r"^Applied edit to\s+(.+?)\s*$")


class AiderBackend:
    """Runs ``aider --message <task>`` headless as an async subprocess."""

    name: ClassVar[str] = "aider"
    capabilities: ClassVar[BackendCapabilities] = BackendCapabilities(
        streaming=True, tools=False, interactive=False, headless=True
    )

    def __init__(self, *, aider_path: str | None = None) -> None:
        path = aider_path or shutil.which("aider")
        if not path:
            raise BackendUnavailable(
                f"aider binary not found on PATH; install with: {INSTALL_HINT}"
            )
        self._aider = path
        self._proc: Process | None = None
        self._cancelled = False

    async def cancel(self) -> None:
        self._cancelled = True
        if self._proc is not None and self._proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self._proc.terminate()

    # -- git helpers (isolated, monkeypatchable; work without a repo) --------

    async def _git(self, cwd: Path, *args: str) -> str | None:
        """Run a git command; return stdout or None when unavailable/failed."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", *args, cwd=cwd, stdout=PIPE, stderr=PIPE
            )
            out, _ = await proc.communicate()
        except OSError:
            return None
        if proc.returncode != 0:
            return None
        return out.decode(errors="replace").strip()

    # -- argv / env / context construction ------------------------------------

    @staticmethod
    def _model_string(model: ModelRef) -> str:
        if model.provider == "ollama":
            return f"ollama_chat/{model.model}"
        if model.provider == "openai":
            return model.model
        return f"openai/{model.model}"  # vllm / openai-compatible

    @staticmethod
    def _env(model: ModelRef) -> dict[str, str]:
        env = dict(os.environ)
        if model.provider == "ollama":
            env["OLLAMA_API_BASE"] = model.endpoint
        elif model.provider == "openai":
            if model.api_key:
                env["OPENAI_API_KEY"] = model.api_key
        else:  # vllm / openai-compatible
            base = model.endpoint
            if not base.endswith("/v1"):
                base += "/v1"
            env["OPENAI_API_BASE"] = base
            env["OPENAI_API_KEY"] = model.api_key or "dummy"
        return env

    @staticmethod
    def _context_text(ctx: SessionContext) -> str:
        lines = [
            "# pxx session context",
            "",
            f"Permission mode: {ctx.settings.permission}",
            f"Scope — only read/write paths under: {ctx.scope.describe()}",
            "Scope and permission gates are absolute; do not attempt to bypass them.",
        ]
        if ctx.memory_context:
            lines += ["", "## Memory context (advisory)", ctx.memory_context]
        return "\n".join(lines)

    def _argv(self, task: str, ctx: SessionContext, *, use_git: bool, read_file: str) -> list[str]:
        argv = [
            self._aider,
            "--message",
            task,
            "--yes-always",
            "--no-stream",
            "--no-pretty",
            "--model",
            self._model_string(ctx.settings.model),
        ]
        if ctx.settings.permission in (PermissionMode.ASK, PermissionMode.PLAN):
            argv += ["--chat-mode", "ask"]
        # EDIT/AUTO -> default code mode
        if not use_git:
            argv.append("--no-git")
        argv += ["--read", read_file]
        return argv

    # -- run ------------------------------------------------------------------

    @staticmethod
    def _parse_porcelain(status: str) -> tuple[set[str], bool]:
        """(untracked paths, tracked-dirty?) from ``git status --porcelain=v1``."""
        untracked: set[str] = set()
        tracked_dirty = False
        for line in status.splitlines():
            if line[:2] == "??":
                untracked.add(line[3:])
            elif line.strip():
                tracked_dirty = True
        return untracked, tracked_dirty

    async def run(self, task: str, ctx: SessionContext) -> RunOutcome:
        pre_head = await self._git(ctx.cwd, "rev-parse", "HEAD")
        use_git = pre_head is not None
        untracked_start: set[str] = set()
        tracked_dirty_start = False
        if use_git:
            status = await self._git(ctx.cwd, "status", "--porcelain=v1", "--untracked-files=all")
            untracked_start, tracked_dirty_start = self._parse_porcelain(status or "")
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", prefix="pxx-aider-context-", delete=False, encoding="utf-8"
        )
        try:
            tmp.write(self._context_text(ctx))
            tmp.close()
            argv = self._argv(task, ctx, use_git=use_git, read_file=tmp.name)
            env = self._env(ctx.settings.model)
            log.info("launching aider: %s", " ".join(argv))
            self._proc = await asyncio.create_subprocess_exec(
                *argv, cwd=ctx.cwd, env=env, stdout=PIPE, stderr=PIPE
            )
            lines, stderr_text = await self._pump(ctx)
            returncode = await self._proc.wait()
        finally:
            self._proc = None
            with contextlib.suppress(OSError):
                os.unlink(tmp.name)

        if self._cancelled or ctx.cancel_event.is_set():
            return RunOutcome(
                code=TerminalCode.INTERRUPTED,
                summary="cancelled",
                session_id=ctx.session_id,
            )
        if returncode != 0:
            raise BackendError(
                f"aider exited with code {returncode}: {stderr_text[-500:]}",
                code=TerminalCode.EDIT_FAILED,
            )

        diff_lines = 0
        changed_paths: list[str] = []
        if use_git:
            diff_lines, changed_paths = await self._report_diff(ctx, pre_head or "")
        ctx.budgets.consume(rounds=1, diff_lines=diff_lines)
        ctx.budgets.check_clock()

        # F4 + K2b: when every edit aider reports is outside the active
        # scope, the run is scope-blocked — reverted (below), never
        # projected as success.
        claimed = [m.group(1) for line in lines if (m := _APPLIED_EDIT_RE.search(line))]
        reported = list(dict.fromkeys([*changed_paths, *claimed]))
        if reported and all(not ctx.scope.in_scope(p) for p in reported):
            # K2b: the fence actually fences — undo the blocked run's commits
            # and its session-created droppings. Pre-existing state is
            # untouchable: never reset over user dirt (the K5 stash, when
            # present, is in refs/stash and unaffected by reset --hard),
            # never delete files that were already untracked at run start.
            reverted_to = ""
            not_reverted = ""
            if use_git and pre_head:
                if tracked_dirty_start:
                    not_reverted = (
                        "commits not reverted (pre-existing local changes); "
                        f"revert manually: git reset --hard {pre_head[:7]}"
                    )
                else:
                    await self._git(ctx.cwd, "reset", "--hard", pre_head)
                    reverted_to = pre_head[:7]
            dropped: list[str] = []
            if use_git:
                status_now = await self._git(
                    ctx.cwd, "status", "--porcelain=v1", "--untracked-files=all"
                )
                untracked_now, _ = self._parse_porcelain(status_now or "")
                for rel in sorted(untracked_now - untracked_start):
                    if not ctx.scope.in_scope(rel):
                        try:
                            (ctx.cwd / rel).unlink()
                        except OSError:
                            continue
                        dropped.append(rel)
            summary = (
                "aider reported edits only outside the active scope: "
                f"{', '.join(reported[:5])} (scope: {ctx.scope.describe()})"
            )
            if reverted_to:
                summary += f" — out-of-scope commits reverted to {reverted_to}"
            if not_reverted:
                summary += f" — {not_reverted}"
            if dropped:
                summary += f"; session droppings removed: {', '.join(dropped[:3])}"
            await ctx.bus.emit(
                "gate_decision",
                {
                    "gate": "scope",
                    "backend": "aider",
                    "allowed": False,
                    "paths": reported[:10],
                    "scope": ctx.scope.describe(),
                    "reverted_to": reverted_to,
                    "not_reverted": not_reverted,
                    "dropped_untracked": dropped[:10],
                },
                session_id=ctx.session_id,
            )
            return RunOutcome(
                code=TerminalCode.OUT_OF_SCOPE,
                summary=summary,
                rounds=1,
                diff_lines=diff_lines,
                session_id=ctx.session_id,
            )

        tail = [line for line in lines if line.strip()][-5:]
        summary = "\n".join(tail)[:500] or "aider completed"
        return RunOutcome(
            code=TerminalCode.COMPLETED,
            summary=summary,
            rounds=1,
            diff_lines=diff_lines,
            session_id=ctx.session_id,
        )

    async def _pump(self, ctx: SessionContext) -> tuple[list[str], str]:
        """Stream stdout lines as events while draining stderr concurrently."""
        assert self._proc is not None and self._proc.stdout and self._proc.stderr
        lines: list[str] = []

        async def _drain_stderr() -> str:
            data = await self._proc.stderr.read()  # type: ignore[union-attr]
            return data.decode(errors="replace")

        err_task = asyncio.ensure_future(_drain_stderr())
        while True:
            raw = await self._proc.stdout.readline()
            if not raw:
                break
            line = raw.decode(errors="replace").rstrip("\n")
            lines.append(line)
            await ctx.bus.emit(
                "model_response",
                {"backend": "aider", "line": line[:300]},
                session_id=ctx.session_id,
            )
        return lines, await err_task

    async def _report_diff(self, ctx: SessionContext, pre_head: str) -> tuple[int, list[str]]:
        """Emit file_changed events for the aider run's committed diff.

        Returns (diff lines, changed paths). Paths come from
        ``git diff --name-only``: ``--stat`` truncates deep paths, and a
        truncated path can false-fire the F4 scope-block projection
        (K2-R1). ``--stat`` is used for diff-line counts only."""
        stat = await self._git(ctx.cwd, "diff", "--stat", f"{pre_head}..HEAD")
        names = await self._git(ctx.cwd, "diff", "--name-only", f"{pre_head}..HEAD")
        paths = [p.strip() for p in (names or "").splitlines() if p.strip()]
        for path in paths:
            await ctx.bus.emit(
                "file_changed",
                {"backend": "aider", "path": path},
                session_id=ctx.session_id,
            )
        ins = re.search(r"(\d+) insertion", stat or "")
        dele = re.search(r"(\d+) deletion", stat or "")
        diff_lines = (int(ins.group(1)) if ins else 0) + (int(dele.group(1)) if dele else 0)
        return diff_lines, paths
