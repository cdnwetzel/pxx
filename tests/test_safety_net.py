"""Tests for pxx.safety_net: tie + opt-in commit_session_work (K5 + 2.0.1-B)."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from pxx.safety_net import commit_session_work, tie_safety_net

GIT = shutil.which("git")
needs_git = pytest.mark.skipif(GIT is None, reason="git not available")


def run(coro):
    return asyncio.run(coro)


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run([GIT, *args], cwd=root, check=True, capture_output=True, text=True)
    return proc.stdout.strip()


def _init_repo(path: Path, files: dict[str, str] | None = None) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    for rel, content in (files or {}).items():
        target = path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    _git(path, "add", "-A")
    _git(path, "-c", "user.name=t", "-c", "user.email=t@e.c", "commit", "-q", "-m", "init")


@needs_git
def test_commit_creates_commit_with_net_message(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo, files={"a.py": "x = 1\n"})
    net = run(tie_safety_net(repo, "run-test"))
    (repo / "a.py").write_text("x = 2\n")
    sha = run(commit_session_work(repo, task_preview="fix the bug", net_tag=net.tag))
    assert sha and len(sha) == 40
    message = _git(repo, "log", "-1", "--format=%s")
    assert message.startswith("pxx: fix the bug")
    assert f"[net: {net.tag}]" in message
    assert _git(repo, "status", "--porcelain") == ""
    # the undo story is unchanged: the tag points at pre-session HEAD
    assert _git(repo, "rev-list", "-n", "1", net.tag) == _git(repo, "rev-parse", "HEAD~1")


@needs_git
def test_commit_no_diff_returns_none(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo, files={"a.py": "x = 1\n"})
    before = _git(repo, "rev-parse", "HEAD")
    assert run(commit_session_work(repo, task_preview="noop", net_tag=None)) is None
    assert _git(repo, "rev-parse", "HEAD") == before


def test_commit_outside_repo_fail_soft(tmp_path: Path) -> None:
    assert run(commit_session_work(tmp_path, task_preview="x", net_tag=None)) is None


@needs_git
def test_preview_sanitized_and_truncated(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo, files={"a.py": "x = 1\n"})
    (repo / "a.py").write_text("x = 2\n")
    long_preview = "fix\n" + "word " * 30
    sha = run(commit_session_work(repo, task_preview=long_preview, net_tag=None))
    message = _git(repo, "log", "-1", "--format=%s")
    assert "\n" not in message
    assert len(message) <= 4 + 72 + 1  # "pxx: " + preview + slack
    assert sha


# --- K5 net behavior (restored from the k5 workstream — deleted by mistake in 2.0.1-B) ---


@needs_git
def test_clean_tree_ties_tag_only(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo, files={"a.txt": "hello\n"})
    net = run(tie_safety_net(repo, "run-1"))
    assert net is not None
    assert net.tag and net.tag.startswith("pxx-pre/")
    assert net.stash_message is None  # clean tree: no stash
    assert _git(repo, "tag", "-l", "pxx-pre/*") == net.tag
    assert _git(repo, "rev-parse", net.tag) == _git(repo, "rev-parse", "HEAD")
    assert _git(repo, "stash", "list") == ""


@needs_git
def test_dirty_tree_stashes_and_tags_and_round_trips(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo, files={"a.txt": "hello\n"})
    (repo / "a.txt").write_text("modified\n")
    (repo / "new.txt").write_text("untracked\n")

    net = run(tie_safety_net(repo, "run-42"))
    assert net is not None and net.tag
    assert net.stash_message and "run-42" in net.stash_message
    assert _git(repo, "status", "--porcelain") == ""  # dirt parked
    assert "run-42" in _git(repo, "stash", "list")

    # the reviewer's live repro as a round-trip: reset to the tag, pop the
    # stash — the user's pre-session state comes back exactly.
    _git(repo, "reset", "--hard", net.tag)
    _git(repo, "stash", "pop")
    assert (repo / "a.txt").read_text() == "modified\n"
    assert (repo / "new.txt").read_text() == "untracked\n"


def test_non_git_cwd_is_noop(tmp_path: Path) -> None:
    assert run(tie_safety_net(tmp_path, "run-1")) is None


@needs_git
def test_tag_collision_suffixes(tmp_path: Path, monkeypatch) -> None:
    import time as _time

    repo = tmp_path / "repo"
    _init_repo(repo, files={"a.txt": "hello\n"})
    fixed = _time.struct_time((2026, 7, 21, 4, 5, 6, 1, 202, 0))
    monkeypatch.setattr(_time, "gmtime", lambda: fixed)
    _git(repo, "tag", "pxx-pre/20260721T040506Z", "HEAD")  # squat the base name

    net = run(tie_safety_net(repo, "run-1"))
    assert net is not None
    assert net.tag == "pxx-pre/20260721T040506Z-2"
    assert _git(repo, "rev-parse", net.tag) == _git(repo, "rev-parse", "HEAD")


@needs_git
def test_repo_without_commits_is_noop(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")  # no commits: nothing to tag
    assert run(tie_safety_net(repo, "run-1")) is None


# --- B1.4: loop commits exactly once, at the end, never per round ---------------------


@needs_git
def test_loop_commits_once_at_end_not_per_round(tmp_path: Path) -> None:
    """B1.4: with auto_commit on, a multi-round healing loop commits exactly
    ONCE at the end of the completed loop — never per round."""

    from pxx.backends.mock import MockBackend
    from pxx.config import ModelRef, Settings
    from pxx.loop import run_loop
    from pxx.review import ReviewMode
    from pxx.safety import PermissionMode

    class ReviseThenApprove:
        calls = 0

        async def review(self, diff: str, task: str) -> str:
            self.calls += 1
            return (
                "VERDICT: REVISE\nF-001 [high] a.py:1 fix"
                if self.calls == 1
                else "VERDICT: APPROVE"
            )

    repo = tmp_path / "repo"
    _init_repo(repo, files={"a.py": "x = 0\n"})
    settings = Settings(
        model=ModelRef(provider="ollama", model="stub"),
        permission=PermissionMode.AUTO,
        memory_enabled=False,
        memory_dir=tmp_path / "mem",
        state_dir=tmp_path / "state",
        auto_commit=True,
    )

    class Factory:
        def __init__(self):
            self.n = 0

        def __call__(self):
            self.n += 1
            return MockBackend(
                [
                    {"tool": "write_file", "args": {"path": "a.py", "content": f"x = {self.n}\n"}},
                    {"done": "ok"},
                ]
            )

    outcome = run(
        run_loop(
            "task",
            settings,
            cwd=repo,
            backend_factory=Factory(),
            reviewer=ReviseThenApprove(),
            review_mode=ReviewMode.BLOCKING,
            max_rounds=3,
        )
    )
    assert outcome.code.name == "COMPLETED"
    assert "[committed " in outcome.summary
    pxx_commits = _git(repo, "log", "--format=%s").splitlines()
    assert sum(1 for m in pxx_commits if m.startswith("pxx: ")) == 1  # exactly one, at the end


@needs_git
def test_loop_commit_excludes_baseline_dirt_and_keeps_trap_file(tmp_path: Path) -> None:
    """Loop path of the secondary-B fix: with safety_net=False and baseline
    dirt, the end-of-loop commit stages ONLY the loop's own delta — and a
    file that was dirty BEFORE the loop AND edited by it IS committed (the
    trap case: path-set subtraction would silently drop it)."""

    from pxx.backends.mock import MockBackend
    from pxx.config import ModelRef, Settings
    from pxx.loop import run_loop
    from pxx.safety import PermissionMode

    repo = tmp_path / "repo"
    _init_repo(repo, files={"a.py": "x = 1\n", "b.py": "y = 1\n"})
    # baseline dirt: .env + wip notes + b.py already modified (the trap file)
    (repo / ".env").write_text("SECRET=hunter2\n")
    (repo / "wip.txt").write_text("scratch\n")
    (repo / "b.py").write_text("y = 2\n")  # dirty BEFORE the loop starts

    settings = Settings(
        model=ModelRef(provider="ollama", model="stub"),
        permission=PermissionMode.AUTO,
        memory_enabled=False,
        memory_dir=tmp_path / "mem",
        state_dir=tmp_path / "state",
        auto_commit=True,
        safety_net=False,
    )
    factory = lambda: MockBackend(  # noqa: E731
        [
            {"tool": "write_file", "args": {"path": "a.py", "content": "x = 9\n"}},
            {"tool": "write_file", "args": {"path": "b.py", "content": "y = 3\n"}},
            {"done": "ok"},
        ]
    )
    outcome = run(run_loop("task", settings, cwd=repo, backend_factory=factory))
    assert outcome.code.name == "COMPLETED"
    committed = _git(repo, "show", "--format=", "--name-only", "HEAD").split()
    assert "a.py" in committed
    assert "b.py" in committed  # trap file: dirty at baseline, edited by the loop
    assert ".env" not in committed and "wip.txt" not in committed
    assert (repo / ".env").read_text() == "SECRET=hunter2\n"
    assert (repo / "wip.txt").read_text() == "scratch\n"


@needs_git
def test_commit_uses_repo_identity_when_configured(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo, files={"a.py": "x = 1\n"})
    _git(repo, "config", "user.name", "Repo Owner")
    _git(repo, "config", "user.email", "owner@example.com")
    (repo / "a.py").write_text("x = 2\n")
    sha = run(commit_session_work(repo, task_preview="x", net_tag=None))
    assert sha
    assert _git(repo, "log", "-1", "--format=%an") == "Repo Owner"


@needs_git
def test_commit_falls_back_to_pxx_identity_when_unconfigured(tmp_path: Path, monkeypatch) -> None:
    """07321d6: identity-less runners (CI, the 7960) must still get a commit —
    with an explicit pxx[bot] author, not a failure."""
    repo = tmp_path / "repo"
    _init_repo(repo, files={"a.py": "x = 1\n"})
    # no local config; strip global config + env identity
    empty_home = tmp_path / "home"
    empty_home.mkdir()
    monkeypatch.setenv("HOME", str(empty_home))
    for var in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        monkeypatch.delenv(var, raising=False)
    (repo / "a.py").write_text("x = 2\n")
    sha = run(commit_session_work(repo, task_preview="x", net_tag=None))
    assert sha
    assert _git(repo, "log", "-1", "--format=%an") == "pxx[bot]"
    assert _git(repo, "log", "-1", "--format=%ae") == "pxx[bot]@localhost"
