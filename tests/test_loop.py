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
