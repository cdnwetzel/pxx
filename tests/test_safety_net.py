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


@needs_git
def test_commit_blocked_when_staged_delta_contains_secret(tmp_path: Path) -> None:
    """Fail-closed secrets gate: a staged secret pattern means NO commit —
    the work stays in the tree for the user to fix (fail-soft on the session)."""
    repo = tmp_path / "repo"
    _init_repo(repo, files={"a.py": "x = 1\n"})
    before = _git(repo, "rev-parse", "HEAD")
    # Construct the AKIA sample by concatenation so the literal secret never
    # appears in this source file (which pxx's OWN governance scan reads in CI) —
    # the assembled 20-char value IS written to the staged tree, which is exactly
    # what scan_staged (the gate under test) inspects. Mirrors test_governance.py.
    secret_line = 'AWS_KEY = "' + "AKIA" + "IOSFODNN7EXAMPLE" + '"\n'
    (repo / "a.py").write_text(secret_line)
    assert run(commit_session_work(repo, task_preview="add key", net_tag=None)) is None
    assert _git(repo, "rev-parse", "HEAD") == before  # no commit happened
    # the work itself is untouched, still in the tree (staged) for the user
    assert (repo / "a.py").read_text() == secret_line


@needs_git
def test_commit_blocked_when_scan_indeterminable(tmp_path: Path, monkeypatch) -> None:
    """A scan that cannot run is NOT a clean scan (governance doctrine):
    PxxError from the scan (scan_blobs, since the commit scans the exact
    blobs it records) also skips the commit and keeps the work."""
    import pxx.governance
    from pxx.errors import PxxError

    repo = tmp_path / "repo"
    _init_repo(repo, files={"a.py": "x = 1\n"})
    before = _git(repo, "rev-parse", "HEAD")
    (repo / "a.py").write_text("x = 2\n")

    def boom(cwd, blobs, *, denylist):
        raise PxxError("governance: cannot scan staged files: simulated index lock")

    monkeypatch.setattr(pxx.governance, "scan_blobs", boom)
    assert run(commit_session_work(repo, task_preview="x", net_tag=None)) is None
    assert _git(repo, "rev-parse", "HEAD") == before  # no commit happened
    assert (repo / "a.py").read_text() == "x = 2\n"  # work remains


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


@needs_git
def test_restore_brings_back_untracked_on_abort(tmp_path: Path) -> None:
    """F1: on an abort, restore the user's pre-run tree — including UNTRACKED
    files, which the pxx-pre tag alone cannot recover (neo, R-023)."""
    from pxx.safety_net import restore_safety_net

    repo = tmp_path / "repo"
    _init_repo(repo, files={"a.txt": "hello\n"})
    (repo / "a.txt").write_text("user-wip\n")  # tracked-dirty
    (repo / "notes.txt").write_text("user-untracked\n")  # untracked — the vulnerable one

    net = run(tie_safety_net(repo, "run-abort"))
    assert net.stash_message and "run-abort" in net.stash_message
    assert not (repo / "notes.txt").exists()  # parked in the stash
    (repo / "a.txt").write_text("pxx-partial-garbage\n")  # a failed run's leftover edit

    assert run(restore_safety_net(repo, net)) is True
    assert (repo / "a.txt").read_text() == "user-wip\n"  # partial discarded, wip back
    assert (repo / "notes.txt").read_text() == "user-untracked\n"  # untracked RESTORED
    assert _git(repo, "stash", "list") == ""  # our stash consumed, not stranded


@needs_git
def test_restore_resets_tag_only_net_on_abort(tmp_path: Path) -> None:
    """A clean-tree abort (tag-only net) still resets — the failed run's partial
    tracked edits are discarded, not left behind (CodeRabbit #24)."""
    from pxx.safety_net import restore_safety_net

    repo = tmp_path / "repo"
    _init_repo(repo, files={"a.txt": "orig\n"})
    net = run(tie_safety_net(repo, "run-clean"))
    assert net.stash_message is None and net.tag  # tag-only
    (repo / "a.txt").write_text("failed-run-partial\n")  # a failed run's leftover edit
    assert run(restore_safety_net(repo, net)) is True
    assert (repo / "a.txt").read_text() == "orig\n"  # partial reverted to the tag


