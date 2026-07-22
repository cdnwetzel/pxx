"""Goal-oriented multi-file orchestration (Phase 22).

Decomposes a high-level goal into a validated task DAG via a read-only
planner, then runs each node as a bounded :func:`pxx.loop.run_loop` with its
own scope — a fresh backend per node (fresh-context invariant). Independent
nodes with disjoint scopes run in parallel (``asyncio.gather`` over the
ready set); a failed node skips its dependents and never rewrites completed
nodes. When a ``test_command`` is configured, a final integration run over
the combined tree gates the overall outcome.

Validation is fail-closed: malformed plans (bad JSON, duplicate ids,
dangling dependencies, cycles) raise :class:`ConfigError`; scopes that are
absolute or escape the repo raise :class:`ScopeViolation`. Roles: the
planner is read-only, the loop is the implementer, per-node tests+review
are the verifier, and the integration command is the final check.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from .backends.base import AgentBackend
from .config import Settings
from .errors import BackendUnavailable, ConfigError, ScopeViolation
from .events import EventBus
from .loop import run_loop
from .outcome import RunOutcome, TerminalCode
from .safety import canonicalize

log = logging.getLogger("pxx.goal")

INTEGRATION_TIMEOUT_SECONDS = 600.0

#: A planner maps a goal to task-DAG JSON text. In real use this is a
#: read-only backend (NativeBackend with ASK permission) wired by the CLI;
#: tests inject a stub. Never given write or shell access.
Planner = Callable[[str], Awaitable[str]]


@dataclass(frozen=True)
class GoalTask:
    """One validated node of the task DAG. ``scope`` is repo-relative
    (POSIX separators); ``""`` means the whole repo."""

    id: str
    title: str
    scope: str = ""
    depends_on: tuple[str, ...] = ()
    test_command: str | None = None


@dataclass(frozen=True)
class GoalOutcome:
    """Terminal result of a goal run: per-node disposition plus an overall
    code (``COMPLETED`` only when every node and the integration run pass)."""

    code: TerminalCode
    completed: tuple[str, ...] = ()
    failed: dict[str, str] = field(default_factory=dict)  # task id -> terminal code
    skipped: tuple[str, ...] = ()
    summary: str = ""


async def _default_planner(goal: str) -> str:
    """Lazy placeholder: a real planner is a read-only backend wired by the
    CLI. Raises rather than touching the network on its own."""
    raise BackendUnavailable(
        "no planner configured: pass planner= (a read-only backend, e.g. a "
        "NativeBackend with ASK permission, adapted to return task-DAG JSON)"
    )


def _normalize_scope(scope: object, root: Path, node_id: str) -> str:
    """Validate a node scope; return the repo-relative normalized form."""
    if not isinstance(scope, str):
        raise ConfigError(f"task {node_id!r}: 'scope' must be a string")
    if not scope:
        return ""
    if Path(scope).is_absolute():
        raise ScopeViolation(f"task {node_id!r}: absolute scope not allowed: {scope!r}")
    canon = canonicalize(scope, cwd=root)
    if canon != root and root not in canon.parents:
        raise ScopeViolation(f"task {node_id!r}: scope escapes the repo: {scope!r}")
    if canon == root:
        return ""
    return canon.relative_to(root).as_posix()


def _check_acyclic(tasks: list[GoalTask]) -> None:
    """Iterative DFS over the dependency graph; raise ConfigError on cycles."""
    deps = {t.id: t.depends_on for t in tasks}
    color = dict.fromkeys(deps, 0)  # 0 = unvisited, 1 = in-stack, 2 = done
    for start in deps:
        if color[start] != 0:
            continue
        color[start] = 1
        stack = [(start, iter(deps[start]))]
        while stack:
            node, it = stack[-1]
            descended = False
            for dep in it:
                if color[dep] == 1:
                    raise ConfigError(f"cycle detected in task DAG at {dep!r}")
                if color[dep] == 0:
                    color[dep] = 1
                    stack.append((dep, iter(deps[dep])))
                    descended = True
                    break
            if not descended:
                color[node] = 2
                stack.pop()


def parse_plan(text: str, *, root: Path) -> tuple[GoalTask, ...]:
    """Parse and validate planner output into a task DAG.

    Raises :class:`ConfigError` on malformed structure (bad JSON, missing
    fields, duplicate ids, dangling dependencies, cycles) and
    :class:`ScopeViolation` on absolute or repo-escaping scopes.
    """
    root = canonicalize(root)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"planner returned invalid JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
        raise ConfigError("planner output must be an object with a 'tasks' list")
    tasks: list[GoalTask] = []
    seen: set[str] = set()
    for i, raw in enumerate(data["tasks"]):
        if not isinstance(raw, dict):
            raise ConfigError(f"tasks[{i}] must be an object")
        node_id = raw.get("id")
        if not isinstance(node_id, str) or not node_id.strip():
            raise ConfigError(f"tasks[{i}] needs a non-empty string 'id'")
        if node_id in seen:
            raise ConfigError(f"duplicate task id: {node_id!r}")
        seen.add(node_id)
        title = raw.get("title", node_id)
        if not isinstance(title, str):
            raise ConfigError(f"task {node_id!r}: 'title' must be a string")
        depends = raw.get("depends_on", [])
        if not isinstance(depends, list) or not all(isinstance(d, str) for d in depends):
            raise ConfigError(f"task {node_id!r}: 'depends_on' must be a list of task ids")
        test_command = raw.get("test_command")
        if test_command is not None and not isinstance(test_command, str):
            raise ConfigError(f"task {node_id!r}: 'test_command' must be a string")
        scope = _normalize_scope(raw.get("scope", ""), root, node_id)
        tasks.append(
            GoalTask(
                id=node_id,
                title=title,
                scope=scope,
                depends_on=tuple(depends),
                test_command=test_command,
            )
        )
    ids = {t.id for t in tasks}
    for t in tasks:
        for dep in t.depends_on:
            if dep not in ids:
                raise ConfigError(f"task {t.id!r} depends on unknown task {dep!r}")
    _check_acyclic(tasks)
    return tuple(tasks)


def _scopes_disjoint(a: str, b: str) -> bool:
    """True when neither scope contains the other. The whole-repo scope
    (``""``) overlaps everything."""
    if not a or not b:
        return False
    pa, pb = a.split("/"), b.split("/")
    return pa[: len(pb)] != pb and pb[: len(pa)] != pa


async def _run_integration(root: Path, command: str) -> tuple[bool, str]:
    """Run the full test command over the combined tree (bounded timeout)."""
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as exc:
        return False, f"could not run integration command: {exc}"
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), INTEGRATION_TIMEOUT_SECONDS)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return False, f"integration timed out after {INTEGRATION_TIMEOUT_SECONDS:.0f}s"
    return proc.returncode == 0, out.decode(errors="replace").strip()[-500:]


async def _git(cwd: Path, *args: str, stdin_text: str | None = None) -> tuple[int, str]:
    """Run a git command; return (returncode, combined output)."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(cwd),
        *args,
        stdin=asyncio.subprocess.PIPE if stdin_text is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate(stdin_text.encode() if stdin_text is not None else None)
    return proc.returncode or 0, out.decode(errors="replace")


