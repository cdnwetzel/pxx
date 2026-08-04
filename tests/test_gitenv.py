"""The GIT_* scrub: pxx git subprocesses must target the repo they are
pointed at, never one named by inherited environment (the git-hook leak,
2026-07-26)."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from pxx.gitenv import SCRUBBED_GIT_VARS, git_env
from pxx.safety_net import tie_safety_net

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")

GIT = shutil.which("git") or "git"


def _init_repo(path: Path, files: dict[str, str]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run([GIT, "init", "-q"], cwd=path, check=True, env=git_env())
    for rel, content in files.items():
        (path / rel).write_text(content)
    subprocess.run([GIT, "add", "-A"], cwd=path, check=True, env=git_env())
    subprocess.run(
        [GIT, "-c", "user.name=t", "-c", "user.email=t@e.c", "commit", "-q", "-m", "init"],
        cwd=path,
        check=True,
        env=git_env(),
    )


def test_git_env_drops_exactly_the_scrub_set(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in SCRUBBED_GIT_VARS:
        monkeypatch.setenv(var, "poisoned")
    monkeypatch.setenv("GIT_SSH_COMMAND", "ssh")  # transport var must survive
    env = git_env()
    assert not (set(env) & set(SCRUBBED_GIT_VARS))
    assert env["GIT_SSH_COMMAND"] == "ssh"
    assert os.environ["GIT_DIR"] == "poisoned"  # never mutates the process env


@needs_git
def test_poisoned_git_dir_cannot_redirect_pxx_git_ops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hook scenario: GIT_DIR/GIT_INDEX_FILE point at a victim repo;
    a pxx git op against a target repo must touch only the target."""
    victim = tmp_path / "victim"
    target = tmp_path / "target"
    _init_repo(victim, {"keep.txt": "victim\n"})
    _init_repo(target, {"work.txt": "target\n"})

    monkeypatch.setenv("GIT_DIR", str(victim / ".git"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(victim / ".git" / "index"))
    monkeypatch.setenv("GIT_AUTHOR_NAME", "leaked-author")

    (target / "work.txt").write_text("dirty\n")  # make the net stash + tag
    net = asyncio.run(tie_safety_net(target, "gitenv-test"))

    assert net is not None and net.tag is not None
    # the tag landed on the target, not the victim
    tags_target = subprocess.run(
        [GIT, "tag"], cwd=target, capture_output=True, text=True, env=git_env(), check=True
    ).stdout
    tags_victim = subprocess.run(
        [GIT, "tag"], cwd=victim, capture_output=True, text=True, env=git_env(), check=True
    ).stdout
    assert net.tag in tags_target
    assert net.tag not in tags_victim
    # victim's index untouched: status is clean
    victim_status = subprocess.run(
        [GIT, "status", "--porcelain"],
        cwd=victim,
        capture_output=True,
        text=True,
        env=git_env(),
        check=True,
    ).stdout
    assert victim_status == ""


@needs_git
def test_eval_harness_git_is_scrubbed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The reviewer-proven P0 (2026-07-26): harness._git ran `add -A` against
    a GIT_DIR-named victim repo. Must target only its own scaffold."""
    from pxx.eval.harness import _git as harness_git

    victim = tmp_path / "victim"
    _init_repo(victim, {"real.txt": "keep\n"})
    scaffold = tmp_path / "scaffold"
    scaffold.mkdir()

    monkeypatch.setenv("GIT_DIR", str(victim / ".git"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(victim / ".git" / "index"))

    harness_git(scaffold, "init", "-q")
    (scaffold / "a.txt").write_text("x\n")
    harness_git(scaffold, "add", "-A")

    assert (scaffold / ".git").is_dir()  # init landed in the scaffold
    victim_status = subprocess.run(
        [GIT, "status", "--porcelain"],
        cwd=victim,
        capture_output=True,
        text=True,
        env=git_env(),
        check=True,
    ).stdout
    assert victim_status == ""  # victim index untouched


# --- git subprocess timeout backstop (2.3.6) --------------------------------

from pxx.gitenv import communicate_bounded, git_timeout  # noqa: E402


@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, 60.0),
        ("12.5", 12.5),
        ("0.5", 0.5),
        ("bad", 60.0),
        ("-1", 60.0),
        ("0", 60.0),
        ("inf", 60.0),
    ],
)
def test_git_timeout_env_parsing(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("PXX_GIT_TIMEOUT", raising=False)
    else:
        monkeypatch.setenv("PXX_GIT_TIMEOUT", raw)
    assert git_timeout() == expected


def test_communicate_bounded_times_out_kills_and_raises(monkeypatch):
    # A real, killable child that never exits on its own must be killed + reaped,
    # and TimeoutError re-raised — never a hang, never a leaked process.
    monkeypatch.setenv("PXX_GIT_TIMEOUT", "0.2")

    async def go():
        proc = await asyncio.create_subprocess_exec(
            "sleep", "10", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        with pytest.raises(TimeoutError):
            await communicate_bounded(proc, label="sleep")
        assert proc.returncode is not None  # reaped (killed), not left running

    asyncio.run(go())


class _FakeProc:
    """A git process stand-in — the timeout path is forced via a stubbed
    communicate_bounded, so create_subprocess_exec must not spawn real git."""

    returncode = None

    def kill(self) -> None:  # pragma: no cover - not reached via the stub
        pass

    async def wait(self) -> int:  # pragma: no cover
        return 0


async def _fake_exec(*_a, **_k):
    return _FakeProc()


async def _raise_timeout(*_a, **_k):
    raise TimeoutError


def test_safety_net_git_degrades_to_none_on_timeout(monkeypatch, tmp_path: Path):
    import pxx.safety_net as sn

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(sn, "communicate_bounded", _raise_timeout)
    assert asyncio.run(sn._git(tmp_path, "status")) is None


def test_loop_git_degrades_to_none_on_timeout(monkeypatch, tmp_path: Path):
    import pxx.loop as loopmod

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(loopmod, "communicate_bounded", _raise_timeout)
    assert asyncio.run(loopmod._git(tmp_path, "diff")) is None


def test_goal_git_reports_failure_on_timeout(monkeypatch, tmp_path: Path):
    import pxx.goal as goalmod

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(goalmod, "communicate_bounded", _raise_timeout)
    code, out = asyncio.run(goalmod._git(tmp_path, "apply"))
    assert code != 0 and "timed out" in out
