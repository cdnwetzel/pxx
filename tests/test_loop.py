"""Tests for pxx.loop: bounded autonomous edit -> test -> review loop.

Uses a protocol-compatible scripted backend (fresh instance per round via a
factory) and scripted reviewers. Git-repo tests use real tmp repos via
subprocess (git is present on dev machines); the no-repo path is covered
separately. No network, no Ollama, no aider.

Session lazily imports the memory/tools groups at run time; those groups are
built in parallel, so they are stubbed in sys.modules for these tests.
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
from pxx.events import EventBus
from pxx.loop import run_loop
from pxx.outcome import RunOutcome, TerminalCode
from pxx.review import ReviewMode, ReviewUnavailable
from pxx.safety import Budgets, PermissionMode


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

    async def run(self, task: str, ctx: object) -> RunOutcome:
        await asyncio.sleep(0)
        self.tasks.append(task)
        for rel, content in self.edits.items():
            path = Path(ctx.cwd) / rel  # type: ignore[attr-defined]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        return self.outcome or RunOutcome(code=TerminalCode.COMPLETED, summary="ok", tokens=10)

    async def cancel(self) -> None:
        await asyncio.sleep(0)


class Factory:
    """Hands out a FRESH scripted backend per round (rounds beyond the script
    reuse the last entry); records every instance it made."""

    def __init__(self, script: list[ScriptedBackend]) -> None:
        self.script = script
        self.made: list[ScriptedBackend] = []

    def __call__(self) -> ScriptedBackend:
        backend = self.script[min(len(self.made), len(self.script) - 1)]
        self.made.append(backend)
        return backend


class ScriptedReviewer:
    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    async def review(self, diff: str, task: str) -> str:
        await asyncio.sleep(0)
        self.calls.append((diff, task))
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    base: dict = {
        "permission": PermissionMode.AUTO,
        "memory_enabled": False,
        "state_dir": tmp_path / "state",
    }
    base.update(overrides)
    return Settings(**base)


GIT = shutil.which("git")
needs_git = pytest.mark.skipif(GIT is None, reason="git not available")


def _git(root: Path, *args: str) -> None:
    subprocess.run([GIT, *args], cwd=root, check=True, capture_output=True)


def _init_repo(path: Path, files: dict[str, str] | None = None) -> None:
    _git(path, "init", "-q")
    for rel, content in (files or {}).items():
        target = path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    _git(path, "add", "-A")
    _git(
        path,
        "-c",
        "user.name=test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "init",
    )


def _gate_events(bus: EventBus, gate: str) -> list[dict]:
    return [e.data for e in bus.history if e.kind == "gate_decision" and e.data.get("gate") == gate]


# --- no-repo paths ----------------------------------------------------------


def test_no_repo_completes_without_tests_or_reviewer(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    factory = Factory([ScriptedBackend(edits={"out.txt": "hello"})])
    outcome = asyncio.run(run_loop("do it", _settings(tmp_path), cwd=work, backend_factory=factory))
    assert outcome.code is TerminalCode.COMPLETED
    assert outcome.rounds == 1
    assert outcome.tokens == 10
    assert (work / "out.txt").read_text() == "hello"


def test_no_repo_review_gets_marker_diff(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    reviewer = ScriptedReviewer(["VERDICT: APPROVE"])
    outcome = asyncio.run(
        run_loop(
            "do it",
            _settings(tmp_path),
            cwd=work,
            backend_factory=Factory([ScriptedBackend(edits={"a.txt": "x"})]),
            reviewer=reviewer,
        )
    )
    assert outcome.code is TerminalCode.COMPLETED
    assert "no git repository" in reviewer.calls[0][0]


def test_backend_failure_short_circuits(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    factory = Factory(
        [ScriptedBackend(outcome=RunOutcome(code=TerminalCode.MODEL_UNAVAILABLE, summary="boom"))]
    )
    outcome = asyncio.run(run_loop("do it", _settings(tmp_path), cwd=work, backend_factory=factory))
    assert outcome.code is TerminalCode.MODEL_UNAVAILABLE
    assert len(factory.made) == 1  # no further rounds


# --- review gate ------------------------------------------------------------


@needs_git
def test_review_approve_completes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    bus = EventBus()
    reviewer = ScriptedReviewer(["VERDICT: APPROVE"])
    outcome = asyncio.run(
        run_loop(
            "fix it",
            _settings(tmp_path),
            cwd=repo,
            backend_factory=Factory([ScriptedBackend(edits={"src.py": "x = 1\n"})]),
            reviewer=reviewer,
            bus=bus,
        )
    )
    assert outcome.code is TerminalCode.COMPLETED
    assert outcome.rounds == 1
    assert _gate_events(bus, "scope_recheck")[0]["allowed"]
    assert _gate_events(bus, "review")[0]["verdict"] == "APPROVE"


@needs_git
def test_loop_ties_one_net_for_the_whole_run(tmp_path: Path) -> None:
    """K5: the loop ties exactly one net at start — per-round Sessions must
    not re-stash (rounds share the loop's net)."""
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "dirty.txt").write_text("user work in progress\n")
    bus = EventBus()
    reviewer = ScriptedReviewer(["VERDICT: APPROVE"])
    outcome = asyncio.run(
        run_loop(
            "fix it",
            _settings(tmp_path),
            cwd=repo,
            backend_factory=Factory([ScriptedBackend(edits={"src.py": "x = 1\n"})]),
            reviewer=reviewer,
            bus=bus,
        )
    )
    assert outcome.code is TerminalCode.COMPLETED
    assert "[net: pxx-pre/" in outcome.summary and "+stash" in outcome.summary

    def git(*args: str) -> str:
        proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=False)
        assert proc.returncode == 0, proc.stderr
        return proc.stdout.strip()

    assert len(git("tag", "-l", "pxx-pre/*").splitlines()) == 1  # not per-round
    assert len(git("stash", "list").splitlines()) == 1
    nets = _gate_events(bus, "safety_net")
    assert len(nets) == 1 and nets[0]["stash"]


