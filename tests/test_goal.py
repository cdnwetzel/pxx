"""Tests for pxx.goal: goal -> validated task DAG -> bounded loops -> integrate.

The planner is a stub returning fixed DAG JSON; the backend factory hands
out scripted backends (same pattern as tests/test_loop.py). Session lazily
imports the memory/tools groups at run time, so those are stubbed in
sys.modules. All tests are deterministic: no network, no git, no Ollama.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

from pxx.backends.base import BackendCapabilities
from pxx.config import Settings
from pxx.errors import BackendUnavailable, ConfigError, ScopeViolation
from pxx.events import EventBus
from pxx.goal import GoalOutcome, parse_plan, run_goal
from pxx.outcome import RunOutcome, TerminalCode
from pxx.safety import PermissionMode


@pytest.fixture(autouse=True)
def _stub_unbuilt_groups(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub pxx.memory/pxx.tools so the real Session can run in tests."""
    memory = types.ModuleType("pxx.memory")
    capture = types.ModuleType("pxx.memory.capture")
    inject = types.ModuleType("pxx.memory.inject")
    store = types.ModuleType("pxx.memory.store")
    tools = types.ModuleType("pxx.tools")

    async def record_observations(*args: object, **kwargs: object) -> None:
        await asyncio.sleep(0)

    async def build_context(*args: object, **kwargs: object) -> str:
        await asyncio.sleep(0)
        return ""

    class MemoryStore:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def close(self) -> None:
            pass

    class ToolRegistry:
        pass

    def default_registry() -> ToolRegistry:
        return ToolRegistry()

    capture.record_observations = record_observations  # type: ignore[attr-defined]
    inject.build_context = build_context  # type: ignore[attr-defined]
    store.MemoryStore = MemoryStore  # type: ignore[attr-defined]
    tools.ToolRegistry = ToolRegistry  # type: ignore[attr-defined]
    tools.default_registry = default_registry  # type: ignore[attr-defined]
    for name, module in {
        "pxx.memory": memory,
        "pxx.memory.capture": capture,
        "pxx.memory.inject": inject,
        "pxx.memory.store": store,
        "pxx.tools": tools,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)


class ScriptedBackend:
    """Protocol-compatible backend: applies scripted edits, returns an outcome."""

    name = "scripted"
    capabilities = BackendCapabilities(
        streaming=False, tools=False, interactive=False, headless=True
    )

    def __init__(
        self, edits: dict[str, str] | None = None, outcome: RunOutcome | None = None
    ) -> None:
        self.edits = edits or {}
        self.outcome = outcome
        self.tasks: list[str] = []
        self.scopes: list[tuple[str, ...]] = []

    async def run(self, task: str, ctx: object) -> RunOutcome:
        await asyncio.sleep(0)
        self.tasks.append(task)
        self.scopes.append(tuple(ctx.settings.scope))  # type: ignore[attr-defined]
        for rel, content in self.edits.items():
            path = Path(ctx.cwd) / rel  # type: ignore[attr-defined]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        return self.outcome or RunOutcome(code=TerminalCode.COMPLETED, summary="ok", tokens=10)

    async def cancel(self) -> None:
        await asyncio.sleep(0)


class Factory:
    """Hands out a fresh scripted backend per node, in script order."""

    def __init__(self, script: list[ScriptedBackend]) -> None:
        self.script = script
        self.made: list[ScriptedBackend] = []

    def __call__(self) -> ScriptedBackend:
        backend = self.script[min(len(self.made), len(self.script) - 1)]
        self.made.append(backend)
        return backend


class FailOnBackend(ScriptedBackend):
    """Fails with a terminal code for one specific task; order-independent."""

    def __init__(self, fail_task: str, code: TerminalCode) -> None:
        super().__init__()
        self.fail_task = fail_task
        self.code = code

    async def run(self, task: str, ctx: object) -> RunOutcome:
        if task == self.fail_task:
            return RunOutcome(code=self.code, summary="boom")
        return await super().run(task, ctx)