async def _merge_node(root: Path, worktree: Path, node_id: str, base_sha: str) -> tuple[bool, str]:
    """Apply one node's worktree changes to the main tree.

    Diffs against the MERGE BASE (the root's HEAD when the worktree was
    created), not the worktree's HEAD — so work the node COMMITTED inside
    its worktree (aider auto-commit, a settings propagation bug) is still
    carried back. A node commit can no longer empty the patch (P0-2).
    Rename detection off; a failed apply is a CONFLICT, never silently
    clobbered.
    """
    rc, out = await _git(worktree, "add", "-A")
    if rc != 0:
        return False, f"git add failed in {node_id}: {out[:200]}"
    rc, patch = await _git(worktree, "diff", "--cached", "--no-renames", base_sha)
    if rc != 0:
        return False, f"git diff failed in {node_id}: {patch[:200]}"
    if not patch.strip():
        return True, ""
    rc, out = await _git(root, "apply", "--whitespace=nowarn", "-", stdin_text=patch)
    if rc != 0:
        return False, f"merge conflict applying {node_id}: {out[:300]}"
    return True, ""


async def _emit(bus: EventBus | None, event: str, data: dict) -> None:
    """Goal progress events — metadata-only, best-effort.

    Emitted as ``observation`` events (the bus rejects kinds outside
    ``EVENT_KINDS``); the goal-level name rides in ``data["event"]`` as
    ``goal_start`` / ``goal_node`` / ``goal_end``.
    """
    if bus is None:
        return
    await bus.emit("observation", {"source": "goal", "event": event, **data})