@needs_git
def test_revise_then_approve_uses_healing_prompt(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    first = ScriptedBackend(edits={"a.py": "v1\n"})
    second = ScriptedBackend(edits={"a.py": "v2\n"})
    factory = Factory([first, second])
    reviewer = ScriptedReviewer(
        ["VERDICT: REVISE\nF-001 [high] a.py:1 bad logic", "VERDICT: APPROVE"]
    )
    outcome = asyncio.run(
        run_loop(
            "the task",
            _settings(tmp_path),
            cwd=repo,
            backend_factory=factory,
            reviewer=reviewer,
            max_rounds=3,
        )
    )
    assert outcome.code is TerminalCode.COMPLETED
    assert outcome.rounds == 2
    # fresh backend per round (invariant)
    assert len(factory.made) == 2
    assert factory.made[0] is not factory.made[1]
    # round 1 gets the raw task; round 2 gets the healing prompt
    assert first.tasks[0] == "the task"
    assert "F-001" in second.tasks[0]
    assert "a.py:1" in second.tasks[0]
    assert "the task" in second.tasks[0]


@needs_git
def test_round_cap(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    factory = Factory([ScriptedBackend(edits={"a.py": f"v{i}\n"}) for i in range(2)])
    reviewer = ScriptedReviewer(["VERDICT: REVISE\nF-001 [medium] a.py:1 still wrong"] * 2)
    outcome = asyncio.run(
        run_loop(
            "task",
            _settings(tmp_path),
            cwd=repo,
            backend_factory=factory,
            reviewer=reviewer,
            max_rounds=2,
        )
    )
    assert outcome.code is TerminalCode.ROUND_CAP
    assert outcome.rounds == 2
    assert len(factory.made) == 2
    assert len(outcome.findings) == 1  # last review findings carried in outcome


@needs_git
def test_blocked_review_unavailable_gets_specific_code(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    reviewer = ScriptedReviewer([ReviewUnavailable("endpoint down")])
    outcome = asyncio.run(
        run_loop(
            "task",
            _settings(tmp_path),
            cwd=repo,
            backend_factory=Factory([ScriptedBackend(edits={"a.py": "x\n"})]),
            reviewer=reviewer,
        )
    )
    assert outcome.code is TerminalCode.REVIEW_UNAVAILABLE
    assert outcome.rounds == 1


@needs_git
def test_advisory_review_never_blocks(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    reviewer = ScriptedReviewer(["unparseable reviewer output"])
    outcome = asyncio.run(
        run_loop(
            "task",
            _settings(tmp_path),
            cwd=repo,
            backend_factory=Factory([ScriptedBackend(edits={"a.py": "x\n"})]),
            reviewer=reviewer,
            review_mode=ReviewMode.ADVISORY,
        )
    )
    assert outcome.code is TerminalCode.COMPLETED


# --- guards ------------------------------------------------------------------


@needs_git
def test_scope_violation_after_round(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "src").mkdir()
    bus = EventBus()
    settings = _settings(tmp_path, scope=("src",))
    factory = Factory([ScriptedBackend(edits={"evil.txt": "pwned"})])
    outcome = asyncio.run(run_loop("task", settings, cwd=repo, backend_factory=factory, bus=bus))
    assert outcome.code is TerminalCode.OUT_OF_SCOPE
    assert outcome.rounds == 1
    events = _gate_events(bus, "scope_recheck")
    assert events and events[0]["allowed"] is False
    assert "evil.txt" in events[0]["violations"]


@needs_git
def test_diff_budget_cap(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, files={"big.txt": "old\n"})
    settings = _settings(tmp_path, budgets=Budgets(max_diff_lines=5))
    factory = Factory([ScriptedBackend(edits={"big.txt": "line\n" * 20})])
    bus = EventBus()
    outcome = asyncio.run(run_loop("task", settings, cwd=repo, backend_factory=factory, bus=bus))
    assert outcome.code is TerminalCode.DIFF_CAP
    events = _gate_events(bus, "diff_budget")
    assert events and events[0]["allowed"] is False


# --- test gate / monotonic progress ------------------------------------------


@needs_git
def test_tests_pass_then_review(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    bus = EventBus()
    reviewer = ScriptedReviewer(["VERDICT: APPROVE"])
    outcome = asyncio.run(
        run_loop(
            "task",
            _settings(tmp_path),
            cwd=repo,
            backend_factory=Factory([ScriptedBackend(edits={"a.py": "x = 1\n"})]),
            test_command="true",
            reviewer=reviewer,
            bus=bus,
        )
    )
    assert outcome.code is TerminalCode.COMPLETED
    tests = _gate_events(bus, "tests")
    assert tests and tests[0]["passed"] and tests[0]["allowed"]


@needs_git
def test_regression_on_new_failures(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(
        repo,
        files={
            "check.py": (
                "import sys\n"
                "from pathlib import Path\n"
                "n = int(Path('count.txt').read_text().strip())\n"
                "for i in range(n):\n"
                "    print(f'FAILED test_case_{i}')\n"
                "sys.exit(1)\n"
            ),
            "count.txt": "0",
        },
    )
    factory = Factory(
        [
            ScriptedBackend(edits={"count.txt": "1"}),  # baseline: {test_case_0}
            ScriptedBackend(edits={"count.txt": "2"}),  # adds test_case_1 -> stop
        ]
    )
    outcome = asyncio.run(
        run_loop(
            "task",
            _settings(tmp_path),
            cwd=repo,
            backend_factory=factory,
            test_command=f"{sys.executable} check.py",
            max_rounds=5,
        )
    )
    assert outcome.code is TerminalCode.TEST_REGRESSION
    assert outcome.rounds == 2


def test_greenfield_failing_baseline_never_completes(tmp_path: Path) -> None:
    """A suite that fails from round 1 — the greenfield case where round 1 IS the
    coder's own first (broken) attempt, so there is no known-good baseline — must
    never reach COMPLETED, even though 'no NEW failures vs the broken baseline'
    trivially holds. The gate refuses to call broken-and-not-regressing code done;
    this is the negative control against a greenfield vacuous-pass. Runs without git
    (no-repo mode) so the guard is unconditional, and the suite is instrumented with
    an invocation counter to assert it was ACTUALLY executed (not skipped). (Added
    after a probe raised the concern, which reproduced as a clean non-COMPLETED — the
    gate is sound; this locks that in.)"""
    work = tmp_path / "work"
    work.mkdir()
    counter = work / "runs.txt"
    # a "suite" that records each invocation, then always exits 2 (broken /
    # collection-error-like) — so round 1 already fails and never recovers.
    (work / "check.py").write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "p = Path(__file__).parent / 'runs.txt'\n"
        "p.write_text(str((int(p.read_text()) + 1) if p.exists() else 1))\n"
        "sys.exit(2)\n"
    )
    factory = Factory([ScriptedBackend(edits={"a.py": f"x = {i}\n"}) for i in range(6)])
    outcome = asyncio.run(
        run_loop(
            "build a thing",
            _settings(tmp_path),
            cwd=work,
            backend_factory=factory,
            test_command=f"{sys.executable} check.py",
            max_rounds=6,
        )
    )
    # the failing suite was actually invoked (the guard is not vacuous itself)
    assert counter.exists() and int(counter.read_text()) >= 1
    # the essential guard: broken-from-baseline code is NOT declared done
    assert outcome.code is not TerminalCode.COMPLETED
    # it terminates via a real non-success stop, not a silent pass
    assert outcome.code in {
        TerminalCode.LOOP_DETECTED,
        TerminalCode.ROUND_CAP,
        TerminalCode.NO_TEST_PROGRESS,
    }


@needs_git
def test_same_failures_are_not_no_progress(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(
        repo,
        files={
            "check.py": (
                "import sys\n"
                "from pathlib import Path\n"
                "n = int(Path('count.txt').read_text().strip())\n"
                "if n < 3:\n"
                "    print('FAILED test_a')\n"
                "    sys.exit(1)\n"
            ),
            "count.txt": "0",
        },
    )
    factory = Factory([ScriptedBackend(edits={"count.txt": str(i)}) for i in (1, 2, 3)])
    reviewer = ScriptedReviewer(["VERDICT: APPROVE"])
    outcome = asyncio.run(
        run_loop(
            "task",
            _settings(tmp_path),
            cwd=repo,
            backend_factory=factory,
            test_command=f"{sys.executable} check.py",
            reviewer=reviewer,
            max_rounds=3,
        )
    )
    assert outcome.code is TerminalCode.COMPLETED
    assert outcome.rounds == 3
    # reviewer is only consulted once tests pass
    assert len(reviewer.calls) == 1


# --- M0 regression: F2 (committed out-of-scope edits) + F3 (rename collapse) ---


@needs_git
def test_scope_violation_committed_by_backend_caught(tmp_path: Path) -> None:
    """F2: a backend that COMMITS out-of-scope work (aider auto-commit style)
    leaves a clean ``git status``; the scope re-check must still catch it via
    the pre_sha..working-tree diff."""

    class CommittingBackend(ScriptedBackend):
        async def run(self, task: str, ctx: object) -> RunOutcome:
            outcome = await super().run(task, ctx)
            root = Path(ctx.cwd)  # type: ignore[attr-defined]
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
                "backend auto-commit",
            )
            return outcome

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "src").mkdir()
    bus = EventBus()
    settings = _settings(tmp_path, scope=("src",))
    factory = Factory([CommittingBackend(edits={"secrets.py": "TOKEN = 'x'\n"})])
    outcome = asyncio.run(run_loop("task", settings, cwd=repo, backend_factory=factory, bus=bus))
    assert outcome.code is TerminalCode.OUT_OF_SCOPE
    events = _gate_events(bus, "scope_recheck")
    assert events and events[0]["allowed"] is False
    assert "secrets.py" in events[0]["violations"]


@needs_git
def test_changed_paths_rename_reports_source_and_dest(tmp_path: Path) -> None:
    """F3: rename detection is off for gate decisions — ``git mv`` must
    surface BOTH the source and the destination path."""
    from pxx.loop import _changed_paths

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, files={"secrets.py": "x = 1\n"})
    (repo / "src").mkdir()
    _git(repo, "mv", "secrets.py", "src/moved.py")
    changed = asyncio.run(_changed_paths(repo))
    assert "secrets.py" in changed
    assert "src/moved.py" in changed


# --- B2.1/B2.2/B2.3: specific codes, contributing codes, fat fields, STALE ------


@needs_git
def test_test_run_failed_on_spawn_error(tmp_path: Path) -> None:
    """A test command that cannot even spawn yields TEST_RUN_FAILED (not a
    regression, not a generic gate failure)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    factory = Factory([ScriptedBackend(edits={"a.py": "x\n"})])
    outcome = asyncio.run(
        run_loop(
            "task",
            _settings(tmp_path),
            cwd=repo,
            backend_factory=factory,
            test_command="/nonexistent/definitely-not-a-command-xyz",
        )
    )
    assert outcome.code is TerminalCode.TEST_RUN_FAILED


@needs_git
def test_sandboxed_test_gate_fails_closed_without_a_sandboxer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2.5.5+ps2: ``sandbox_shell`` confines the loop's OWN test run. Asked for
    and absent, the suite is not run and the round ends TEST_RUN_FAILED — a
    test command that would have passed on the host must not be reported green."""
    monkeypatch.setattr("pxx.loop.sandbox_argv", lambda *a, **k: None)
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    outcome = asyncio.run(
        run_loop(
            "task",
            _settings(tmp_path, sandbox_shell=True),
            cwd=repo,
            backend_factory=Factory([ScriptedBackend(edits={"a.py": "x\n"})]),
            test_command="true",
        )
    )
    assert outcome.code is TerminalCode.TEST_RUN_FAILED
    assert "sandbox-unavailable" in outcome.summary


@needs_git
def test_tests_gate_event_says_whether_it_was_sandboxed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    bus = EventBus()
    asyncio.run(
        run_loop(
            "task",
            _settings(tmp_path),
            cwd=repo,
            backend_factory=Factory([ScriptedBackend(edits={"a.py": "x = 1\n"})]),
            test_command="true",
            reviewer=ScriptedReviewer(["VERDICT: APPROVE"]),
            bus=bus,
        )
    )
    tests = _gate_events(bus, "tests")
    assert tests and tests[0]["sandboxed"] is False


def _loop_run_dirs(tmp_path: Path) -> list[Path]:
    runs = tmp_path / "state" / "runs"
    return sorted(d for d in runs.iterdir() if "-loop-" in d.name) if runs.is_dir() else []


@needs_git
def test_loop_writes_its_own_run_record_with_the_tests_gate(tmp_path: Path) -> None:
    """2.5.5+ps3: the loop's gates were emitted on the parent bus and persisted
    nowhere; a reader of runs/ saw COMPLETED sessions and no test gate for a
    loop that ended TEST_REGRESSION. The loop now has its own runs/<id>/."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    outcome = asyncio.run(
        run_loop(
            "task",
            _settings(tmp_path),
            cwd=repo,
            backend_factory=Factory([ScriptedBackend(edits={"a.py": "x = 1\n"})]),
            test_command="true",
            reviewer=ScriptedReviewer(["VERDICT: APPROVE"]),
        )
    )
    assert outcome.code is TerminalCode.COMPLETED
    dirs = _loop_run_dirs(tmp_path)
    assert len(dirs) == 1, dirs
    d = dirs[0]
    task = json.loads((d / "task.json").read_text())
    assert task["mode"] == "loop" and task["test_command"] == "true"
    events = [json.loads(line) for line in (d / "events.jsonl").read_text().splitlines()]
    gates = [e for e in events if e["kind"] == "gate_decision" and e["data"].get("gate") == "tests"]
    assert gates and gates[-1]["data"]["passed"] is True
    rec = json.loads((d / "outcome.json").read_text())
    assert rec["mode"] == "loop" and rec["code"] == "COMPLETED"
    assert rec["terminal_failures"] == 0 and rec["test_command"] == "true"
    assert (d / "diff.patch").read_text().startswith("diff --git")


@needs_git
def test_loop_record_keeps_the_diff_a_restore_discards(tmp_path: Path) -> None:
    """A failing loop resets the tree; its diff and outcome are on disk first."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, files={"a.py": "x = 0\n"})
    outcome = asyncio.run(
        run_loop(
            "task",
            _settings(tmp_path),
            cwd=repo,
            backend_factory=Factory([ScriptedBackend(edits={"a.py": "x = 1\n"})]),
            test_command="false",
            max_rounds=1,
        )
    )
    assert outcome.code is not TerminalCode.COMPLETED
    assert (repo / "a.py").read_text() == "x = 0\n", "the net should have restored the tree"
    d = _loop_run_dirs(tmp_path)[0]
    assert "+x = 1" in (d / "diff.patch").read_text()
    rec = json.loads((d / "outcome.json").read_text())
    assert rec["code"] == str(outcome.code) and rec["terminal_failures"] >= 1


def test_loop_record_and_tests_gate_without_a_git_repository(tmp_path: Path) -> None:
    """No repository at all: the loop still runs the test gate, still writes
    its own record, and the gate event still says whether it was sandboxed."""
    work = tmp_path / "plain"
    work.mkdir()
    bus = EventBus()
    outcome = asyncio.run(
        run_loop(
            "task",
            _settings(tmp_path),
            cwd=work,
            backend_factory=Factory([ScriptedBackend(edits={"a.py": "x = 1\n"})]),
            test_command="true",
            reviewer=ScriptedReviewer(["VERDICT: APPROVE"]),
            bus=bus,
        )
    )
    assert outcome.code is TerminalCode.COMPLETED
    gates = _gate_events(bus, "tests")
    assert gates and gates[-1]["passed"] is True and gates[-1]["sandboxed"] is False
    d = _loop_run_dirs(tmp_path)[0]
    rec = json.loads((d / "outcome.json").read_text())
    assert rec["mode"] == "loop" and rec["code"] == "COMPLETED"
    events = [json.loads(line) for line in (d / "events.jsonl").read_text().splitlines()]
    assert any(e["kind"] == "gate_decision" and e["data"].get("gate") == "tests" for e in events)


def test_sandboxed_gate_without_a_repository_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("pxx.loop.sandbox_argv", lambda *a, **k: None)
    work = tmp_path / "plain"
    work.mkdir()
    outcome = asyncio.run(
        run_loop(
            "task",
            _settings(tmp_path, sandbox_shell=True),
            cwd=work,
            backend_factory=Factory([ScriptedBackend(edits={"a.py": "x\n"})]),
            test_command="true",
        )
    )
    assert outcome.code is TerminalCode.TEST_RUN_FAILED


@needs_git
def test_loop_record_captures_the_safety_net_gate(tmp_path: Path) -> None:
    """The net is tied before the first round; its gate event must be in the
    loop's own record, which means the writer subscribes before it fires."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    asyncio.run(
        run_loop(
            "task",
            _settings(tmp_path),
            cwd=repo,
            backend_factory=Factory([ScriptedBackend(edits={"a.py": "x = 1\n"})]),
            test_command="true",
            reviewer=ScriptedReviewer(["VERDICT: APPROVE"]),
        )
    )
    d = _loop_run_dirs(tmp_path)[0]
    events = [json.loads(line) for line in (d / "events.jsonl").read_text().splitlines()]
    nets = [
        e for e in events if e["kind"] == "gate_decision" and e["data"].get("gate") == "safety_net"
    ]
    assert nets and nets[0]["data"].get("tag", "").startswith("pxx-pre/")
    task = json.loads((d / "task.json").read_text())
    assert task["safety_net"]["tag"] == nets[0]["data"]["tag"]


@needs_git
def test_loop_record_diff_skips_generated_artifacts_and_carries_binaries(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, files={"a.py": "x = 0\n"})
    outcome = asyncio.run(
        run_loop(
            "task",
            _settings(tmp_path),
            cwd=repo,
            backend_factory=Factory(
                [
                    ScriptedBackend(
                        edits={
                            "a.py": "x = 1\n",
                            "__pycache__/a.cpython-312.pyc": "junk",
                            "blob.bin": "\x00\x01\x02\xff",
                        }
                    )
                ]
            ),
            test_command="false",
            max_rounds=1,
        )
    )
    assert outcome.code is not TerminalCode.COMPLETED
    patch = (_loop_run_dirs(tmp_path)[0] / "diff.patch").read_text(errors="replace")
    assert "+x = 1" in patch
    assert "__pycache__" not in patch
    assert "blob.bin" in patch and "GIT binary patch" in patch


@needs_git
def test_lint_blocked_when_lint_command_fails(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "check.py").write_text("print('tests pass')\n")
    factory = Factory([ScriptedBackend(edits={"a.py": "x\n"})])
    bus = EventBus()
    outcome = asyncio.run(
        run_loop(
            "task",
            _settings(tmp_path, safety_net=False),  # net would stash the check.py fixture
            cwd=repo,
            backend_factory=factory,
            test_command=f"{sys.executable} check.py",
            lint_command=f'{sys.executable} -c "import sys; sys.exit(3)"',
            bus=bus,
        )
    )
    assert outcome.code is TerminalCode.LINT_BLOCKED
    assert outcome.lint_errors == 1
    assert _gate_events(bus, "lint")[0]["allowed"] is False


@needs_git
def test_review_empty_gets_specific_code(tmp_path: Path) -> None:
    """A review whose findings are ALL generic (no evidence anchors) yields
    REVIEW_EMPTY — distinct from unparseable output."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    reviewer = ScriptedReviewer(["VERDICT: REVISE\nF-001 [high] a.py improve error handling"])
    outcome = asyncio.run(
        run_loop(
            "task",
            _settings(tmp_path),
            cwd=repo,
            backend_factory=Factory([ScriptedBackend(edits={"a.py": "x\n"})]),
            reviewer=reviewer,
        )
    )
    assert outcome.code is TerminalCode.REVIEW_EMPTY


@needs_git
def test_review_unparseable_gets_specific_code(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    reviewer = ScriptedReviewer(["the code looks fine to me, ship it!"])
    outcome = asyncio.run(
        run_loop(
            "task",
            _settings(tmp_path),
            cwd=repo,
            backend_factory=Factory([ScriptedBackend(edits={"a.py": "x\n"})]),
            reviewer=reviewer,
        )
    )
    assert outcome.code is TerminalCode.REVIEW_UNPARSEABLE
    assert outcome.unparseable_review_count == 1


@needs_git
def test_round_cap_carries_contributing_codes(tmp_path: Path) -> None:
    """One terminal code + contributing codes: a loop stuck on failing tests
    caps at ROUND_CAP and records NO_TEST_PROGRESS as contributing."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(
        repo,
        files={
            "check.py": "print('FAILED test_always')\nimport sys; sys.exit(1)\n",
        },
    )
    factory = Factory([ScriptedBackend(edits={"a.py": "x\n"})])
    outcome = asyncio.run(
        run_loop(
            "task",
            _settings(tmp_path),
            cwd=repo,
            backend_factory=factory,
            test_command=f"{sys.executable} check.py",
            max_rounds=2,
        )
    )
    assert outcome.code is TerminalCode.ROUND_CAP
    assert "NO_TEST_PROGRESS" in outcome.contributing_codes


@needs_git
def test_revise_loop_records_review_rejected_contributing(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    reviewer = ScriptedReviewer(["VERDICT: REVISE\nF-001 [high] a.py:1 fix this"] * 5)
    factory = Factory([ScriptedBackend(edits={"a.py": "x\n"})])
    outcome = asyncio.run(
        run_loop(
            "task",
            _settings(tmp_path),
            cwd=repo,
            backend_factory=factory,
            reviewer=reviewer,
            max_rounds=2,
        )
    )
    assert outcome.code is TerminalCode.ROUND_CAP
    assert "REVIEW_REJECTED" in outcome.contributing_codes


@needs_git
def test_fat_outcome_fields_from_real_loop(tmp_path: Path) -> None:
    """B2.2: a real loop run produces the full 12.1 field set."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, files={"check.py": "print('ok')\n"})
    factory = Factory([ScriptedBackend(edits={"a.py": "x = 1\n"})])
    outcome = asyncio.run(
        run_loop(
            "task",
            _settings(tmp_path),
            cwd=repo,
            backend_factory=factory,
            test_command=f"{sys.executable} check.py",
        )
    )
    assert outcome.code is TerminalCode.COMPLETED
    assert outcome.accepted is True
    assert outcome.edit_seconds > 0
    assert outcome.test_seconds > 0
    assert outcome.files_changed == 1
    assert outcome.baseline_failures == 0
    assert outcome.terminal_failures == 0
    assert outcome.cost_usd is None  # unpriced local run, never fabricated


def test_review_packet_staleness() -> None:
    from pxx.review import ReviewPacket

    packet = ReviewPacket(
        task="t", base_sha="a" * 40, head_sha="b" * 40, verdict="APPROVE", findings=()
    )
    assert not packet.is_stale("b" * 40)
    assert packet.is_stale("c" * 40)


@needs_git
def test_stale_review_forces_rereview(tmp_path: Path) -> None:
    """B2.3: an APPROVE bound to commit X must NOT approve HEAD Y — the loop
    re-reviews against the moved tree."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    class HeadAdvancingReviewer(ScriptedReviewer):
        async def review(self, diff: str, task: str) -> str:
            call = await super().review(diff, task)
            if len(self.calls) == 1:
                # advance HEAD between the diff and the verdict
                (repo / "later.py").write_text("y = 2\n")
                _git(repo, "add", "-A")
                _git(
                    repo,
                    "-c",
                    "user.name=test",
                    "-c",
                    "user.email=test@example.com",
                    "commit",
                    "-q",
                    "-m",
                    "advance head mid-review",
                )
            return call

    reviewer = HeadAdvancingReviewer(["VERDICT: APPROVE", "VERDICT: APPROVE"])
    bus = EventBus()
    outcome = asyncio.run(
        run_loop(
            "task",
            _settings(tmp_path),
            cwd=repo,
            backend_factory=Factory([ScriptedBackend(edits={"a.py": "x\n"})]),
            reviewer=reviewer,
            bus=bus,
        )
    )
    assert len(reviewer.calls) == 2  # the stale verdict forced a re-review
    assert _gate_events(bus, "review_stale")
    assert outcome.code is TerminalCode.COMPLETED  # second APPROVE binds fresh


@needs_git
def test_perpetually_moving_head_fails_closed(tmp_path: Path) -> None:
    """If the tree never stops moving, the review cannot bind — fail closed."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    counter = {"n": 0}

    class AlwaysAdvancingReviewer(ScriptedReviewer):
        async def review(self, diff: str, task: str) -> str:
            result = await super().review(diff, task)
            counter["n"] += 1
            (repo / f"later{counter['n']}.py").write_text("y = 2\n")
            _git(repo, "add", "-A")
            _git(
                repo,
                "-c",
                "user.name=test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-q",
                "-m",
                f"advance {counter['n']}",
            )
            return result

    reviewer = AlwaysAdvancingReviewer(["VERDICT: APPROVE", "VERDICT: APPROVE"])
    outcome = asyncio.run(
        run_loop(
            "task",
            _settings(tmp_path),
            cwd=repo,
            backend_factory=Factory([ScriptedBackend(edits={"a.py": "x\n"})]),
            reviewer=reviewer,
        )
    )
    assert outcome.code is TerminalCode.REVIEW_UNAVAILABLE


# --- B4.2: semantic loop detection + recovery ladder -------------------------------


@needs_git
def test_oscillating_review_loop_detected(tmp_path: Path) -> None:
    """A loop that produces the SAME diff and gets the SAME finding every
    round is stopped by the recovery ladder, not the blunt round cap."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    # backend never changes anything after round 1; reviewer repeats itself
    reviewer = ScriptedReviewer(["VERDICT: REVISE\nF-001 [high] a.py:1 fix this"] * 6)
    factory = Factory([ScriptedBackend(edits={"a.py": "x = 1\n"})] + [ScriptedBackend()] * 5)
    bus = EventBus()
    outcome = asyncio.run(
        run_loop(
            "task",
            _settings(tmp_path),
            cwd=repo,
            backend_factory=factory,
            reviewer=reviewer,
            max_rounds=6,
            bus=bus,
        )
    )
    assert outcome.code is TerminalCode.LOOP_DETECTED
    assert outcome.rounds < 6  # the ladder fired before the cap


@needs_git
def test_stagnant_test_loop_detected(tmp_path: Path) -> None:
    """Same failing set + same diff every round → LOOP_DETECTED."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(
        repo,
        files={"check.py": "print('FAILED test_x')\nimport sys; sys.exit(1)\n"},
    )
    factory = Factory([ScriptedBackend(edits={"a.py": "x = 1\n"})] + [ScriptedBackend()] * 4)
    outcome = asyncio.run(
        run_loop(
            "task",
            _settings(tmp_path),
            cwd=repo,
            backend_factory=factory,
            test_command=f"{sys.executable} check.py",
            max_rounds=5,
        )
    )
    assert outcome.code is TerminalCode.LOOP_DETECTED


@needs_git
def test_replan_prompt_injected_after_first_stagnation(tmp_path: Path) -> None:
    """Ladder step 1: the round after a stagnation gets the re-plan prefix."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, files={"a.py": "x = 0\n"})  # tracked: edits show in the diff
    reviewer = ScriptedReviewer(
        ["VERDICT: REVISE\nF-001 [high] a.py:1 fix this"] * 3 + ["VERDICT: APPROVE"]
    )
    factory = Factory(
        [ScriptedBackend(edits={"a.py": "x = 1\n"})]
        + [ScriptedBackend()]
        + [ScriptedBackend(edits={"a.py": "x = 2\n"})]
        + [ScriptedBackend()] * 2
    )
    outcome = asyncio.run(
        run_loop(
            "task",
            _settings(tmp_path),
            cwd=repo,
            backend_factory=factory,
            reviewer=reviewer,
            max_rounds=5,
        )
    )
    assert outcome.code is TerminalCode.COMPLETED  # recovered, not detected
    # round 2 was the first stagnation -> round 3's prompt got the re-plan prefix
    assert factory.made[2].tasks[0].startswith("You made NO measurable progress")


@needs_git
def test_healthy_healing_loop_not_flagged(tmp_path: Path) -> None:
    """A loop whose diff CHANGES every round is never loop-detected."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    reviewer = ScriptedReviewer(
        ["VERDICT: REVISE\nF-001 [high] a.py:1 fix this", "VERDICT: APPROVE"]
    )
    factory = Factory(
        [
            ScriptedBackend(edits={"a.py": "x = 1\n"}),
            ScriptedBackend(edits={"a.py": "x = 2\n"}),
        ]
    )
    outcome = asyncio.run(
        run_loop(
            "task",
            _settings(tmp_path),
            cwd=repo,
            backend_factory=factory,
            reviewer=reviewer,
        )
    )
    assert outcome.code is TerminalCode.COMPLETED


# --- clean-termination salvage: over-work (BUDGET_EXCEEDED) with a verified edit --


def _overwork_backend(edits: dict[str, str] | None = None) -> ScriptedBackend:
    """A backend that (optionally) edits, then reports session-level over-work."""
    return ScriptedBackend(
        edits=edits if edits is not None else {"calc.py": "x = 2\n"},
        outcome=RunOutcome(code=TerminalCode.BUDGET_EXCEEDED, summary="budget exceeded", tokens=50),
    )


@needs_git
def test_overwork_salvaged_to_completed_when_tests_pass(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, files={"calc.py": "x = 1\n"})
    settings = _settings(tmp_path, test_command="true")
    outcome = asyncio.run(
        run_loop("task", settings, cwd=repo, backend_factory=Factory([_overwork_backend()]))
    )
    assert outcome.code is TerminalCode.COMPLETED
    assert "salvaged" in outcome.summary
    assert outcome.diff_lines > 0 and outcome.files_changed >= 1  # telemetry recorded


@needs_git
def test_overwork_not_salvaged_when_tests_fail(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, files={"calc.py": "x = 1\n"})
    settings = _settings(tmp_path, test_command="false")
    outcome = asyncio.run(
        run_loop("task", settings, cwd=repo, backend_factory=Factory([_overwork_backend()]))
    )
    assert outcome.code is TerminalCode.BUDGET_EXCEEDED


@needs_git
def test_overwork_not_salvaged_without_test_command(tmp_path: Path) -> None:
    # No objective signal that the over-worked edit is complete -> stands.
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, files={"calc.py": "x = 1\n"})
    outcome = asyncio.run(
        run_loop(
            "task", _settings(tmp_path), cwd=repo, backend_factory=Factory([_overwork_backend()])
        )
    )
    assert outcome.code is TerminalCode.BUDGET_EXCEEDED


@needs_git
def test_overwork_not_salvaged_without_diff(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, files={"calc.py": "x = 1\n"})
    settings = _settings(tmp_path, test_command="true")
    outcome = asyncio.run(
        run_loop("task", settings, cwd=repo, backend_factory=Factory([_overwork_backend(edits={})]))
    )
    assert outcome.code is TerminalCode.BUDGET_EXCEEDED


@needs_git
def test_overwork_not_salvaged_when_blocking_review_revises(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, files={"calc.py": "x = 1\n"})
    settings = _settings(tmp_path, test_command="true")
    reviewer = ScriptedReviewer(["VERDICT: REVISE\nF-001 [medium] calc.py:1 nope"])
    outcome = asyncio.run(
        run_loop(
            "task",
            settings,
            cwd=repo,
            backend_factory=Factory([_overwork_backend()]),
            reviewer=reviewer,
            review_mode=ReviewMode.BLOCKING,
        )
    )
    assert outcome.code is TerminalCode.BUDGET_EXCEEDED  # review blocked the salvage


@needs_git
def test_overwork_salvaged_under_advisory_review(tmp_path: Path) -> None:
    # Advisory review never blocks, so a tests-passing over-work run still salvages.
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, files={"calc.py": "x = 1\n"})
    settings = _settings(tmp_path, test_command="true")
    reviewer = ScriptedReviewer(["VERDICT: REVISE\nF-001 [low] calc.py:1 nit"])
    outcome = asyncio.run(
        run_loop(
            "task",
            settings,
            cwd=repo,
            backend_factory=Factory([_overwork_backend()]),
            reviewer=reviewer,
            review_mode=ReviewMode.ADVISORY,
        )
    )
    assert outcome.code is TerminalCode.COMPLETED