class EditsInWorktree(ScriptedBackend):
    """Applies its edits only inside the named worktree — order-independent.

    Parallel node loops race for factory backends; which node receives a
    given scripted backend is timing-dependent (K5's per-loop net shuffled
    the old ordering and flaked CI on 3.11). Pin the edit to the place,
    not the handout order.
    """

    def __init__(self, worktree_name: str, edits: dict[str, str]) -> None:
        super().__init__(edits)
        self.worktree_name = worktree_name

    async def run(self, task: str, ctx: object) -> RunOutcome:
        if Path(ctx.cwd).name != self.worktree_name:  # type: ignore[attr-defined]
            return RunOutcome(code=TerminalCode.COMPLETED, summary="ok", tokens=10)
        return await super().run(task, ctx)


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    base: dict = {
        "permission": PermissionMode.AUTO,
        "memory_enabled": False,
        "state_dir": tmp_path / "state",
    }
    base.update(overrides)
    return Settings(**base)


def _planner(dag: dict):
    async def plan(goal: str) -> str:
        await asyncio.sleep(0)
        return json.dumps(dag)

    return plan


def _dag(*tasks: dict) -> dict:
    return {"tasks": list(tasks)}


def _task(
    node_id: str,
    *,
    scope: str = "",
    depends_on: list[str] | None = None,
    test_command: str | None = None,
) -> dict:
    node: dict = {"id": node_id, "title": f"task {node_id}", "scope": scope}
    if depends_on is not None:
        node["depends_on"] = depends_on
    if test_command is not None:
        node["test_command"] = test_command
    return node


def _goal_events(bus: EventBus) -> list[dict]:
    return [
        e.data for e in bus.history if e.kind == "observation" and e.data.get("source") == "goal"
    ]


# --- plan validation ---------------------------------------------------------


def test_parse_plan_normalizes_scopes(tmp_path: Path) -> None:
    text = json.dumps(
        _dag(
            _task("a", scope="src/./pkg"),
            _task("b", scope="."),
            _task("c"),
        )
    )
    tasks = parse_plan(text, root=tmp_path)
    assert [t.scope for t in tasks] == ["src/pkg", "", ""]
    assert tasks[0].depends_on == ()
    assert tasks[0].test_command is None


def test_parse_plan_rejects_cycle(tmp_path: Path) -> None:
    dag = _dag(
        _task("a", depends_on=["b"]),
        _task("b", depends_on=["a"]),
    )
    with pytest.raises(ConfigError, match="cycle"):
        parse_plan(json.dumps(dag), root=tmp_path)