@needs_git
def test_stash_ref_matches_exactly_not_by_prefix(tmp_path: Path) -> None:
    """`run-1` must not select the `run-10` stash (exact trailing-message match,
    CodeRabbit #24)."""
    from pxx.safety_net import _stash_ref

    repo = tmp_path / "repo"
    _init_repo(repo, files={"a.txt": "base\n"})
    (repo / "a.txt").write_text("v10\n")
    _git(repo, "stash", "push", "-m", "pxx safety net run-10")
    (repo / "a.txt").write_text("v1\n")
    _git(repo, "stash", "push", "-m", "pxx safety net run-1")

    ref = run(_stash_ref(repo, "pxx safety net run-1"))
    assert ref is not None
    _git(repo, "stash", "pop", ref)
    assert (repo / "a.txt").read_text() == "v1\n"  # the run-1 stash, not run-10


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


def _ident(repo: Path, *args: str) -> str:
    return _git(repo, "-c", "user.name=t", "-c", "user.email=t@e.c", *args)


@needs_git
def test_restore_refuses_when_the_net_tag_was_retargeted(tmp_path: Path) -> None:
    """Grok review (2.9.1 r4, P0): the guard checked HEAD and the branch but
    not the reset TARGET -- a run that `git tag -f`s pxx-pre onto a planted
    commit and then fails had the branch reset onto it."""
    from pxx.safety_net import head_moved_since, restore_safety_net, tie_safety_net

    repo = tmp_path / "repo"
    _init_repo(repo, files={"a.py": "x = 1\n"})
    tip = _git(repo, "rev-parse", "HEAD")
    net = run(tie_safety_net(repo, "run-tag"))
    assert net is not None and net.tag
    tree = _git(repo, "rev-parse", "HEAD^{tree}")
    planted = _ident(repo, "commit-tree", tree, "-m", "planted")
    _git(repo, "tag", "-f", net.tag, planted)
    assert "no longer points" in run(head_moved_since(repo, net))
    assert run(restore_safety_net(repo, net)) is False
    assert _git(repo, "rev-parse", "HEAD") == tip  # the branch never moved


@needs_git
def test_restore_pops_the_nets_own_stash_not_a_newer_same_named_one(tmp_path: Path) -> None:
    """Grok review (2.9.1 r4): the stash was chosen by message, so a newer
    stash reusing the net's message was popped instead of the user's WIP."""
    from pxx.safety_net import restore_safety_net, tie_safety_net

    repo = tmp_path / "repo"
    _init_repo(repo, files={"a.py": "x = 1\n"})
    (repo / "wip.txt").write_text("user wip\n")
    net = run(tie_safety_net(repo, "run-stash"))
    assert net is not None and net.stash_message and net.stash_sha
    (repo / "planted.txt").write_text("planted\n")
    _ident(repo, "stash", "push", "-u", "-q", "-m", net.stash_message)
    assert run(restore_safety_net(repo, net)) is True
    assert (repo / "wip.txt").read_text() == "user wip\n"  # the user's WIP is back
    assert not (repo / "planted.txt").exists()
    remaining = _git(repo, "stash", "list")
    assert remaining.count("\n") == 0 and net.stash_message in remaining  # the planted one stays


@needs_git
def test_restore_is_not_fooled_by_git_replace(tmp_path: Path) -> None:
    """Grok review (2.9.1 r5, P0): `git replace <start> <planted>` left every
    SHA the guard compared unchanged, and `reset --hard` followed the replace
    ref -- the "restore" checked out the attacker's tree."""
    from pxx.safety_net import restore_safety_net, tie_safety_net

    repo = tmp_path / "repo"
    _init_repo(repo, files={"ok.py": "safe = True\n"})
    start = _git(repo, "rev-parse", "HEAD")
    net = run(tie_safety_net(repo, "run-replace"))
    assert net is not None and net.head_tree
    (repo / "ok.py").write_text("safe = False\n")
    (repo / "evil.txt").write_text("planted\n")
    _git(repo, "add", "-A")
    _ident(repo, "commit", "-q", "-m", "planted")
    planted = _git(repo, "rev-parse", "HEAD")
    _git(repo, "reset", "-q", "--hard", start)
    _git(repo, "replace", start, planted)
    run(restore_safety_net(repo, net))  # whatever it decides, it must not plant
    assert (repo / "ok.py").read_text() == "safe = True\n"
    assert not (repo / "evil.txt").exists()