def _resolve_root(cwd: Path | None) -> Path:
    return Path(os.path.realpath(cwd if cwd is not None else os.getcwd()))


async def run_goal(
    goal: str,
    settings: Settings,
    *,
    cwd: Path | None = None,
    planner: Planner | None = None,
    backend_factory: Callable[[], AgentBackend] | None = None,
    bus: EventBus | None = None,
) -> GoalOutcome:
    """Plan ``goal`` into a task DAG and execute it as bounded loops.

    Each node runs :func:`pxx.loop.run_loop` with per-node settings
    (``scope=(node.scope,)``) and a fresh backend from ``backend_factory``.
    Independent nodes with disjoint scopes run in parallel; a failed node
    marks its dependents ``skipped`` and leaves completed nodes untouched.
    """
    root = _resolve_root(cwd)
    plan_text = await (planner or _default_planner)(goal)
    tasks = parse_plan(plan_text, root=root)
    await _emit(bus, "goal_start", {"tasks": len(tasks), "ids": [t.id for t in tasks]})

    # Per-node worktree isolation (Phase 22.3): in a git repo every node runs
    # in its OWN worktree — a node's mid-run state is invisible to siblings,
    # and changes merge only at integration. Disjoint-scope stays as the
    # fast-path guard only when no git isolation exists.
    isolated = (root / ".git").exists()
    worktrees: dict[str, Path] = {}
    base_sha = ""
    if isolated:
        rc, out = await _git(root, "rev-parse", "HEAD")
        base_sha = out.strip() if rc == 0 else ""
    if isolated:
        from .improve.scheduler import candidate_worktree

    # K12: the goal ties ONE net at start, before any node could merge back —
    # integration mutates the root repo. Nodes stay net-less (their
    # worktrees are the isolation).
    net = None
    if isolated and settings.safety_net:
        from .safety_net import tie_safety_net

        net = await tie_safety_net(root, f"goal-{uuid.uuid4().hex[:8]}")
        if net is not None and bus is not None:
            await bus.emit(
                "gate_decision",
                {
                    "gate": "safety_net",
                    "allowed": True,
                    "tag": net.tag or "",
                    "stash": net.stash_message or "",
                },
            )
    net_suffix = ""
    if net is not None:
        net_suffix = f" [net: {net.tag or 'no-tag'}{'+stash' if net.stash_message else ''}]"

    async def _run_node(node: GoalTask) -> RunOutcome:
        # The node verifier is the node's own test_command only; the
        # settings-level command is reserved for the final integration run.
        # auto_commit is forced OFF (mirror run_loop's round_settings): a
        # node committing inside its detached worktree leaves nothing for the
        # integration merge to carry back — silent total work loss (P0-2).
        node_settings = replace(
            settings,
            scope=(node.scope,) if node.scope else (),
            test_command=node.test_command,
            auto_commit=False,
        )
        node_cwd = root
        if isolated:
            node_cwd = candidate_worktree(root, f"goal-{node.id}")
            worktrees[node.id] = node_cwd
        return await run_loop(
            node.title,
            node_settings,
            cwd=node_cwd,
            backend_factory=backend_factory,
            bus=bus,
            # Goal nodes never tie their own net: per-node worktrees ARE the
            # isolation, and parallel nets spam + race the shared tag refs.
            safety_net=False,
        )

    status: dict[str, str] = {}  # task id -> "completed" | "failed" | "skipped"
    failed_codes: dict[str, TerminalCode] = {}

    while len(status) < len(tasks):
        remaining = [t for t in tasks if t.id not in status]
        # Dependents of failed/skipped nodes are skipped (transitively).
        for t in remaining:
            if all(d in status for d in t.depends_on) and not all(
                status[d] == "completed" for d in t.depends_on
            ):
                status[t.id] = "skipped"
                await _emit(bus, "goal_node", {"task": t.id, "status": "skipped"})
        ready = [
            t
            for t in tasks
            if t.id not in status and all(status.get(d) == "completed" for d in t.depends_on)
        ]
        if not ready:
            break  # unreachable: the DAG is validated acyclic
        # Parallelize: with worktree isolation the WHOLE ready set can run
        # (nodes cannot see each other); without it, only disjoint scopes.
        batch: list[GoalTask] = []
        for t in ready:
            if isolated or all(_scopes_disjoint(t.scope, other.scope) for other in batch):
                batch.append(t)
        outcomes = await asyncio.gather(*(_run_node(t) for t in batch))
        for t, outcome in zip(batch, outcomes, strict=True):
            status[t.id] = "completed" if outcome.code is TerminalCode.COMPLETED else "failed"
            if status[t.id] == "failed":
                failed_codes[t.id] = outcome.code
            await _emit(
                bus,
                "goal_node",
                {"task": t.id, "status": status[t.id], "code": str(outcome.code)},
            )

    completed = tuple(tid for tid, s in status.items() if s == "completed")
    skipped = tuple(tid for tid, s in status.items() if s == "skipped")
    failed = {tid: str(code) for tid, code in failed_codes.items()}

    # Integration merge (Phase 22.3): apply each completed node's worktree
    # changes to the main tree, in task order. A failed apply is a merge
    # CONFLICT — caught here, never silently clobbered.
    merge_conflict = ""
    if isolated and not failed_codes:
        for t in tasks:
            if t.id not in worktrees:
                continue
            ok, detail = await _merge_node(root, worktrees[t.id], t.id, base_sha)
            if not ok:
                merge_conflict = detail
                await _emit(bus, "goal_node", {"task": t.id, "status": "merge_conflict"})
                break

    # Boundary role: the Artifact-Reviewer vets the merged artifact at the
    # integration boundary (typed handoff artifact; protected-path content
    # rejects as OUT_OF_SCOPE).
    artifact_rejection = ""
    if isolated and not failed_codes and not merge_conflict:
        from .roles import DeterministicArtifactReviewer

        _rc, merged_diff = await _git(root, "diff", "--no-renames", "HEAD")
        artifact = await DeterministicArtifactReviewer().review_artifact(
            merged_diff, f"goal-{len(tasks)}-nodes"
        )
        await _emit(
            bus,
            "goal_node",
            {
                "task": "artifact-review",
                "status": "ok" if artifact.payload["ok"] else "rejected",
                "issues": len(artifact.payload["issues"]),
            },
        )
        if not artifact.payload["ok"]:
            artifact_rejection = "; ".join(artifact.payload["issues"])

    # Integration: full test command over the combined tree, only when every
    # node completed and a command is configured.
    integration = "skipped (no test_command)"
    if merge_conflict:
        integration = f"merge conflict ({merge_conflict})"
    elif artifact_rejection:
        integration = f"rejected by artifact review ({artifact_rejection})"
    elif failed_codes:
        integration = "skipped (node failures)"
    elif settings.test_command:
        ok, tail = await _run_integration(root, settings.test_command)
        integration = "passed" if ok else "failed"
        if not ok:
            log.info("goal integration failed: %s", tail)

    if merge_conflict:
        code = TerminalCode.MERGE_CONFLICT
    elif artifact_rejection:
        code = TerminalCode.OUT_OF_SCOPE
    elif failed_codes:
        first = next(t.id for t in tasks if t.id in failed_codes)
        code = failed_codes[first]
    elif integration == "failed":
        code = TerminalCode.TEST_REGRESSION
    else:
        code = TerminalCode.COMPLETED

    parts = [f"{len(completed)}/{len(tasks)} tasks completed"]
    if failed:
        parts.append("failed: " + ", ".join(f"{tid} ({c})" for tid, c in failed.items()))
    if skipped:
        parts.append("skipped: " + ", ".join(skipped))
    parts.append(f"integration {integration}")
    summary = "; ".join(parts) + net_suffix
    await _emit(
        bus,
        "goal_end",
        {
            "code": str(code),
            "completed": len(completed),
            "failed": len(failed),
            "skipped": len(skipped),
            "integration": integration,
        },
    )
    return GoalOutcome(
        code=code, completed=completed, failed=failed, skipped=skipped, summary=summary
    )