@needs_git
def test_overwork_not_salvaged_when_diff_out_of_scope(tmp_path: Path) -> None:
    # A salvage must not wave an out-of-scope edit past the scope gate.
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, files={"evil.py": "x = 1\n", "src/ok.py": "y = 1\n"})
    settings = _settings(tmp_path, scope=("src",), test_command="true")
    factory = Factory([_overwork_backend(edits={"evil.py": "x = 2\n"})])
    outcome = asyncio.run(run_loop("task", settings, cwd=repo, backend_factory=factory))
    assert outcome.code is TerminalCode.BUDGET_EXCEEDED


@needs_git
def test_overwork_not_salvaged_when_diff_over_budget(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, files={"big.txt": "old\n"})
    settings = _settings(tmp_path, test_command="true", budgets=Budgets(max_diff_lines=5))
    factory = Factory([_overwork_backend(edits={"big.txt": "line\n" * 20})])
    outcome = asyncio.run(run_loop("task", settings, cwd=repo, backend_factory=factory))
    assert outcome.code is TerminalCode.BUDGET_EXCEEDED


@needs_git
def test_overwork_not_salvaged_when_lint_fails(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, files={"calc.py": "x = 1\n"})
    settings = _settings(tmp_path, test_command="true")
    factory = Factory([_overwork_backend()])
    outcome = asyncio.run(
        run_loop("task", settings, cwd=repo, backend_factory=factory, lint_command="false")
    )
    assert outcome.code is TerminalCode.BUDGET_EXCEEDED