@needs_git
def test_pxx_commit_ignores_a_hook_written_during_the_run(tmp_path: Path) -> None:
    """Grok review (2.9.1 r5): a pre-commit hook (invisible to every scope
    check) staged a protected file into pxx's path-limited commit."""
    repo = tmp_path / "repo"
    _init_repo(repo, files={"ok.py": "x = 1\n", "pxx/safety.py": "SAFE = True\n"})
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'SAFE = False' > pxx/safety.py\ngit add pxx/safety.py\n")
    hook.chmod(0o755)
    (repo / "ok.py").write_text("x = 2\n")
    sha = run(commit_session_work(repo, task_preview="t", net_tag=None, only={"ok.py"}))
    assert sha
    assert _git(repo, "show", "--name-only", "--format=", "HEAD").split() == ["ok.py"]
    assert (repo / "pxx" / "safety.py").read_text() == "SAFE = True\n"  # the hook never ran


# Assembled so no source line matches the CI governance gate; same bytes.
_SECRET = "api_key = '" + "sk-" + "live-" + "0123456789abcdef" * 2 + "'\n"


def _no_secret_committed(repo: Path) -> bool:
    names = _git(repo, "ls-tree", "-r", "--name-only", "HEAD").splitlines()
    return all("sk-live" not in _git(repo, "show", f"HEAD:{n}") for n in names if n)


@needs_git
def test_commit_never_ships_a_secret_behind_assume_unchanged(tmp_path: Path) -> None:
    """Grok review (2.9.1 r6, P0): `git add` of an assume-unchanged file did
    nothing, the scan saw the old index blob, `git commit -- path` committed
    the worktree secret."""
    repo = tmp_path / "repo"
    _init_repo(repo, files={"ok.py": "x = 1\n"})
    _git(repo, "update-index", "--assume-unchanged", "ok.py")
    (repo / "ok.py").write_text(_SECRET)
    assert run(commit_session_work(repo, task_preview="t", net_tag=None, only={"ok.py"})) is None
    assert _no_secret_committed(repo)


@needs_git
def test_commit_records_exactly_the_bytes_it_scanned(tmp_path: Path) -> None:
    """Grok review (2.9.1 r6, P0): a clean filter that redacts on its FIRST
    run only -- the scan saw the redacted blob, the commit's second filter run
    stored the secret. The worktree is now hashed once and that blob is both
    scanned and committed."""
    repo = tmp_path / "repo"
    _init_repo(repo, files={"ok.py": "x = 1\n"})
    counter = tmp_path / "runs"
    script = tmp_path / "filter.sh"
    script.write_text(
        f"#!/bin/sh\nn=$(cat {counter} 2>/dev/null || echo 0); echo $((n+1)) > {counter}\n"
        'if [ "$n" = 0 ]; then cat >/dev/null; echo "x = 1"; else cat; fi\n'
    )
    script.chmod(0o755)
    _git(repo, "config", "filter.twophase.clean", str(script))
    (repo / ".gitattributes").write_text("*.py filter=twophase\n")
    _git(repo, "add", ".gitattributes")
    _ident(repo, "commit", "-q", "-m", "attrs")
    (repo / "ok.py").write_text(_SECRET)
    run(commit_session_work(repo, task_preview="t", net_tag=None, only={"ok.py"}))
    assert _no_secret_committed(repo)


