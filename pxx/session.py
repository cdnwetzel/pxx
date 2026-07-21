"""Session — the orchestration hub.

Wires config + backend + memory + safety + events into one run:

1. Build gates (scope, hooks, budgets) and the event bus + audit sink.
2. Open the memory store and build the deterministic injection context.
3. Assemble the tool registry (built-ins + MCP client tools).
4. Run the backend; map gate errors to terminal codes; honor SIGINT.
5. Post-session: capture observations, write the terminal audit record.

Nothing here does model I/O directly — that is the backend's job.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import signal
import subprocess
import time
import uuid
from dataclasses import replace
from pathlib import Path

from .backends.base import AgentBackend, SessionContext
from .config import Settings
from .errors import (
    BackendError,
    BudgetExceeded,
    ConfigError,
    GateError,
    HookDenied,
    HooksMissing,
    PxxError,
    ScopeViolation,
)
from .events import AuditLog, Event, EventBus
from .manifest import RunDirWriter, build_manifest
from .outcome import RunOutcome, TerminalCode
from .safety import BudgetGuard, HookRunner, ScopeGate

log = logging.getLogger("pxx.session")


def _sha256_file(path: Path) -> str | None:
    """Content fingerprint for an untracked file (size fingerprint past 1 MB)."""
    try:
        if path.stat().st_size > 1_000_000:
            return f"size:{path.stat().st_size}"
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


class Session:
    def __init__(
        self,
        settings: Settings,
        backend: AgentBackend,
        *,
        cwd: Path | None = None,
        bus: EventBus | None = None,
        review_mode: str = "blocking",
        safety_net: bool | None = None,
    ) -> None:
        self.settings = settings
        self.backend = backend
        self.cwd = (cwd or Path.cwd()).resolve()
        self.project = self.cwd.name
        self.session_id = uuid.uuid4().hex[:12]
        self.bus = bus or EventBus()
        self.review_mode = review_mode
        # K5: None resolves from config; run_loop passes False for its
        # per-round sessions (the loop ties its own net once).
        self._safety_net = settings.safety_net if safety_net is None else safety_net
        self._net = None  # set when the net fires (recorded in outcome.json)
        self._background_tasks: set[asyncio.Task] = set()
        self._mcp_clients: list = []  # tracked so run() always closes them
        self._sigint_loop: asyncio.AbstractEventLoop | None = None
        self._profile = None  # broker PermissionProfile, resolved in run()
        # Phase 11.3 identity threading (set by _open_run_dir; "" when unknown)
        self.task_id = ""
        self.repository_fingerprint = ""
        self.starting_commit = ""

    async def run(self, task: str, *, check_clarity: bool = True) -> RunOutcome:
        from .memory.capture import record_observations
        from .memory.inject import build_context
        from .memory.store import MemoryStore
        from .tools import ToolRegistry, default_registry

        settings = self.settings
        audit = AuditLog(settings.state_dir, self.session_id)
        audit.subscribe_to(self.bus)

        # Ambiguity gate (Phase 14): stop underspecified tasks BEFORE any
        # backend round. Uncertain never guesses — it asks.
        if check_clarity:
            from .clarify import ReadyState, ready_to_act

            clarity = ready_to_act(task, cwd=self.cwd, test_command=settings.test_command)
            if clarity.state is not ReadyState.READY_TO_EXECUTE:
                await self._clarification_stop(clarity)
                return RunOutcome(
                    code=TerminalCode.CLARIFICATION_REQUIRED,
                    summary=clarity.question,
                    session_id=self.session_id,
                )

        # The broker's permission profile: the repo's WORKFLOW.md when present
        # (a malformed contract raises ConfigError — no silent defaults), else
        # the built-in posture.
        from .broker import resolve_profile

        try:
            self._profile = resolve_profile(self.cwd)
        except ConfigError as exc:
            await self.bus.emit(
                "session_end",
                {
                    "code": str(TerminalCode.CONFIGURATION_INVALID),
                    "summary": str(exc)[:500],
                    "rounds": 0,
                    "tokens": 0,
                },
                session_id=self.session_id,
            )
            return RunOutcome(
                code=TerminalCode.CONFIGURATION_INVALID,
                summary=str(exc),
                session_id=self.session_id,
            )

        scope = ScopeGate(self.cwd, settings.scope, settings.trusted_paths)
        hooks = HookRunner(settings.hooks)
        budgets = BudgetGuard(settings.budgets)
        cancel_event = asyncio.Event()

        memory: MemoryStore | None = None
        memory_context = ""
        injected_ids: list[str] = []
        if settings.memory_enabled:
            try:
                memory = MemoryStore(settings.memory_dir / "memory.db")
                memory_context = await build_context(
                    memory, self.project, task, collect_ids=injected_ids
                )
            except Exception:
                log.exception("memory unavailable; continuing without it")
                memory = None

        tools: ToolRegistry = default_registry()
        await self._attach_mcp_tools(tools)

        ctx = SessionContext(
            settings=settings,
            bus=self.bus,
            scope=scope,
            hooks=hooks,
            budgets=budgets,
            tools=tools,
            memory=memory,
            session_id=self.session_id,
            project=self.project,
            cwd=self.cwd,
            cancel_event=cancel_event,
            memory_context=memory_context,
            profile=self._profile,
        )

        self._install_sigint(cancel_event)

        try:
            # Phase 11 run-dir telemetry: manifest + task + event mirror. All
            # best-effort — it must never crash a session (hard rule 1).
            from .manifest import probe_model_fingerprint

            try:
                fingerprint = await probe_model_fingerprint(settings.model)
            except Exception:
                log.exception("model fingerprint probe failed (best-effort)")
                fingerprint = None
            run_id, agent_version_id, run_dir = self._open_run_dir(
                task,
                memory=memory is not None,
                memory_context_bytes=len(memory_context),
                fingerprint=fingerprint,
            )

            await self.bus.emit(
                "run_created",
                {"task_preview": task[:200], "project": self.project},
                session_id=self.session_id,
            )
            await self.bus.emit(
                "session_start",
                {
                    "backend": self.backend.name,
                    "model": settings.model.model,
                    "provider": settings.model.provider,
                    "permission": str(settings.permission),
                    "scope": scope.describe(),
                    "project": self.project,
                    "memory": memory is not None,
                    "memory_context_bytes": len(memory_context),
                    "run_id": run_id,
                    "agent_version_id": agent_version_id,
                    "task_id": self.task_id,
                    "repository_fingerprint": self.repository_fingerprint,
                    "starting_commit": self.starting_commit,
                },
                session_id=self.session_id,
            )

            edit_start = time.monotonic()
            # K5: tie the safety net BEFORE anything can write — parked work
            # stays parked (pop is the user's move, never pxx's).
            if self._safety_net and settings.permission.can_write:
                from .safety_net import tie_safety_net

                self._net = await tie_safety_net(self.cwd, run_id)
                if self._net is not None:
                    await self.bus.emit(
                        "gate_decision",
                        {
                            "gate": "safety_net",
                            "allowed": True,
                            "tag": self._net.tag or "",
                            "stash": self._net.stash_message or "",
                            "run_id": run_id,
                        },
                        session_id=self.session_id,
                    )
            worktree_start = await self._worktree_snapshot()
            try:
                outcome = await asyncio.wait_for(
                    self.backend.run(task, ctx), timeout=budgets.remaining_seconds() + 5
                )
            except TimeoutError:
                outcome = RunOutcome(
                    code=TerminalCode.EDIT_TIMEOUT,
                    summary="wall-clock budget exceeded while editing",
                    session_id=self.session_id,
                )
            except BudgetExceeded as exc:
                outcome = RunOutcome(
                    code=TerminalCode.BUDGET_EXCEEDED, summary=str(exc), session_id=self.session_id
                )
            except ScopeViolation as exc:
                outcome = RunOutcome(
                    code=TerminalCode.OUT_OF_SCOPE, summary=str(exc), session_id=self.session_id
                )
            except HooksMissing as exc:
                outcome = RunOutcome(
                    code=TerminalCode.HOOKS_MISSING, summary=str(exc), session_id=self.session_id
                )
            except HookDenied as exc:
                outcome = RunOutcome(
                    code=TerminalCode.HOOK_DENIED, summary=str(exc), session_id=self.session_id
                )
            except ConfigError as exc:
                outcome = RunOutcome(
                    code=TerminalCode.CONFIGURATION_INVALID,
                    summary=str(exc),
                    session_id=self.session_id,
                )
            except BackendError as exc:
                code = (
                    exc.code
                    if isinstance(exc.code, TerminalCode)
                    else TerminalCode.MODEL_UNAVAILABLE
                )
                outcome = RunOutcome(code=code, summary=str(exc), session_id=self.session_id)
            except GateError as exc:
                outcome = RunOutcome(
                    code=TerminalCode.MODEL_UNAVAILABLE,
                    summary=str(exc),
                    session_id=self.session_id,
                )
            except PxxError as exc:
                outcome = RunOutcome(
                    code=TerminalCode.MODEL_UNAVAILABLE,
                    summary=str(exc),
                    session_id=self.session_id,
                )
            except asyncio.CancelledError:
                outcome = RunOutcome(
                    code=TerminalCode.INTERRUPTED, summary="cancelled", session_id=self.session_id
                )
            except Exception as exc:  # last-resort: a run always ends with a code
                log.exception("unexpected backend failure")
                outcome = RunOutcome(
                    code=TerminalCode.MODEL_UNAVAILABLE,
                    summary=f"unexpected: {exc!r}",
                    session_id=self.session_id,
                )

            edit_seconds = time.monotonic() - edit_start
            outcome = replace(
                outcome,
                edit_seconds=outcome.edit_seconds or edit_seconds,
                injected_observation_ids=tuple(injected_ids),
            )

            if cancel_event.is_set() and outcome.code is TerminalCode.COMPLETED:
                outcome = RunOutcome(
                    code=TerminalCode.INTERRUPTED,
                    summary="interrupted by user",
                    rounds=outcome.rounds,
                    tokens=outcome.tokens,
                    session_id=self.session_id,
                )

            # K8: report mutations the terminal code alone hides — the native
            # backend commits nothing, and aborts can follow landed writes
            # (reporting only; gate behavior is unchanged).
            outcome = await self._report_worktree_mutations(worktree_start, outcome)

            if self._net is not None:
                net_suffix = self._net.tag or "no-tag"
                if self._net.stash_message:
                    net_suffix += "+stash"
                outcome = replace(outcome, summary=f"{outcome.summary} [net: {net_suffix}]")

            await self.bus.emit(
                "session_end",
                {
                    "code": str(outcome.code),
                    "summary": outcome.summary[:500],
                    "rounds": outcome.rounds,
                    "tokens": outcome.tokens,
                    "diff_lines": outcome.diff_lines,
                    "budgets": budgets.snapshot(),
                    "run_id": run_id,
                    "agent_version_id": agent_version_id,
                },
                session_id=self.session_id,
            )
            await self._close_run_dir(run_dir, run_id, agent_version_id, outcome)

            # Post-session memory capture is best-effort. It runs after the
            # session_end emit so capture can derive provenance from the
            # terminal code in the event history (Phase 20).
            if memory is not None:
                try:
                    await record_observations(
                        memory, self.project, self.session_id, self.bus.history
                    )
                except Exception:
                    log.exception("memory capture failed (best-effort)")
                memory.close()
            return outcome
        finally:
            await self._cleanup()

    async def _cleanup(self) -> None:
        """Release per-run resources: MCP clients + the SIGINT handler.

        Best-effort: cleanup failures are logged, never raised. Without this,
        every MCP-attached run leaks a subprocess + reader task (the
        event-loop-closed teardown noise) and the signal handler outlives
        the session.
        """
        for client in self._mcp_clients:
            try:
                await client.close()
            except Exception:
                log.exception("MCP client close failed (best-effort)")
        self._mcp_clients.clear()
        if self._sigint_loop is not None:
            try:
                self._sigint_loop.remove_signal_handler(signal.SIGINT)
            except Exception:
                log.exception("SIGINT handler removal failed (best-effort)")
            self._sigint_loop = None

    async def _clarification_stop(self, decision) -> None:
        """Telemetry for a run stopped at the clarification gate (metadata-only)."""
        await self.bus.emit(
            "session_start",
            {
                "backend": self.backend.name,
                "model": self.settings.model.model,
                "provider": self.settings.model.provider,
                "project": self.project,
                "clarification_gate": True,
            },
            session_id=self.session_id,
        )
        await self.bus.emit(
            "gate_decision",
            {
                "gate": "clarification",
                "allowed": False,
                "state": str(decision.state),
                "question": decision.question[:500],
            },
            session_id=self.session_id,
        )
        await self.bus.emit(
            "session_end",
            {
                "code": str(TerminalCode.CLARIFICATION_REQUIRED),
                "summary": decision.question[:500],
                "rounds": 0,
                "tokens": 0,
            },
            session_id=self.session_id,
        )

    def _repo_identity(self) -> tuple[str, str]:
        """(repository_fingerprint, starting_commit) — best-effort.

        The fingerprint binds HEAD sha + dirty flag + tracked-file count into
        one stable hash: two runs against the same repo state share it; a
        dirty vs clean tree differs. Both empty outside a git repo. Sync git
        calls are used here (a few ms) to keep run-dir setup non-async.
        """
        try:
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.cwd,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if head.returncode != 0:
                return "", ""
            starting = head.stdout.strip()
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.cwd,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            files = subprocess.run(
                ["git", "ls-files"],
                cwd=self.cwd,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            dirty = bool(status.stdout.strip())
            tracked = len([ln for ln in files.stdout.splitlines() if ln.strip()])
            fingerprint = hashlib.sha256(f"{starting}:{dirty}:{tracked}".encode()).hexdigest()[:16]
            return fingerprint, starting
        except Exception:
            log.exception("repo identity probe failed (best-effort)")
            return "", ""

    def _open_run_dir(
        self,
        task: str,
        *,
        memory: bool,
        memory_context_bytes: int,
        fingerprint=None,
    ) -> tuple[str, str, RunDirWriter | None]:
        """Create the Phase 11 run dir and mirror bus events into it.

        Returns (run_id, agent_version_id, writer). Best-effort: any failure
        degrades to a None writer and an empty agent_version_id.
        """
        run_id = f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:8]}"
        try:
            manifest = build_manifest(
                self.settings,
                self.backend.name,
                review_mode=self.review_mode,
                workflow_path=self.cwd / "WORKFLOW.md",
                fingerprint=fingerprint,
            )
            task_id = hashlib.sha256(task.encode()).hexdigest()[:16]
            repo_fingerprint, starting_commit = self._repo_identity()
            self.task_id = task_id
            self.repository_fingerprint = repo_fingerprint
            self.starting_commit = starting_commit
            writer = RunDirWriter.open(self.settings.state_dir, run_id)
            writer.write_manifest(manifest)
            writer.write_task(
                {
                    "run_id": run_id,
                    "session_id": self.session_id,
                    "project": self.project,
                    "task": task,
                    "task_id": task_id,
                    "repository_fingerprint": repo_fingerprint,
                    "starting_commit": starting_commit,
                    "ts": time.time(),
                    "memory": memory,
                    "memory_context_bytes": memory_context_bytes,
                }
            )

            async def _mirror(event: Event) -> None:
                # event.to_json() scrubs credentials and stays metadata-only.
                writer.append_event(json.loads(event.to_json()))

            self.bus.subscribe(_mirror)
            return run_id, manifest.agent_version_id, writer
        except Exception:
            log.exception("run dir setup failed (best-effort, continuing)")
            return run_id, "", None

    async def _close_run_dir(
        self,
        writer: RunDirWriter | None,
        run_id: str,
        agent_version_id: str,
        outcome: RunOutcome,
    ) -> None:
        """Write outcome.json (+ diff.patch when a diff is available)."""
        if writer is None:
            return
        try:
            diff = await self._collect_diff()
            # Deterministic human-audit sampling (Phase 14.5): runs whose diff
            # touches the protected control plane are flagged 100%; ordinary
            # runs take a reproducible 20% hash sample.
            from .audit_sampling import audit_sample
            from .protected_paths import is_protected_path

            touched = {
                line[4:].strip()
                for line in diff.splitlines()
                if line.startswith(("+++ b/", "--- a/"))
            }
            risk = "high" if any(is_protected_path(t) for t in touched) else "ordinary"
            sample = audit_sample(run_id, risk=risk)
            # Phase 10.8: the recorded outcome is PROJECTED from the event
            # stream — the audit log is the source of truth, so the record
            # cannot disagree with what happened.
            from .projection import project_outcome

            projected = project_outcome(self.bus.history, self.session_id)
            writer.write_outcome(
                {
                    "run_id": run_id,
                    "session_id": self.session_id,
                    "agent_version_id": agent_version_id,
                    "code": str(projected.code),
                    "summary": projected.summary[:500],
                    "rounds": projected.rounds,
                    "tokens": projected.tokens,
                    "diff_lines": outcome.diff_lines,
                    "cost_usd": outcome.cost_usd,
                    "contributing_codes": list(projected.contributing_codes),
                    "edit_seconds": outcome.edit_seconds,
                    "test_seconds": projected.test_seconds,
                    "review_seconds": projected.review_seconds,
                    "files_changed": projected.files_changed,
                    "baseline_failures": projected.baseline_failures,
                    "introduced_failures": projected.introduced_failures,
                    "terminal_failures": projected.terminal_failures,
                    "lint_errors": outcome.lint_errors,
                    "findings_by_severity": dict(outcome.findings_by_severity),
                    "unparseable_review_count": projected.unparseable_review_count,
                    "injected_observation_ids": list(outcome.injected_observation_ids),
                    "safety_net": (
                        {"tag": self._net.tag, "stash": self._net.stash_message}
                        if self._net
                        else None
                    ),
                    "audit_sampled": sample.sampled,
                    "audit_sample_reason": sample.reason,
                    "ts": time.time(),
                }
            )
            if diff:
                writer.write_diff(diff)
        except Exception:
            log.exception("run dir finalize failed (best-effort, continuing)")

    async def _collect_diff(self) -> str:
        """Best-effort ``git diff HEAD`` for the run dir; '' when unavailable."""
        if not (self.cwd / ".git").exists():
            return ""
        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "diff",
                "HEAD",
                cwd=str(self.cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15.0)
        except (OSError, TimeoutError):
            return ""
        if proc.returncode != 0:
            return ""
        text = stdout.decode(errors="replace")
        return text if len(text) <= 1_000_000 else ""

    # -- K8 worktree-mutation reporting --------------------------------------

    async def _git_text(self, *args: str) -> str | None:
        """git in the session cwd; stdout or None on any failure (best-effort)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                *args,
                cwd=str(self.cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15.0)
        except (OSError, TimeoutError):
            return None
        if proc.returncode != 0:
            return None
        return stdout.decode(errors="replace")

    async def _worktree_snapshot(self) -> dict | None:
        """Worktree state for mutation reporting; None without git.

        Tracks two channels: tracked-vs-HEAD diff volume (``--numstat``)
        and untracked-file content fingerprints. A session's mutations are
        the delta between two snapshots — pre-existing dirt is excluded.
        """
        status = await self._git_text("status", "--porcelain=v1", "--untracked-files=all")
        if status is None:
            return None
        untracked: dict[str, str | None] = {}
        for line in status.splitlines():
            if line[:2] == "??":
                rel = line[3:]
                untracked[rel] = _sha256_file(self.cwd / rel)
        numstat: dict[str, int] = {}
        diff = await self._git_text("diff", "HEAD", "--numstat")
        for line in (diff or "").splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                adds, dels, rel = parts
                numstat[rel] = (int(adds) if adds.isdigit() else 0) + (
                    int(dels) if dels.isdigit() else 0
                )
        return {"untracked": untracked, "numstat": numstat}

    async def _worktree_delta(self, start: dict) -> tuple[list[str], int]:
        """(paths changed since ``start``, diff lines) — uncommitted included."""
        now = await self._worktree_snapshot()
        if now is None:
            return [], 0
        changed: list[str] = []
        diff_lines = 0
        for rel, lines in now["numstat"].items():
            base = start["numstat"].get(rel)
            if base is None:
                changed.append(rel)
                diff_lines += lines
            elif lines != base:
                changed.append(rel)
                diff_lines += max(0, lines - base)
        for rel, fingerprint in now["untracked"].items():
            if start["untracked"].get(rel) != fingerprint:
                changed.append(rel)
                try:
                    diff_lines += len((self.cwd / rel).read_bytes().splitlines())
                except OSError:
                    pass
        return changed, diff_lines

    async def _report_worktree_mutations(
        self, start: dict | None, outcome: RunOutcome
    ) -> RunOutcome:
        """Fold measured worktree mutations into the terminal report (K8).

        Fills ``diff_lines`` when the backend left it at zero (the native
        backend commits nothing), names landed writes on non-success
        outcomes, and emits ``file_changed`` for writes that bypassed the
        tool surface (e.g. shell redirects). A backend-reported nonzero
        count (aider's committed diff) always wins.
        """
        if start is None:
            return outcome
        changed, landed_lines = await self._worktree_delta(start)
        if not changed:
            return outcome
        already = {
            str(e.data.get("path", "")) for e in self.bus.history if e.kind == "file_changed"
        }
        for rel in changed:
            if rel not in already and str(self.cwd / rel) not in already:
                await self.bus.emit(
                    "file_changed",
                    {"path": rel, "source": "worktree-diff"},
                    session_id=self.session_id,
                )
        if outcome.diff_lines == 0:
            outcome = replace(outcome, diff_lines=landed_lines)
        if outcome.code is not TerminalCode.COMPLETED:
            count = len(changed)
            noun = "file" if count == 1 else "files"
            outcome = replace(
                outcome,
                summary=(
                    f"{outcome.summary} — {count} {noun} already modified: {', '.join(changed[:5])}"
                ),
            )
        return outcome

    async def _attach_mcp_tools(self, tools) -> None:
        """Connect configured MCP servers and register their tools."""
        if not self.settings.mcp_servers:
            return
        from .mcp.client import McpClientError, StdioMcpClient, register_mcp_tools

        for spec in self.settings.mcp_servers:
            try:
                client = await StdioMcpClient.connect(spec.name, spec.command)
                self._mcp_clients.append(client)  # closed in run()'s finally
                await register_mcp_tools(client, tools)
                await self.bus.emit(
                    "gate_decision",
                    {"gate": "mcp_connect", "server": spec.name, "allowed": True},
                    session_id=self.session_id,
                )
            except McpClientError as exc:
                log.warning("MCP server %s unavailable: %s", spec.name, exc)
                await self.bus.emit(
                    "gate_decision",
                    {
                        "gate": "mcp_connect",
                        "server": spec.name,
                        "allowed": False,
                        "reason": str(exc)[:200],
                    },
                    session_id=self.session_id,
                )

    def _install_sigint(self, cancel_event: asyncio.Event) -> None:
        try:
            loop = asyncio.get_running_loop()

            def _on_sigint() -> None:
                cancel_event.set()
                task = asyncio.ensure_future(self.backend.cancel())
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)

            loop.add_signal_handler(signal.SIGINT, _on_sigint)
            self._sigint_loop = loop  # removed in run()'s finally
        except (NotImplementedError, RuntimeError, ValueError):
            pass  # no signal support (non-main thread, Windows) — skip