# --- done-signal early-exit: the loop supplies the objective done oracle ------


class DoneCheckBackend(ScriptedBackend):
    """Applies edits, then consults ``ctx.done_check`` like the native backend:
    if the injected oracle says the edit is objectively done, COMPLETE early;
    otherwise report session-level over-work (BUDGET_EXCEEDED). Records what the
    loop handed it so a test can assert the oracle was (or wasn't) wired."""

    def __init__(self, edits: dict[str, str] | None = None) -> None:
        super().__init__(edits=edits if edits is not None else {"calc.py": "x = 2\n"})
        self.seen_done_check: object = "unset"
        self.oracle_result: bool | None = None

    async def run(self, task: str, ctx: object) -> RunOutcome:
        await asyncio.sleep(0)
        for rel, content in self.edits.items():
            path = Path(ctx.cwd) / rel  # type: ignore[attr-defined]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        self.seen_done_check = ctx.done_check  # type: ignore[attr-defined]
        if self.seen_done_check is not None:
            self.oracle_result = await self.seen_done_check()  # type: ignore[operator]
            if self.oracle_result:
                return RunOutcome(code=TerminalCode.COMPLETED, summary="done-signal", tokens=5)
        return RunOutcome(code=TerminalCode.BUDGET_EXCEEDED, summary="over-work", tokens=50)