@needs_git
def test_a_name_that_looks_like_an_index_stage_is_scanned_as_itself(tmp_path: Path) -> None:
    """Grok review (2.9.1 r6, P0): `cat-file blob :0:notes.txt` read stage 0
    of notes.txt, so a secret in a file named `0:notes.txt` was scanned as the
    clean decoy and committed."""
    repo = tmp_path / "repo"
    _init_repo(repo, files={"notes.txt": "clean\n"})
    (repo / "0:notes.txt").write_text(_SECRET)
    assert (
        run(commit_session_work(repo, task_preview="t", net_tag=None, only={"0:notes.txt"})) is None
    )
    assert _no_secret_committed(repo)


@needs_git
def test_scan_staged_ignores_git_replace(tmp_path: Path) -> None:
    """Grok review (2.9.1 r6, P0): with the staged secret blob replaced by a
    clean one, the scanner (following replace refs) saw clean bytes."""
    from pxx.governance import scan_staged

    repo = tmp_path / "repo"
    _init_repo(repo, files={"ok.py": "x = 1\n"})
    (repo / "ok.py").write_text(_SECRET)
    _git(repo, "add", "ok.py")
    secret_blob = _git(repo, "rev-parse", ":ok.py")
    clean_blob = _git(repo, "rev-parse", "HEAD:ok.py")
    _git(repo, "replace", secret_blob, clean_blob)
    assert scan_staged(cwd=repo), "the replace ref hid the staged secret"


@needs_git
def test_pxx_commit_runs_no_reference_transaction_hook(tmp_path: Path) -> None:
    """Codex review (2.9.1 r7, P0): `update-ref` fires reference-transaction,
    so moving from `git commit` to commit-tree still ran a planted hook."""
    repo = tmp_path / "repo"
    _init_repo(repo, files={"ok.py": "x = 1\n", "pxx/safety.py": "SAFE = True\n"})
    marker = tmp_path / "hook-ran"
    hook = repo / ".git" / "hooks" / "reference-transaction"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\necho 'SAFE = False' > pxx/safety.py\n")
    hook.chmod(0o755)
    (repo / "ok.py").write_text("x = 2\n")
    sha = run(commit_session_work(repo, task_preview="t", net_tag=None, only={"ok.py"}))
    assert sha
    assert not marker.exists()  # the hook never ran
    assert (repo / "pxx" / "safety.py").read_text() == "SAFE = True\n"


def _plant_filter(repo: Path, attr_path: str, clean: str, smudge: str = "cat") -> None:
    """A filter driver + attribute, both invisible to `git status`."""
    _git(repo, "config", "filter.evil.clean", clean)
    _git(repo, "config", "filter.evil.smudge", smudge)
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "attributes").write_text(f"{attr_path} filter=evil\n")


@needs_git
def test_restore_refuses_after_the_run_planted_a_smudge_filter(tmp_path: Path) -> None:
    # Grok review (2.9.1 r7, P0): `reset --hard` under a smudge filter planted
    # during the run checked out other bytes than the tree holds, and the
    # restore reported success with `git status` clean.
    from pxx.safety_net import restore_blocker, restore_safety_net

    repo = tmp_path / "repo"
    _init_repo(repo, files={"guard.py": "SAFE = True\n"})
    net = run(tie_safety_net(repo, "run-smudge"))
    _plant_filter(repo, "guard.py", clean="cat", smudge="sed s/True/False/")
    (repo / "guard.py").write_text("SAFE = 1\n")  # the failed run's edit
    reason, detail = run(restore_blocker(repo, net))
    assert reason == "git_state_changed" and "info/attributes" in detail
    assert run(restore_safety_net(repo, net)) is False
    assert (repo / "guard.py").read_text() == "SAFE = 1\n"  # nothing smudged in


