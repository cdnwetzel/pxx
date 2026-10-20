"""aider subprocess backend (optional dependency).

Delegates the edit to the ``aider`` CLI as an async subprocess. pxx still owns
policy: permission mode maps to aider's chat mode, scope/memory context is
passed via a temp ``--read`` file, stdout lines are streamed as
``model_response`` events, and in a repo aider runs with
``--no-auto-commits --no-dirty-commits``: what the run changed is measured
from the working tree (``pxx.worktree``, as the session and loop do) and
feeds ``file_changed`` events, the diff-lines budget and the scope fence.
pxx alone commits (``--commit``); a HEAD or branch that moves anyway is
reported on every exit, never reset.
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
from ..gitenv import INTERNAL_GIT, git_env
from ..outcome import RunOutcome, TerminalCode
from ..safety import PermissionMode
from .base import BackendCapabilities, SessionContext

log = logging.getLogger("pxx.backends.aider")

INSTALL_HINT = "pip install pxx-orchestrator[aider]"

#: aider's own stdout marker for an applied edit (its reporting, not ours).
_APPLIED_EDIT_RE = re.compile(r"^Applied edit to\s+(.+?)\s*$")

#: Provider/endpoint-down signatures aider (via litellm) prints when the model
#: endpoint is unreachable or failing. aider commonly exits 0 in these cases and
#: makes no edit, so without this a down endpoint reads as COMPLETED. Only
#: consulted when nothing was edited.
#:
#: Deliberately restricted to DISTINCTIVE error tokens (exception class names,
#: aider's own down message). Loose prose phrases ("rate limit", "connection
#: error/refused") were dropped (neo advisory 2026-08-02): ask-mode answers are
#: also zero-edit exit-0 runs, and an answer that merely discusses rate limiting
#: or connection errors must not be reclassified. The real litellm failures
#: (RateLimitError, APIConnectionError, Timeout, …) still match via the
#: ``litellm.*Error`` and class-name alternatives below.
_MODEL_DOWN_RE = re.compile(
    r"servers are down or overloaded"  # aider's own friendly message
    r"|litellm\.\w*(?:error|exception)"  # any litellm exception class
    r"|api ?connection ?error"  # APIConnectionError
    r"|internal ?server ?error"  # InternalServerError
    r"|service ?unavailable(?:error)?"  # ServiceUnavailable(Error)
    r"|overloaded_error"  # provider overload class
    r"|max retries exceeded",  # urllib3/requests exhaustion
    re.IGNORECASE,
)


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
            # INTERNAL_GIT: what a SHA means -- and what `reset --hard` checks
            # out -- must not be redirectable by state made during the run.
            proc = await asyncio.create_subprocess_exec(
                *INTERNAL_GIT,
                *args,
                cwd=cwd,
                stdout=PIPE,
                stderr=PIPE,
                env=git_env(),
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
        env = git_env()  # aider commits via its own git calls — same wrong-repo risk
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
            f"Read — any path inside the project: {ctx.scope.root}",
            f"Write — only paths under: {ctx.scope.describe()}",
            "An edit outside the write scope is reverted and ends the run; "
            "scope and permission gates are absolute.",
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
        else:
            # pxx owns committing: aider must leave its edits (and any
            # pre-existing changes) uncommitted. --commit is then honoured by
            # the session's own commit path -- secrets scan, [net:] trailer,
            # telemetry -- on every backend alike.
            argv += ["--no-auto-commits", "--no-dirty-commits"]
        argv += ["--read", read_file]
        return argv

    # -- run ------------------------------------------------------------------

    @staticmethod
    async def _status(cwd: Path) -> tuple[set[str], bool] | None:
        """(untracked paths, tracked-dirty?) from NUL-exact ``git status -z``
        (pxx.worktree's one status parser); None when git fails. Unquoted
        paths matter: a quoted ``"evil\"file"`` could never be removed
        (2.9.1 review)."""
        from ..worktree import _git_status_entries

        entries = await _git_status_entries(cwd)
        if entries is None:
            return None
        untracked = {path for status, path in entries if status == "??"}
        return untracked, any(status != "??" for status, _ in entries)

    async def run(self, task: str, ctx: SessionContext) -> RunOutcome:
        pre_head = await self._git(ctx.cwd, "rev-parse", "HEAD")
        use_git = pre_head is not None
        # The branch too: a switch to another branch at the SAME commit moves
        # no SHA but would still put any later reset on the wrong branch.
        pre_ref = (await self._git(ctx.cwd, "symbolic-ref", "-q", "HEAD") or "") if use_git else ""
        pre_tree = (
            (await self._git(ctx.cwd, "rev-parse", "--verify", "HEAD^{tree}") or "")
            if use_git
            else ""
        )
        untracked_start: set[str] = set()
        tracked_dirty_start = False
        snapshot: dict | None = None
        if use_git:
            start_status = await self._status(ctx.cwd)
            if start_status is not None:
                untracked_start, tracked_dirty_start = start_status
            snapshot = await self._snapshot(ctx.cwd)
            if snapshot is None or start_status is None:
                # Fail closed (2.9.1 review): without a baseline nothing aider
                # changes could be measured or scope-checked afterwards.
                raise BackendError(
                    "could not snapshot the working tree before running aider "
                    "(git failed); not running it",
                    code=TerminalCode.EDIT_FAILED,
                )
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
            try:
                lines, stderr_text = await self._pump(ctx)
                returncode = await self._proc.wait()
            except asyncio.CancelledError:
                # The task itself was cancelled (e.g. the session's wall-clock
                # wait_for), not backend.cancel(): never leave aider running,
                # and still record whether it moved HEAD (2.9.1 review). The
                # cleanup is shielded so the cancellation cannot cut it short.
                await asyncio.shield(self._abandon(ctx, pre_head, pre_ref, use_git))
                raise
        finally:
            self._proc = None
            with contextlib.suppress(OSError):
                os.unlink(tmp.name)

        # aider was told not to commit. If HEAD moved anyway (an aider that
        # ignores --no-auto-commits), say so on EVERY way out -- cancelled,
        # failed or finished: pxx made no commit and will not rewrite history
        # behind the operator's back.
        moved = await self._head_moved(ctx, pre_head, pre_ref) if use_git else ""
        if self._cancelled or ctx.cancel_event.is_set():
            return RunOutcome(
                code=TerminalCode.INTERRUPTED,
                summary="cancelled" + (f" — {moved}" if moved else ""),
                session_id=ctx.session_id,
            )
        if returncode != 0:
            raise BackendError(
                f"aider exited with code {returncode}: {stderr_text[-500:]}"
                + (f" — {moved}" if moved else ""),
                code=TerminalCode.EDIT_FAILED,
            )
        if moved:
            raise BackendError(moved, code=TerminalCode.EDIT_FAILED)

        diff_lines = 0
        changed_paths: list[str] = []
        if use_git:
            diff_lines, changed_paths = await self._report_diff(ctx, snapshot)
        ctx.budgets.consume(rounds=1, diff_lines=diff_lines)
        ctx.budgets.check_clock()

        # F4 + K2b: when ANY edit aider reports is outside the active scope,
        # the run is scope-blocked — reverted (below), never projected as
        # success. "Every edit" (the pre-2.9.1 rule) let a run that also made
        # one in-scope edit through, out-of-scope file and all, and --commit
        # then committed it; the loop's re-check already uses "any".
        claimed = [m.group(1) for line in lines if (m := _APPLIED_EDIT_RE.search(line))]
        reported = list(dict.fromkeys([*changed_paths, *claimed]))
        offenders = [p for p in reported if not ctx.scope.in_scope(p)]
        if offenders:
            # K2b: the fence actually fences — undo the blocked run's edits
            # (reset --hard to the pre-run HEAD; aider made no commits) and its
            # session-created droppings. Pre-existing state is
            # untouchable: never reset over user dirt (the K5 stash, when
            # present, is in refs/stash and unaffected by reset --hard),
            # never delete files that were already untracked at run start.
            reverted_to = ""
            not_reverted = ""
            if use_git and pre_head:
                if tracked_dirty_start:
                    not_reverted = (
                        "edits not reverted (pre-existing local changes); "
                        f"revert manually: git reset --hard {pre_head[:7]}"
                    )
                else:
                    # Only revert to a start whose tree is still the start's,
                    # and claim it only once the tree is verified afterwards.
                    tree_now = await self._git(ctx.cwd, "rev-parse", "--verify", "HEAD^{tree}")
                    reset = (
                        await self._git(ctx.cwd, "reset", "--hard", pre_head)
                        if pre_tree and tree_now == pre_tree
                        else None
                    )
                    tree_after = (
                        await self._git(ctx.cwd, "rev-parse", "--verify", "HEAD^{tree}")
                        if reset is not None
                        else None
                    )
                    if reset is None or tree_after != pre_tree:
                        not_reverted = (  # never report a revert that did not happen
                            f"revert FAILED (git reset --hard {pre_head[:7]} could not be "
                            f"verified); revert manually: git reset --hard {pre_head[:7]}"
                        )
                    else:
                        reverted_to = pre_head[:7]
            dropped: list[str] = []
            undropped: list[str] = []  # out-of-scope droppings that stayed
            if use_git:
                status_now = await self._status(ctx.cwd)
                untracked_now = status_now[0] if status_now is not None else set()
                if status_now is None:
                    undropped.append("<untracked files could not be listed>")
                for rel in sorted(untracked_now - untracked_start):
                    if not ctx.scope.in_scope(rel):
                        try:
                            (ctx.cwd / rel).unlink()
                        except OSError:
                            undropped.append(rel)  # never silent (2.9.1 review)
                            continue
                        dropped.append(rel)
            summary = (
                "aider edited outside the active scope: "
                f"{', '.join(offenders[:5])} (scope: {ctx.scope.describe()})"
            )
            if reverted_to:
                summary += f" — out-of-scope edits reverted to {reverted_to}"
            if not_reverted:
                summary += f" — {not_reverted}"
            if dropped:
                summary += f"; session droppings removed: {', '.join(dropped[:3])}"
            if undropped:
                summary += f"; droppings NOT removed: {', '.join(undropped[:3])}"
            await ctx.bus.emit(
                "gate_decision",
                {
                    "gate": "scope",
                    "backend": "aider",
                    "allowed": False,
                    "paths": offenders[:10],
                    "scope": ctx.scope.describe(),
                    "reverted_to": reverted_to,
                    "not_reverted": not_reverted,
                    "dropped_untracked": dropped[:10],
                    "undropped_untracked": undropped[:10],
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

        # Truthfulness: aider frequently exits 0 even when the LLM endpoint was
        # down/overloaded — it prints the provider error and makes no edit.
        # Reporting that as COMPLETED is a false success (portable-box degrade
        # receipt, 2026-08-02); reclassify it as MODEL_UNAVAILABLE. Guarded by
        # "nothing was edited" (no diff, no reported edit) so a genuine
        # completion that merely mentions an error word stays COMPLETED.
        if diff_lines == 0 and not reported:
            probe = [*lines, *stderr_text.splitlines()]
            hit = next((ln.strip() for ln in probe if _MODEL_DOWN_RE.search(ln)), "")
            if hit:
                return RunOutcome(
                    code=TerminalCode.MODEL_UNAVAILABLE,
                    summary=f"aider: model endpoint unavailable — {hit[:300]}",
                    rounds=1,
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

    async def _abandon(
        self, ctx: SessionContext, pre_head: str | None, pre_ref: str, use_git: bool
    ) -> None:
        """Kill and reap aider after the task was cancelled, then report a
        moved HEAD/branch as an observation (the outcome is the caller's)."""
        proc = self._proc
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), timeout=10)
        if not use_git:
            return
        moved = await self._head_moved(ctx, pre_head, pre_ref)
        if moved:
            log.warning("aider run cancelled: %s", moved)
            await ctx.bus.emit(
                "observation",
                {"source": "aider_head_moved", "detail": moved[:300]},
                session_id=ctx.session_id,
            )

    async def _head_moved(
        self, ctx: SessionContext, pre_head: str | None, pre_ref: str = ""
    ) -> str:
        """A message when HEAD is no longer where the run started, else ""."""
        post_ref = await self._git(ctx.cwd, "symbolic-ref", "-q", "HEAD") or ""
        if post_ref.strip() != pre_ref.strip():
            return (
                "the checked-out branch changed during the aider run "
                f"({pre_ref.strip() or 'detached'} -> {post_ref.strip() or 'detached'}); "
                "pxx switched nothing. Check out the original branch to continue."
            )
        post_head = await self._git(ctx.cwd, "rev-parse", "HEAD")
        if pre_head and not post_head:
            # A failed read is not "HEAD did not move" (2.9.1 review).
            return (
                "could not verify HEAD after the aider run (git failed); "
                f"the run started at {pre_head.strip()[:7]} — inspect before trusting it"
            )
        if not pre_head or post_head.strip() == pre_head.strip():
            return ""
        pre, post = pre_head.strip()[:7], post_head.strip()[:7]
        return (
            f"aider committed despite --no-auto-commits (HEAD moved {pre} -> {post}); "
            "pxx did not create these commits. Inspect them; to uncommit and keep "
            f"the edits: git reset --soft {pre}"
        )

    @staticmethod
    async def _snapshot(cwd: Path) -> dict | None:
        """Worktree state at run start (seam for tests; the session and loop
        use the same helper)."""
        from ..worktree import worktree_snapshot

        return await worktree_snapshot(cwd)

    @staticmethod
    async def _delta(cwd: Path, start: dict) -> tuple[list[str], int] | None:
        from ..worktree import worktree_delta_checked

        return await worktree_delta_checked(cwd, start)

    async def _report_diff(self, ctx: SessionContext, start: dict | None) -> tuple[int, list[str]]:
        """Emit file_changed events for what the run changed in the working
        tree -- tracked edits and new files, pre-existing dirt excluded unless
        the run changed it (worktree_delta). Returns (diff lines, paths)."""
        if start is None:
            return 0, []
        from ..safety_net import git_state_changed_since

        changed = await git_state_changed_since(ctx.cwd, start.get("control"))
        if changed:
            # Filter/diff drivers, attributes or replace refs changed: git's
            # report of the tree -- and a revert's checkout -- can no longer
            # be trusted, so nothing is measured or reverted (2.9.1 review).
            raise BackendError(
                f"{changed}; pxx cannot measure or revert what aider changed -- "
                "inspect the working tree and the git configuration",
                code=TerminalCode.EDIT_FAILED,
            )
        delta = await self._delta(ctx.cwd, start)
        if delta is None:
            # Unknown is not "nothing changed": a run whose effects cannot be
            # measured cannot be scope-checked or reported (2.9.1 review).
            raise BackendError(
                "could not measure what aider changed (git failed after the run); "
                "inspect the working tree before trusting it",
                code=TerminalCode.EDIT_FAILED,
            )
        paths, diff_lines = delta
        for path in paths:
            await ctx.bus.emit(
                "file_changed",
                {"backend": "aider", "path": path},
                session_id=ctx.session_id,
            )
        return diff_lines, paths