@needs_git
def test_done_signal_oracle_true_when_edit_passes_tests(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, files={"calc.py": "x = 1\n"})
    settings = _settings(tmp_path, test_command="true")  # objective signal passes
    backend = DoneCheckBackend()
    outcome = asyncio.run(run_loop("task", settings, cwd=repo, backend_factory=Factory([backend])))
    assert backend.seen_done_check is not None  # loop wired the oracle
    assert backend.oracle_result is True  # real _edit_objectively_done said done
    assert outcome.code is TerminalCode.COMPLETED


@needs_git
def test_done_signal_oracle_false_when_tests_fail(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, files={"calc.py": "x = 1\n"})
    settings = _settings(tmp_path, test_command="false")  # objective signal fails
    backend = DoneCheckBackend()
    outcome = asyncio.run(run_loop("task", settings, cwd=repo, backend_factory=Factory([backend])))
    assert backend.oracle_result is False
    assert outcome.code is TerminalCode.BUDGET_EXCEEDED  # not done, over-work stands


@needs_git
def test_done_signal_disabled_leaves_oracle_unset(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, files={"calc.py": "x = 1\n"})
    settings = _settings(tmp_path, test_command="true", done_signal=False)
    backend = DoneCheckBackend()
    asyncio.run(run_loop("task", settings, cwd=repo, backend_factory=Factory([backend])))
    assert backend.seen_done_check is None  # off -> no oracle injected