@needs_git
def test_commit_never_runs_a_clean_filter(tmp_path: Path) -> None:
    # Grok review (2.9.1 r7, P0): `hash-object --path=<rel>` ran a clean
    # filter on the bytes before the scan; the filter rewrote a protected
    # file and hid it behind assume-unchanged. Even a filter that predates
    # the run (the control state is unchanged) must not run for pxx's commit.
    from pxx.worktree import git_control_state

    repo = tmp_path / "repo"
    _init_repo(repo, files={"ok.py": "x = 1\n", "guard.py": "SAFE = True\n"})
    marker = tmp_path / "filter-ran"
    _plant_filter(repo, "ok.py", clean=f"sh -c 'touch {marker}; cat'")
    control = run(git_control_state(repo))
    (repo / "ok.py").write_text("x = 2\n")
    head = _git(repo, "rev-parse", "HEAD")
    sha = run(
        commit_session_work(
            repo, task_preview="t", net_tag=None, only={"ok.py"}, control_start=control
        )
    )
    assert sha is None
    assert _git(repo, "rev-parse", "HEAD") == head
    assert not marker.exists()  # the clean command never ran


@needs_git
def test_commit_refuses_after_the_git_configuration_changed(tmp_path: Path) -> None:
    from pxx.worktree import git_control_state

    repo = tmp_path / "repo"
    _init_repo(repo, files={"ok.py": "x = 1\n"})
    control = run(git_control_state(repo))
    _git(repo, "config", "core.attributesFile", str(tmp_path / "elsewhere"))
    (repo / "ok.py").write_text("x = 2\n")
    sha = run(
        commit_session_work(
            repo, task_preview="t", net_tag=None, only={"ok.py"}, control_start=control
        )
    )
    assert sha is None


@needs_git
def test_restore_resets_to_the_checked_sha_not_the_tag_name(tmp_path: Path, monkeypatch) -> None:
    # Grok review (2.9.1 r7, P2): the tag was checked, then the reset named
    # the tag -- a re-tag between the two was not covered.
    import pxx.safety_net as sn

    repo = tmp_path / "repo"
    _init_repo(repo, files={"a.py": "x = 1\n"})
    net = run(tie_safety_net(repo, "run-sha"))
    (repo / "a.py").write_text("x = 2\n")
    seen: list[tuple[str, ...]] = []
    real = sn._git

    async def spy(cwd, *args):
        seen.append(args)
        return await real(cwd, *args)

    monkeypatch.setattr(sn, "_git", spy)
    assert run(sn.restore_safety_net(repo, net)) is True
    resets = [a for a in seen if a[:2] == ("reset", "--hard")]
    assert resets == [("reset", "--hard", net.head_sha)]
    assert (repo / "a.py").read_text() == "x = 1\n"


@needs_git
def test_a_stash_that_cannot_pop_is_never_reported_restored(tmp_path: Path) -> None:
    # Kimi review (2.9.1 r9, P1): the failed run created a file at a path the
    # net had stashed, so the pop failed -- behind `restored: true`, with the
    # operator's work still in the stash.
    from pxx.safety_net import settle_failed_run

    repo = tmp_path / "repo"
    _init_repo(repo, files={"a.py": "x = 1\n"})
    for key, val in (("user.name", "t"), ("user.email", "t@e.c")):
        _git(repo, "config", key, val)
    (repo / "wip.txt").write_text("operator work\n")
    net = run(tie_safety_net(repo, "run-pop"))
    assert net.stash_message and not (repo / "wip.txt").exists()
    (repo / "wip.txt").write_text("the run's file\n")  # collides with the stash
    events: list[dict] = []

    async def emit(data: dict) -> None:
        events.append(data)

    suffix = run(settle_failed_run(repo, net, emit))
    assert "partly restored" in suffix and "still stashed" in suffix
    assert [e.get("restored") for e in events] == [False]
    assert events[0]["reason"] == "stash_not_restored"
    assert _git(repo, "stash", "list")  # the operator's work is still there


@needs_git
def test_commit_keeps_the_recorded_mode_when_filemode_is_off(tmp_path: Path) -> None:
    # CodeRabbit (2.9.1, PR #98): with core.fileMode=false (WSL /mnt/c, FAT)
    # files report 0o777, and every committed file became 100755.
    import os as _os

    repo = tmp_path / "repo"
    _init_repo(repo, files={"a.py": "x = 1\n"})
    _git(repo, "config", "core.fileMode", "false")
    (repo / "a.py").write_text("x = 2\n")
    _os.chmod(repo / "a.py", 0o777)
    sha = run(commit_session_work(repo, task_preview="t", net_tag=None, only={"a.py"}))
    assert sha
    assert _git(repo, "ls-tree", "HEAD", "a.py").split()[0] == "100644"