def test_parse_plan_rejects_self_cycle(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cycle"):
        parse_plan(json.dumps(_dag(_task("a", depends_on=["a"]))), root=tmp_path)


def test_parse_plan_rejects_duplicate_ids(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="duplicate"):
        parse_plan(json.dumps(_dag(_task("a"), _task("a"))), root=tmp_path)


def test_parse_plan_rejects_escaping_scope(tmp_path: Path) -> None:
    with pytest.raises(ScopeViolation):
        parse_plan(json.dumps(_dag(_task("a", scope="../outside"))), root=tmp_path)


def test_parse_plan_rejects_absolute_scope(tmp_path: Path) -> None:
    with pytest.raises(ScopeViolation):
        parse_plan(json.dumps(_dag(_task("a", scope="/etc"))), root=tmp_path)


def test_parse_plan_rejects_dangling_dependency(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="unknown task"):
        parse_plan(json.dumps(_dag(_task("a", depends_on=["ghost"]))), root=tmp_path)


def test_parse_plan_rejects_malformed_json(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="invalid JSON"):
        parse_plan("not json {", root=tmp_path)
    with pytest.raises(ConfigError, match="tasks"):
        parse_plan(json.dumps({"nodes": []}), root=tmp_path)


def test_run_goal_propagates_plan_validation_error(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    dag = _dag(_task("a", depends_on=["b"]), _task("b", depends_on=["a"]))
    with pytest.raises(ConfigError):
        asyncio.run(run_goal("goal", _settings(tmp_path), cwd=work, planner=_planner(dag)))


def test_default_planner_raises_backend_unavailable(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(BackendUnavailable):
        asyncio.run(run_goal("goal", _settings(tmp_path), cwd=work))


# --- execution ---------------------------------------------------------------


def test_sequential_dependency_order_and_node_scopes(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    dag = _dag(
        _task("b", scope="src/b", depends_on=["a"]),
        _task("a", scope="src/a"),
    )
    first, second = ScriptedBackend(), ScriptedBackend()
    outcome = asyncio.run(
        run_goal(
            "goal",
            _settings(tmp_path),
            cwd=work,
            planner=_planner(dag),
            backend_factory=Factory([first, second]),
        )
    )
    assert outcome.code is TerminalCode.COMPLETED
    assert outcome.completed == ("a", "b")  # plan order, executed a before b
    assert first.tasks == ["task a"]
    assert second.tasks == ["task b"]
    # per-node settings carry the node scope (fresh, bounded context)
    assert first.scopes == [("src/a",)]
    assert second.scopes == [("src/b",)]


def test_disjoint_scopes_run_in_parallel(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    probe = {"count": 0, "both": asyncio.Event()}

    class ProbeBackend(ScriptedBackend):
        async def run(self, task: str, ctx: object) -> RunOutcome:
            probe["count"] += 1
            if probe["count"] == 2:
                probe["both"].set()
            # Fails (timeout -> non-COMPLETED) unless both nodes overlap.
            await asyncio.wait_for(probe["both"].wait(), 1.0)
            return await super().run(task, ctx)

    dag = _dag(_task("a", scope="src/a"), _task("b", scope="src/b"))
    outcome = asyncio.run(
        run_goal(
            "goal",
            _settings(tmp_path),
            cwd=work,
            planner=_planner(dag),
            backend_factory=Factory([ProbeBackend(), ProbeBackend()]),
        )
    )
    assert outcome.code is TerminalCode.COMPLETED
    assert outcome.completed == ("a", "b")
    assert probe["both"].is_set()  # both node bodies overlapped in time


def test_overlapping_scopes_run_sequentially(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    records: list[tuple[str, str]] = []

    class RecordingBackend(ScriptedBackend):
        async def run(self, task: str, ctx: object) -> RunOutcome:
            records.append(("start", task))
            await asyncio.sleep(0.01)
            records.append(("end", task))
            return await super().run(task, ctx)

    dag = _dag(_task("a", scope="src"), _task("b", scope="src/sub"))
    outcome = asyncio.run(
        run_goal(
            "goal",
            _settings(tmp_path),
            cwd=work,
            planner=_planner(dag),
            backend_factory=Factory([RecordingBackend(), RecordingBackend()]),
        )
    )
    assert outcome.code is TerminalCode.COMPLETED
    # nested scopes overlap -> strictly sequential, no interleaving
    assert records == [
        ("start", "task a"),
        ("end", "task a"),
        ("start", "task b"),
        ("end", "task b"),
    ]


def test_failure_skips_dependents_and_preserves_completed(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    # a and c run in the same parallel batch, so the failing backend is
    # keyed on task text, not factory order.
    factory = Factory([FailOnBackend("task a", TerminalCode.MODEL_UNAVAILABLE) for _ in range(2)])
    dag = _dag(
        _task("a", scope="src/a"),
        _task("b", scope="src/b", depends_on=["a"]),
        _task("c", scope="src/c"),
    )
    outcome = asyncio.run(
        run_goal(
            "goal",
            _settings(tmp_path),
            cwd=work,
            planner=_planner(dag),
            backend_factory=factory,
        )
    )
    assert outcome.code is TerminalCode.MODEL_UNAVAILABLE
    assert outcome.failed == {"a": "MODEL_UNAVAILABLE"}
    assert outcome.skipped == ("b",)
    assert outcome.completed == ("c",)
    assert len(factory.made) == 2  # the dependent node never ran


def test_transitive_skip(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    dag = _dag(
        _task("a", scope="src/a"),
        _task("b", scope="src/b", depends_on=["a"]),
        _task("c", scope="src/c", depends_on=["b"]),
    )
    outcome = asyncio.run(
        run_goal(
            "goal",
            _settings(tmp_path),
            cwd=work,
            planner=_planner(dag),
            backend_factory=Factory([FailOnBackend("task a", TerminalCode.MODEL_UNAVAILABLE)]),
        )
    )
    assert outcome.completed == ()
    assert outcome.failed == {"a": "MODEL_UNAVAILABLE"}
    assert outcome.skipped == ("b", "c")


def test_node_test_command_verifies_node(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    fail_tests = f'{sys.executable} -c "import sys; sys.exit(1)"'
    dag = _dag(
        _task("a", scope="src/a", test_command=fail_tests),
        _task("b", scope="src/b", depends_on=["a"]),
    )
    outcome = asyncio.run(
        run_goal(
            "goal",
            _settings(tmp_path),
            cwd=work,
            planner=_planner(dag),
            backend_factory=Factory([ScriptedBackend()]),
        )
    )
    # the node verifier (its test command) never passes and nothing changes
    # -> the recovery ladder stops the oscillation (not the blunt round cap)
    assert outcome.failed == {"a": "LOOP_DETECTED"}
    assert outcome.skipped == ("b",)
    assert outcome.code is TerminalCode.LOOP_DETECTED


# --- integration -------------------------------------------------------------


def test_integration_command_runs_over_combined_tree(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    marker = (
        f"{sys.executable} -c \"from pathlib import Path; Path('marker.txt').write_text('ran')\""
    )
    dag = _dag(_task("a", scope="src/a"), _task("b", scope="src/b"))
    outcome = asyncio.run(
        run_goal(
            "goal",
            _settings(tmp_path, test_command=marker),
            cwd=work,
            planner=_planner(dag),
            backend_factory=Factory([ScriptedBackend(), ScriptedBackend()]),
        )
    )
    assert outcome.code is TerminalCode.COMPLETED
    assert (work / "marker.txt").read_text() == "ran"
    assert "integration passed" in outcome.summary


def test_integration_failure_is_gate_failed(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    fail = f'{sys.executable} -c "import sys; sys.exit(1)"'
    outcome = asyncio.run(
        run_goal(
            "goal",
            _settings(tmp_path, test_command=fail),
            cwd=work,
            planner=_planner(_dag(_task("a", scope="src/a"))),
            backend_factory=Factory([ScriptedBackend()]),
        )
    )
    assert outcome.code is TerminalCode.TEST_REGRESSION
    assert outcome.completed == ("a",)
    assert outcome.failed == {}
    assert "integration failed" in outcome.summary


def test_integration_skipped_without_test_command(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    outcome = asyncio.run(
        run_goal(
            "goal",
            _settings(tmp_path),
            cwd=work,
            planner=_planner(_dag(_task("a", scope="src/a"))),
            backend_factory=Factory([ScriptedBackend()]),
        )
    )
    assert outcome.code is TerminalCode.COMPLETED
    assert "integration skipped" in outcome.summary


# --- outcome shape + events ---------------------------------------------------


def test_goal_outcome_shape(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    outcome = asyncio.run(
        run_goal(
            "goal",
            _settings(tmp_path),
            cwd=work,
            planner=_planner(_dag(_task("a", scope="src/a"))),
            backend_factory=Factory([ScriptedBackend()]),
        )
    )
    assert isinstance(outcome, GoalOutcome)
    assert isinstance(outcome.code, TerminalCode)
    assert isinstance(outcome.completed, tuple)
    assert isinstance(outcome.failed, dict)
    assert isinstance(outcome.skipped, tuple)
    assert isinstance(outcome.summary, str)


def test_progress_events_emitted_on_bus(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    bus = EventBus()
    outcome = asyncio.run(
        run_goal(
            "goal",
            _settings(tmp_path),
            cwd=work,
            planner=_planner(_dag(_task("a", scope="src/a"))),
            backend_factory=Factory([ScriptedBackend()]),
            bus=bus,
        )
    )
    assert outcome.code is TerminalCode.COMPLETED
    events = [e["event"] for e in _goal_events(bus)]
    assert events == ["goal_start", "goal_node", "goal_end"]
    node = _goal_events(bus)[1]
    assert node["task"] == "a"
    assert node["status"] == "completed"
    end = _goal_events(bus)[2]
    assert end["code"] == "COMPLETED"


# --- B10.1: per-node worktree isolation + integration merge (O4) ----------------------

GIT = shutil.which("git")
needs_git = pytest.mark.skipif(GIT is None, reason="git not available")


def _git(path: Path, *args: str) -> str:
    proc = subprocess.run([GIT, *args], cwd=path, check=True, capture_output=True, text=True)
    return proc.stdout.strip()


def _init_repo(path: Path, files: dict[str, str] | None = None) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run([GIT, "init", "-q"], cwd=path, check=True, capture_output=True)
    for rel, content in (files or {}).items():
        target = path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    subprocess.run([GIT, "add", "-A"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        [
            GIT,
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@e.c",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "init",
        ],
        cwd=path,
        check=True,
        capture_output=True,
    )


@needs_git
def test_node_worktree_isolation_invisible_to_siblings(tmp_path: Path) -> None:
    """Node b runs in parallel with node a but CANNOT see a's mid-run work:
    its verifier (alpha.py absent) passes only in an isolated worktree."""
    repo = tmp_path / "repo"
    _init_repo(
        repo,
        files={
            "check_absent.py": (
                "import sys\nfrom pathlib import Path\n"
                "sys.exit(0 if not Path('alpha.py').exists() else 1)\n"
            )
        },
    )
    dag = _dag(
        _task("a", scope="."),
        _task("b", scope=".", test_command=f"{sys.executable} check_absent.py"),
    )
    factory = Factory([EditsInWorktree("goal-a", {"alpha.py": "A = 1\n"}), ScriptedBackend()])
    outcome = asyncio.run(
        run_goal(
            "goal",
            _settings(tmp_path),
            cwd=repo,
            planner=_planner(dag),
            backend_factory=factory,
        )
    )
    assert outcome.code is TerminalCode.COMPLETED
    assert set(outcome.completed) == {"a", "b"}
    # integration merge brought the node's work back to the main tree
    assert (repo / "alpha.py").read_text() == "A = 1\n"


@needs_git
def test_integration_merges_disjoint_node_changes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    dag = _dag(_task("a"), _task("b"))
    factory = Factory(
        [
            ScriptedBackend(edits={"alpha.py": "A = 1\n"}),
            ScriptedBackend(edits={"beta.py": "B = 2\n"}),
        ]
    )
    outcome = asyncio.run(
        run_goal(
            "goal",
            _settings(tmp_path),
            cwd=repo,
            planner=_planner(dag),
            backend_factory=factory,
        )
    )
    assert outcome.code is TerminalCode.COMPLETED
    assert (repo / "alpha.py").exists()
    assert (repo / "beta.py").exists()


@needs_git
def test_conflicting_nodes_caught_at_integration_not_clobbered(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo, files={"shared.py": "x = 0\n"})
    dag = _dag(_task("a"), _task("b"))
    factory = Factory(
        [
            EditsInWorktree("goal-a", {"shared.py": "x = 1\n"}),
            EditsInWorktree("goal-b", {"shared.py": "x = 999\n"}),
        ]
    )
    outcome = asyncio.run(
        run_goal(
            "goal",
            _settings(tmp_path),
            cwd=repo,
            planner=_planner(dag),
            backend_factory=factory,
        )
    )
    assert outcome.code is TerminalCode.MERGE_CONFLICT
    assert "merge conflict" in outcome.summary
    # the first applied patch stands; the tree is not a silent mix
    assert (repo / "shared.py").read_text() == "x = 1\n"


@needs_git
def test_artifact_reviewer_rejects_protected_merge(tmp_path: Path) -> None:
    """B10.2 at the boundary: a merged artifact touching a protected path is
    rejected by the Artifact-Reviewer as OUT_OF_SCOPE."""
    repo = tmp_path / "repo"
    _init_repo(repo, files={"pxx/safety.py": "SAFE = True\n"})
    dag = _dag(_task("a"))
    factory = Factory([ScriptedBackend(edits={"pxx/safety.py": "SAFE = False\n"})])
    outcome = asyncio.run(
        run_goal(
            "goal",
            _settings(tmp_path),
            cwd=repo,
            planner=_planner(dag),
            backend_factory=factory,
        )
    )
    assert outcome.code is TerminalCode.OUT_OF_SCOPE
    assert "artifact review" in outcome.summary


@needs_git
def test_goal_ties_one_net_at_start(tmp_path: Path) -> None:
    """K12: one net per goal run (merge-back mutates the root repo) — never
    per node."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "wip.txt").write_text("user work\n")
    bus = EventBus()
    dag = _dag(_task("a"), _task("b"))
    factory = Factory(
        [
            EditsInWorktree("goal-a", {"alpha.py": "A = 1\n"}),
            EditsInWorktree("goal-b", {"beta.py": "B = 2\n"}),
        ]
    )
    outcome = asyncio.run(
        run_goal(
            "goal",
            _settings(tmp_path),
            cwd=repo,
            planner=_planner(dag),
            backend_factory=factory,
            bus=bus,
        )
    )
    assert outcome.code is TerminalCode.COMPLETED
    assert "[net: pxx-pre/" in outcome.summary and "+stash" in outcome.summary

    def git(*args: str) -> str:
        proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=False)
        assert proc.returncode == 0, proc.stderr
        return proc.stdout.strip()

    # once per goal run — not per node (2 nodes ran)
    assert len(git("tag", "-l", "pxx-pre/*").splitlines()) == 1
    assert len(git("stash", "list").splitlines()) == 1
    nets = [
        e for e in bus.history if e.kind == "gate_decision" and e.data.get("gate") == "safety_net"
    ]
    assert len(nets) == 1 and nets[0].data["stash"]
    # merge-back landed on the stashed-clean tree; the user's dirt stays parked
    assert (repo / "alpha.py").read_text() == "A = 1\n"
    assert not (repo / "wip.txt").exists()


@needs_git
def test_goal_net_disabled_by_knob(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "wip.txt").write_text("user work\n")
    dag = _dag(_task("a"))
    factory = Factory([ScriptedBackend()])
    outcome = asyncio.run(
        run_goal(
            "goal",
            _settings(tmp_path, safety_net=False),
            cwd=repo,
            planner=_planner(dag),
            backend_factory=factory,
        )
    )
    assert outcome.code is TerminalCode.COMPLETED
    assert "[net:" not in outcome.summary
    tags = subprocess.run(
        ["git", "tag", "-l", "pxx-pre/*"], cwd=repo, capture_output=True, text=True
    )
    assert tags.stdout == ""
    assert (repo / "wip.txt").read_text() == "user work\n"  # untouched


@needs_git
def test_goal_auto_commit_flag_still_merges_node_work(tmp_path: Path) -> None:
    """P0-2 regression: --commit on goal must NOT let nodes commit inside
    their worktrees — node settings force auto_commit=False, and the merge
    still carries every node's work back to the root."""
    from dataclasses import replace as _replace

    repo = tmp_path / "repo"
    _init_repo(repo)
    dag = _dag(_task("a"), _task("b"))
    factory = Factory(
        [
            ScriptedBackend(edits={"alpha.py": "A = 1\n"}),
            ScriptedBackend(edits={"beta.py": "B = 2\n"}),
        ]
    )
    outcome = asyncio.run(
        run_goal(
            "goal",
            _replace(_settings(tmp_path), auto_commit=True),
            cwd=repo,
            planner=_planner(dag),
            backend_factory=factory,
        )
    )
    assert outcome.code is TerminalCode.COMPLETED
    assert (repo / "alpha.py").read_text() == "A = 1\n"
    assert (repo / "beta.py").read_text() == "B = 2\n"


@needs_git
def test_committed_worktree_still_merges_back(tmp_path: Path) -> None:
    """P0-2 hardening: a node whose backend COMMITS inside its worktree
    (aider auto-commit style) must still merge back — the merge diffs
    against the merge base, not the worktree's HEAD."""

    class CommittingBackend(ScriptedBackend):
        async def run(self, task, ctx):
            outcome = await super().run(task, ctx)
            root = Path(ctx.cwd)
            _git(root, "add", "-A")
            _git(
                root,
                "-c",
                "user.name=test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-q",
                "-m",
                "node auto-commit",
            )
            return outcome

    repo = tmp_path / "repo"
    _init_repo(repo)
    dag = _dag(_task("a"))
    factory = Factory([CommittingBackend(edits={"alpha.py": "A = 1\n"})])
    outcome = asyncio.run(
        run_goal(
            "goal",
            _settings(tmp_path),
            cwd=repo,
            planner=_planner(dag),
            backend_factory=factory,
        )
    )
    assert outcome.code is TerminalCode.COMPLETED
    assert (repo / "alpha.py").read_text() == "A = 1\n"