@needs_git
def test_done_signal_needs_a_test_command(tmp_path: Path) -> None:
    # No test command -> no objective completion signal -> no oracle wired.
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, files={"calc.py": "x = 1\n"})
    backend = DoneCheckBackend()
    asyncio.run(run_loop("task", _settings(tmp_path), cwd=repo, backend_factory=Factory([backend])))
    assert backend.seen_done_check is None


def test_done_signal_not_wired_without_repo(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    settings = _settings(tmp_path, test_command="true")
    backend = DoneCheckBackend(edits={"out.txt": "hi\n"})
    asyncio.run(run_loop("task", settings, cwd=work, backend_factory=Factory([backend])))
    assert backend.seen_done_check is None  # no git repo -> no diff/scope basis


# --- generated-artifact filtering in _changed_paths (scope-noise) -----------


@pytest.mark.parametrize(
    "rel_path, expected",
    [
        ("__pycache__/foo.cpython-311.pyc", True),
        ("src/pkg/__pycache__/mod.pyc", True),
        ("foo.pyc", True),
        ("foo.pyo", True),
        (".pytest_cache/v/cache/lastfailed", True),
        (".mypy_cache/3.11/x.data.json", True),
        (".ruff_cache/content", True),
        ("src/foo.py", False),
        ("tests/test_foo.py", False),
        ("docs/pycache_notes.md", False),  # substring, not a path component
        ("a.pycx", False),  # not a .pyc suffix
    ],
)
def test_is_generated_artifact(rel_path: str, expected: bool) -> None:
    from pxx.loop import _is_generated_artifact

    assert _is_generated_artifact(rel_path) is expected


def test_changed_paths_filters_generated_artifacts(tmp_path: Path) -> None:
    from pxx.loop import _changed_paths

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    # A real source edit (should be reported) alongside test/interpreter
    # byproducts (should be filtered) — the repo does NOT gitignore them.
    (repo / "src").mkdir()
    (repo / "src" / "foo.py").write_text("x = 1\n")
    (repo / "src" / "__pycache__").mkdir()
    (repo / "src" / "__pycache__" / "foo.pyc").write_bytes(b"\x00cached")
    (repo / ".pytest_cache").mkdir()
    (repo / ".pytest_cache" / "lastfailed").write_text("{}")

    changed = asyncio.run(_changed_paths(repo))
    assert changed == ["src/foo.py"]


def test_a_forwarded_session_abort_reports_loop_rounds_and_tokens(tmp_path):
    # Codex review, 68f5882: a session abort is forwarded as the loop's
    # outcome; it must count OUTER rounds and the loop's accumulated tokens,
    # not the session's inner model rounds (which can exceed max_rounds).
    aborting = ScriptedBackend(
        outcome=RunOutcome(code=TerminalCode.HOOKS_MISSING, summary="x", rounds=5, tokens=50)
    )
    outcome = asyncio.run(
        run_loop(
            "task",
            _settings(tmp_path, budgets=Budgets(max_rounds=3)),
            cwd=tmp_path,
            backend_factory=Factory([aborting]),
            bus=EventBus(),
        )
    )
    assert outcome.code is TerminalCode.HOOKS_MISSING
    assert (outcome.rounds, outcome.tokens) == (1, 50)


def test_a_later_round_abort_reports_the_accumulated_tokens(tmp_path):
    first = ScriptedBackend(edits={"a.txt": "a\n"})  # COMPLETED, tokens=10
    aborting = ScriptedBackend(
        outcome=RunOutcome(code=TerminalCode.HOOKS_MISSING, summary="x", rounds=4, tokens=7)
    )
    outcome = asyncio.run(
        run_loop(
            "task",
            _settings(tmp_path, test_command="false", budgets=Budgets(max_rounds=3)),
            cwd=tmp_path,
            backend_factory=Factory([first, aborting]),
            bus=EventBus(),
        )
    )
    assert outcome.code is TerminalCode.HOOKS_MISSING
    assert (outcome.rounds, outcome.tokens) == (2, 17)


@needs_git
def test_a_file_the_round_created_reaches_review_and_the_diff_budget(tmp_path: Path) -> None:
    # Codex review (2.9.1): `git diff <pre_sha>` never shows a created file, so
    # the review and the diff budget missed new files for every backend that
    # leaves work uncommitted. A pre-existing untracked file is not the run's.
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, {"a.py": "a = 1\n"})
    (repo / "notes.txt").write_text("the operator's, untracked before the loop\n")
    reviewer = ScriptedReviewer(["VERDICT: APPROVE"])
    created = "".join(f"line{i} = {i}\n" for i in range(6))
    outcome = asyncio.run(
        run_loop(
            "add new.py",
            _settings(tmp_path),
            cwd=repo,
            backend_factory=Factory([ScriptedBackend(edits={"new.py": created})]),
            reviewer=reviewer,
        )
    )
    assert outcome.code is TerminalCode.COMPLETED
    reviewed = reviewer.calls[0][0]
    assert "new.py" in reviewed and "+line5 = 5" in reviewed
    assert "notes.txt" not in reviewed  # untracked at loop start: not the run's work
    assert outcome.diff_lines == 6  # the created file's lines count toward the budget


@needs_git
def test_a_force_added_ignored_file_outside_scope_is_caught(tmp_path: Path) -> None:
    # Grok review (2.9.1): a run that `git add -f`s a gitignored file outside
    # the scope, beside an in-scope edit, must not slip past the loop's scope
    # re-check. aider no longer commits, so the file stays staged -- and
    # staged paths are part of `git diff <pre_sha>`.
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, {"src/ok.py": "a = 1\n", ".gitignore": ".secret\n"})

    class ForceAdding(ScriptedBackend):
        async def run(self, task, ctx):
            (Path(ctx.cwd) / "src" / "ok.py").write_text("a = 2\n")
            (Path(ctx.cwd) / ".secret").write_text("SECRET_TOKEN=abc\n")
            _git(Path(ctx.cwd), "add", "-f", ".secret")
            return RunOutcome(code=TerminalCode.COMPLETED, summary="ok", tokens=1)

    outcome = asyncio.run(
        run_loop(
            "edit",
            _settings(tmp_path, scope=("src",)),
            cwd=repo,
            backend_factory=Factory([ForceAdding()]),
        )
    )
    assert outcome.code is TerminalCode.OUT_OF_SCOPE
    assert ".secret" in outcome.summary