@needs_git
def test_a_control_state_unreadable_at_tie_blocks_the_restore(tmp_path, monkeypatch) -> None:
    # CodeRabbit (2.9.1, PR #98, Major): an unreadable state at tie time was
    # stored as None ("unknown"), so restore never checked it -- fail open.
    import pxx.worktree as wt
    from pxx.safety_net import restore_blocker, restore_safety_net

    repo = tmp_path / "repo"
    _init_repo(repo, files={"a.py": "x = 1\n"})

    async def unreadable(cwd):
        return None

    monkeypatch.setattr(wt, "git_control_state", unreadable)
    net = run(tie_safety_net(repo, "run-unreadable"))
    monkeypatch.undo()
    (repo / "a.py").write_text("x = 2\n")
    reason, detail = run(restore_blocker(repo, net))
    assert reason == "git_state_changed" and "could not be read" in detail
    assert run(restore_safety_net(repo, net)) is False
    assert (repo / "a.py").read_text() == "x = 2\n"


@needs_git
@pytest.mark.parametrize(
    "reason", ["nothing_to_commit", "secrets", "filter_attribute", "git_state_changed"]
)
def test_a_skipped_commit_says_why(tmp_path: Path, reason: str) -> None:
    # 2.9.3 (plan B2): every path that commits nothing returns a reason
    # consumers can act on, instead of a bare None.
    from pxx.safety_net import COMMIT_SKIP_REASONS, commit_session_work_checked
    from pxx.worktree import git_control_state

    repo = tmp_path / "repo"
    _init_repo(repo, files={"ok.py": "x = 1\n"})
    control = run(git_control_state(repo))
    only = {"ok.py"}
    if reason == "nothing_to_commit":
        only = set()
    elif reason == "secrets":
        (repo / "ok.py").write_text(_SECRET)
    elif reason == "filter_attribute":
        _plant_filter(repo, "ok.py", clean="cat")
        control = run(git_control_state(repo))  # pre-existing: control state unchanged
        (repo / "ok.py").write_text("x = 2\n")
    else:
        (repo / "ok.py").write_text("x = 2\n")
        _git(repo, "config", "pxx.test.marker", "changed")
    sha, why = run(
        commit_session_work_checked(
            repo, task_preview="t", net_tag=None, only=only, control_start=control
        )
    )
    assert sha is None and why == reason and why in COMMIT_SKIP_REASONS


@needs_git
@pytest.mark.parametrize("moved", [False, True])
def test_a_failed_head_update_names_its_cause(tmp_path: Path, monkeypatch, moved: bool) -> None:
    # 2.9.3 review: update-ref failing (lock, timeout) is git_failed; only a
    # HEAD seen to move is head_moved.
    import pxx.safety_net as sn

    repo = tmp_path / "repo"
    _init_repo(repo, files={"ok.py": "x = 1\n"})
    (repo / "ok.py").write_text("x = 2\n")
    real = sn._git_io

    async def fake(cwd, *args, **kw):
        if args and args[0] == "update-ref":
            if not moved:
                return None
            _git(repo, "commit", "--allow-empty", "-q", "-m", "racer")
        return await real(cwd, *args, **kw)

    monkeypatch.setattr(sn, "_git_io", fake)
    sha, why = run(
        sn.commit_session_work_checked(repo, task_preview="t", net_tag=None, only={"ok.py"})
    )
    assert sha is None and why == ("head_moved" if moved else "git_failed")


@needs_git
def test_a_committed_change_reports_no_skip_reason(tmp_path: Path) -> None:
    from pxx.safety_net import commit_session_work_checked

    repo = tmp_path / "repo"
    _init_repo(repo, files={"ok.py": "x = 1\n"})
    (repo / "ok.py").write_text("x = 2\n")
    sha, why = run(
        commit_session_work_checked(repo, task_preview="t", net_tag=None, only={"ok.py"})
    )
    assert sha and why == ""