@needs_git
def test_a_failed_created_file_listing_is_visible_not_silent(tmp_path: Path, monkeypatch) -> None:
    # Codex review (2.9.1 r2): a listing failure read as "no files created".
    import pxx.worktree as wt

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, {"a.py": "a = 1\n"})
    real = wt.untracked_paths
    calls = {"n": 0}

    async def flaky(cwd):
        calls["n"] += 1
        return await real(cwd) if calls["n"] == 1 else None  # baseline ok, then git fails

    monkeypatch.setattr(wt, "untracked_paths", flaky)
    reviewer = ScriptedReviewer(["VERDICT: APPROVE"])
    outcome = asyncio.run(
        run_loop(
            "add new.py",
            _settings(tmp_path),
            cwd=repo,
            backend_factory=Factory([ScriptedBackend(edits={"new.py": "n = 1\n"})]),
            reviewer=reviewer,
        )
    )
    # Codex review (2.9.1 r3): visible is not enough -- an approving reviewer
    # must not be able to pass an incomplete diff. Blocked before review.
    assert outcome.code is TerminalCode.REVIEW_UNAVAILABLE
    assert "diff is incomplete" in outcome.summary
    assert reviewer.calls == []


@needs_git
def test_a_recreated_preexisting_untracked_file_reaches_review(tmp_path: Path) -> None:
    # agy review (2.9.1 r2): a path untracked before the loop (then stashed by
    # the net) and re-created by a round was invisible to review and budget.
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, {"a.py": "a = 1\n"})
    (repo / "new.py").write_text("old = 0\n")
    reviewer = ScriptedReviewer(["VERDICT: APPROVE"])
    asyncio.run(
        run_loop(
            "write new.py",
            _settings(tmp_path),
            cwd=repo,
            backend_factory=Factory([ScriptedBackend(edits={"new.py": "fresh = 1\n"})]),
            reviewer=reviewer,
        )
    )
    assert "+fresh = 1" in reviewer.calls[0][0]


@needs_git
def test_an_incomplete_diff_is_never_salvaged_after_a_budget_overrun(
    tmp_path: Path, monkeypatch
) -> None:
    # Codex review (2.9.1 r4): budget-overrun salvage bypassed the
    # completeness block -- tests green + an approving reviewer => COMPLETED.
    import pxx.worktree as wt

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, {"a.py": "a = 1\n"})
    real = wt.untracked_paths
    calls = {"n": 0}

    async def flaky(cwd):
        calls["n"] += 1
        return await real(cwd) if calls["n"] == 1 else None

    monkeypatch.setattr(wt, "untracked_paths", flaky)
    overrun = ScriptedBackend(
        edits={"new.py": "n = 1\n"},
        outcome=RunOutcome(code=TerminalCode.BUDGET_EXCEEDED, summary="over", tokens=5),
    )
    reviewer = ScriptedReviewer(["VERDICT: APPROVE"])
    outcome = asyncio.run(
        run_loop(
            "add new.py",
            _settings(tmp_path, test_command="true"),
            cwd=repo,
            backend_factory=Factory([overrun]),
            reviewer=reviewer,
        )
    )
    assert outcome.code is not TerminalCode.COMPLETED
    assert reviewer.calls == []


@needs_git
def test_a_scope_recheck_that_cannot_list_changes_blocks(tmp_path: Path, monkeypatch) -> None:
    # Grok review (2.9.1 r4): a failed `git status` read as "no changes", so
    # the scope re-check saw nothing out of scope.
    import pxx.loop as loop_mod

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, {"a.py": "a = 1\n"})
    state = {"ran": False}

    class Editing(ScriptedBackend):
        async def run(self, task, ctx):
            outcome = await super().run(task, ctx)
            state["ran"] = True
            return outcome

    real_git = loop_mod._git

    async def git_after_round(root, *args, **kw):
        if state["ran"] and args and args[0] == "status":
            return None  # git fails once the round has run
        return await real_git(root, *args, **kw)

    monkeypatch.setattr(loop_mod, "_git", git_after_round)
    outcome = asyncio.run(
        run_loop(
            "edit",
            _settings(tmp_path),
            cwd=repo,
            backend_factory=Factory([Editing(edits={"a.py": "a = 2\n"})]),
        )
    )
    assert outcome.code is TerminalCode.REVIEW_UNAVAILABLE
    assert "scope cannot be verified" in outcome.summary


@needs_git
def test_the_recorded_patch_says_when_it_is_incomplete(tmp_path: Path, monkeypatch) -> None:
    # Grok review (2.9.1 r4, P2): a listing failure wrote a patch that looked
    # complete.
    import pxx.loop as loop_mod
    import pxx.worktree as wt

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, {"a.py": "a = 1\n"})

    async def failing(cwd):
        return None

    monkeypatch.setattr(wt, "untracked_paths", failing)
    patch = asyncio.run(loop_mod._record_diff(repo, None, {}))
    assert loop_mod._INCOMPLETE_DIFF_MARK in patch.splitlines()


@needs_git
@pytest.mark.parametrize("overrun", [False, True], ids=["main-path", "budget-salvage"])
def test_a_created_file_whose_patch_cannot_be_read_blocks_like_a_failed_listing(
    tmp_path: Path, monkeypatch, overrun: bool
) -> None:
    # Codex review (2.9.1 r5): a failed new_file_patch became a comment the
    # completeness gate did not recognise -> COMPLETED with zero diff lines.
    import pxx.worktree as wt

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, {"a.py": "a = 1\n"})

    async def unreadable(cwd, rel, *, binary=False):
        return ""

    monkeypatch.setattr(wt, "new_file_patch", unreadable)
    backend = ScriptedBackend(
        edits={"new.py": "n = 1\n"},
        outcome=RunOutcome(code=TerminalCode.BUDGET_EXCEEDED, summary="over", tokens=5)
        if overrun
        else None,
    )
    reviewer = ScriptedReviewer(["VERDICT: APPROVE"])
    outcome = asyncio.run(
        run_loop(
            "add new.py",
            _settings(tmp_path, test_command="true"),
            cwd=repo,
            backend_factory=Factory([backend]),
            reviewer=reviewer,
        )
    )
    assert outcome.code is not TerminalCode.COMPLETED
    assert reviewer.calls == []


@needs_git
def test_a_recreated_untracked_file_with_identical_bytes_reaches_review(tmp_path: Path) -> None:
    # Codex review (2.9.1 r5): identical bytes isolate the baseline ORDER.
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, {"a.py": "a = 1\n"})
    for key, val in (("user.name", "t"), ("user.email", "t@e.c")):  # so the stash runs
        subprocess.run(["git", "config", key, val], cwd=repo, check=True)
    (repo / "new.py").write_text("same = 1\n")
    reviewer = ScriptedReviewer(["VERDICT: APPROVE"])
    outcome = asyncio.run(
        run_loop(
            "write new.py",
            _settings(tmp_path),
            cwd=repo,
            backend_factory=Factory([ScriptedBackend(edits={"new.py": "same = 1\n"})]),
            reviewer=reviewer,
        )
    )
    assert "+stash" in outcome.summary  # the net really stashed the file
    assert "+same = 1" in reviewer.calls[0][0]


@needs_git
def test_changed_paths_see_flagged_edits_and_whitespace_names(tmp_path: Path) -> None:
    # Grok review (2.9.1 r5): index flags hid tracked edits from the scope
    # re-check, and a name that strip() empties was dropped.
    import pxx.loop as loop_mod

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, {"a.py": "a = 1\n", "b.py": "b = 1\n"})
    pre = subprocess.run(
        [GIT, "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    _git(repo, "update-index", "--assume-unchanged", "a.py")
    from pxx.worktree import flagged_state

    flagged_start = asyncio.run(flagged_state(repo))  # the loop's baseline moment
    (repo / "a.py").write_text("a = 'hidden'\n")
    (repo / " ").write_text("space\n")
    _git(repo, "add", " ")
    changed = asyncio.run(loop_mod._changed_paths_checked(repo, pre, flagged_start))
    assert "a.py" in changed and " " in changed


class _PlantsGitState(ScriptedBackend):
    """Edits a file in scope, then changes git's view of the tree the way a
    shell command could: a replace ref over HEAD, or a textconv driver."""

    def __init__(self, how: str) -> None:
        super().__init__(edits={"a.py": "a = 2\n"})
        self.how = how

    async def run(self, task: str, ctx: object) -> RunOutcome:
        outcome = await super().run(task, ctx)
        await asyncio.to_thread(self._plant, Path(ctx.cwd))  # type: ignore[attr-defined]
        return outcome

    def _plant(self, root: Path) -> None:
        if self.how == "replace":
            (root / "out.py").write_text("o = 'hidden'\n")
            _git(root, "add", "out.py")
            tree = subprocess.run(
                [GIT, "write-tree"], cwd=root, check=True, capture_output=True, text=True
            ).stdout.strip()
            head = subprocess.run(
                [GIT, "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
            ).stdout.strip()
            fake = subprocess.run(
                [
                    GIT,
                    "-c",
                    "user.name=t",
                    "-c",
                    "user.email=t@e.c",
                    "commit-tree",
                    tree,
                    "-m",
                    "f",
                ],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            _git(root, "replace", head, fake)  # `git diff HEAD` now shows nothing for out.py
        elif self.how == "binary":  # a plain file write -- what write_file can do
            (root / ".gitattributes").write_text("* -diff\n")
        elif self.how == "binary-hidden":  # ignored + case-variant (Grok r9/r10)
            (root / "sub").mkdir(exist_ok=True)
            (root / "sub" / ".gitignore").write_text(".gitattributes\n")
            (root / "sub" / ".GitAttributes").write_text("* -diff\n")
        else:
            _git(root, "config", "diff.hide.textconv", "true")
            (root / ".git" / "info").mkdir(exist_ok=True)
            (root / ".git" / "info" / "attributes").write_text("a.py diff=hide\n")


@needs_git
@pytest.mark.parametrize("how", ["replace", "textconv", "binary", "binary-hidden"])
def test_a_round_that_changes_git_state_is_never_approved(tmp_path: Path, how: str) -> None:
    # Grok review (2.9.1 r7, P0): `git replace` blinded the loop's path list
    # and diff, so an out-of-scope tracked edit read as "nothing changed".
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, {"a.py": "a = 1\n", "out.py": "o = 1\n"})
    reviewer = ScriptedReviewer(["VERDICT: APPROVE"])
    bus = EventBus()
    # The attributes file is IN scope (Codex r9): only the incomplete-diff
    # guard can stop that round, not the scope re-check.
    scope = {"binary": ("a.py", ".gitattributes"), "binary-hidden": ("a.py", "sub")}.get(
        how, ("a.py",)
    )
    outcome = asyncio.run(
        run_loop(
            "edit a.py",
            _settings(tmp_path, scope=scope),
            cwd=repo,
            backend_factory=Factory([_PlantsGitState(how)]),
            reviewer=reviewer,
            bus=bus,
        )
    )
    if how.startswith("binary"):
        assert outcome.code is TerminalCode.REVIEW_UNAVAILABLE
        assert _gate_events(bus, "diff_complete")[0]["allowed"] is False
    assert outcome.code in (TerminalCode.REVIEW_UNAVAILABLE, TerminalCode.OUT_OF_SCOPE)
    assert reviewer.calls == []
    # the restore refuses too: git's view of the tree is not trusted
    assert "net not restored" in outcome.summary


@needs_git
@pytest.mark.parametrize("helper", ["done", "salvage"])
def test_done_and_salvage_see_an_out_of_scope_flagged_edit(tmp_path: Path, helper: str) -> None:
    # agy review (2.9.1 r7, P2): the done-check and the over-work salvage
    # re-listed changed paths without the flagged baseline, so an
    # out-of-scope edit behind an index flag passed their scope check.
    import pxx.loop as loop_mod
    from pxx.safety import ScopeGate
    from pxx.worktree import flagged_state

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, {"a.py": "a = 1\n", "out.py": "o = 1\n"})
    pre = subprocess.run(
        [GIT, "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    _git(repo, "update-index", "--skip-worktree", "out.py")
    flagged_start = asyncio.run(flagged_state(repo))
    (repo / "a.py").write_text("a = 2\n")
    (repo / "out.py").write_text("o = 'hidden'\n")
    kwargs = dict(
        root=repo,
        pre_sha=pre,
        diff="-a = 1\n+a = 2\n",
        scope=ScopeGate(repo, ("a.py",)),
        max_diff_lines=100,
        command=f"{sys.executable} -c pass",  # green: only the scope check can refuse
        lint_command=None,
        flagged_start=flagged_start,
    )
    assert asyncio.run(loop_mod._edit_objectively_done(**{**kwargs, "flagged_start": None}))
    if helper == "done":
        call = loop_mod._edit_objectively_done
    else:
        call = loop_mod._overwork_salvageable
        kwargs.update(task="t", reviewer=None, review_mode=ReviewMode.BLOCKING)
    assert asyncio.run(call(**kwargs)) is False


@needs_git
def test_an_edit_behind_an_index_flag_blocks_before_review(tmp_path: Path) -> None:
    # Codex review (2.9.1 r6): a flagged edit reached the path check but not
    # the reviewer or the budget (git diff omits it) -- COMPLETED, 0 lines.
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, {"a.py": "a = 1\n"})
    _git(repo, "update-index", "--assume-unchanged", "a.py")
    reviewer = ScriptedReviewer(["VERDICT: APPROVE"])
    outcome = asyncio.run(
        run_loop(
            "edit a.py",
            _settings(tmp_path),
            cwd=repo,
            backend_factory=Factory([ScriptedBackend(edits={"a.py": "a = 'hidden'\n"})]),
            reviewer=reviewer,
        )
    )
    assert outcome.code is TerminalCode.REVIEW_UNAVAILABLE
    assert reviewer.calls == []


@needs_git
@pytest.mark.parametrize("failure", ["tracked-diff", "listing", "patch"])
def test_the_recorded_patch_is_marked_for_every_kind_of_failure(
    tmp_path: Path, monkeypatch, failure: str
) -> None:
    # Codex review (2.9.1 r6, P2): a failed tracked diff became "" unmarked.
    import pxx.loop as loop_mod
    import pxx.worktree as wt

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, {"a.py": "a = 1\n"})
    (repo / "new.py").write_text("n = 1\n")
    if failure == "tracked-diff":
        real = loop_mod._git

        async def no_diff(root, *args, **kw):
            return None if args[:1] == ("diff",) else await real(root, *args, **kw)

        monkeypatch.setattr(loop_mod, "_git", no_diff)
    elif failure == "listing":

        async def no_listing(cwd):
            return None

        monkeypatch.setattr(wt, "untracked_paths", no_listing)
    else:

        async def no_patch(cwd, rel, *, binary=False):
            return ""

        monkeypatch.setattr(wt, "new_file_patch", no_patch)
    patch = asyncio.run(loop_mod._record_diff(repo, None, {}))
    assert loop_mod._INCOMPLETE_DIFF_MARK in patch.splitlines()


@needs_git
def test_a_loop_whose_commit_is_skipped_says_why(tmp_path: Path) -> None:
    # 2.9.3 (plan B2): the loop reports why --commit committed nothing.
    from dataclasses import replace

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, {"a.py": "x = 0\n"})
    secret = "api_key = '" + "sk-" + "live-" + "0123456789abcdef" * 2 + "'\n"
    bus = EventBus()
    outcome = asyncio.run(
        run_loop(
            "task",
            replace(_settings(tmp_path), auto_commit=True),
            cwd=repo,
            backend_factory=Factory([ScriptedBackend(edits={"a.py": secret})]),
            test_command="true",
            reviewer=ScriptedReviewer(["VERDICT: APPROVE"]),
            bus=bus,
        )
    )
    assert outcome.code is TerminalCode.COMPLETED
    assert "[not committed: secrets]" in outcome.summary
    assert [e.data for e in bus.history if e.data.get("source") == "auto_commit_skipped"] == [
        {"source": "auto_commit_skipped", "reason": "secrets"}
    ]
