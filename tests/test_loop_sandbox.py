"""2.5.5+ps2: the loop's own test run is confined like ``run_shell``.

Until this patch ``_run_tests`` ran the project's test command with
``create_subprocess_shell`` on the host — with the harness's reach — while the
model's own shell calls were sandboxed. The tests are code the model wrote.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

import pytest

from pxx import loop as loopmod
from pxx.tools import shell as shellmod


class _FakeProc:
    def __init__(self, out: bytes = b"", rc: int = 0) -> None:
        self._out, self.returncode = out, rc

    async def communicate(self) -> tuple[bytes, bytes]:
        await asyncio.sleep(0)
        return self._out, b""

    def kill(self) -> None:  # pragma: no cover - not reached
        pass

    async def wait(self) -> None:  # pragma: no cover - not reached
        await asyncio.sleep(0)


def test_sandbox_argv_is_none_without_a_sandboxer(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(shellmod.shutil, "which", lambda name: None)
    assert shellmod.sandbox_argv(tmp_path, "true", tmp_path) is None


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="bwrap is Linux")
def test_sandbox_argv_binds_only_the_root_writable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(shellmod.shutil, "which", lambda name: "/usr/bin/bwrap")
    argv = shellmod.sandbox_argv(tmp_path, "pytest -q", tmp_path)
    assert argv is not None and argv[0] == "bwrap"
    assert argv[argv.index("--bind") + 1] == str(tmp_path)
    assert argv[-3:] == ["/bin/sh", "-c", "pytest -q"]


def test_run_tests_unsandboxed_path_is_unchanged(tmp_path: Path, monkeypatch) -> None:
    seen: dict[str, object] = {}

    async def fake_shell(command, **kw):
        seen["command"] = command
        return _FakeProc(b"1 passed\n", 0)

    async def no_exec(*a, **kw):  # pragma: no cover - must not run
        raise AssertionError("exec path used without sandbox")

    monkeypatch.setattr(loopmod.asyncio, "create_subprocess_shell", fake_shell)
    monkeypatch.setattr(loopmod.asyncio, "create_subprocess_exec", no_exec)
    passed, failing, _ = asyncio.run(loopmod._run_tests(tmp_path, "pytest -q"))
    assert passed and not failing and seen["command"] == "pytest -q"


def test_run_tests_sandboxed_goes_through_the_sandboxer(tmp_path: Path, monkeypatch) -> None:
    seen: dict[str, object] = {}

    async def fake_exec(*argv, **kw):
        seen["argv"] = argv
        return _FakeProc(b"FAILED tests/t.py::test_a\n1 failed\n", 1)

    async def no_shell(*a, **kw):  # pragma: no cover - must not run
        raise AssertionError("shell path used with sandbox")

    monkeypatch.setattr(shellmod.shutil, "which", lambda name: "/usr/bin/bwrap")
    monkeypatch.setattr(loopmod.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(loopmod.asyncio, "create_subprocess_shell", no_shell)
    passed, failing, _tail = asyncio.run(loopmod._run_tests(tmp_path, "pytest -q", sandbox=True))
    argv = seen["argv"]
    assert argv[0] in ("bwrap", "sandbox-exec") and argv[-1] == "pytest -q"
    assert not passed and failing == {"tests/t.py::test_a"}


def test_run_tests_sandbox_requested_but_absent_fails_closed(tmp_path: Path, monkeypatch) -> None:
    """Asked for a sandbox and there is none: the suite is NOT run, and the
    result is an infrastructure failure, never a pass."""

    async def explode(*a, **kw):  # pragma: no cover - must not run
        raise AssertionError("test command ran without the requested sandbox")

    monkeypatch.setattr(shellmod.shutil, "which", lambda name: None)
    monkeypatch.setattr(loopmod.asyncio, "create_subprocess_exec", explode)
    monkeypatch.setattr(loopmod.asyncio, "create_subprocess_shell", explode)
    passed, failing, tail = asyncio.run(loopmod._run_tests(tmp_path, "pytest -q", sandbox=True))
    assert not passed
    assert any(f.startswith("spawn-error:sandbox-unavailable") for f in failing)
    assert "NOT run" in tail


@pytest.mark.skipif(
    not (sys.platform.startswith("linux") and shutil.which("bwrap")),
    reason="needs bubblewrap",
)
def test_real_sandbox_denies_writes_outside_the_root() -> None:
    """Both paths must live outside /tmp: the profile mounts a writable tmpfs
    on /tmp (before binding the root, so a root under /tmp stays visible), which
    means an ``outside`` under pytest's tmp_path would be writable and prove
    nothing. A directory beside this file is outside /tmp on every runner."""
    import tempfile

    here = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(dir=here, prefix=".sandbox-") as td:
        base = Path(td)
        root = base / "repo"
        root.mkdir()
        outside = base / "escape.txt"
        passed, _failing, tail = asyncio.run(
            loopmod._run_tests(root, f"touch {outside} && echo wrote", sandbox=True)
        )
        assert not passed and not outside.exists(), tail
        passed, _, tail = asyncio.run(loopmod._run_tests(root, "touch inside.txt", sandbox=True))
        assert passed and (root / "inside.txt").exists(), tail


def test_sandbox_setup_failure_is_infrastructure_not_a_baseline(
    tmp_path: Path, monkeypatch
) -> None:
    """bwrap present but unable to create its namespaces exits before the
    command runs; that must read as a spawn error, not as the suite failing."""

    async def fake_exec(*argv, **kw):
        return _FakeProc(b"bwrap: Can't create namespace: Operation not permitted\n", 1)

    monkeypatch.setattr(shellmod.shutil, "which", lambda name: "/usr/bin/bwrap")
    monkeypatch.setattr(loopmod.asyncio, "create_subprocess_exec", fake_exec)
    passed, failing, tail = asyncio.run(loopmod._run_tests(tmp_path, "pytest -q", sandbox=True))
    assert not passed
    assert len(failing) == 1 and next(iter(failing)).startswith("spawn-error:sandbox-setup:")
    assert "namespace" in tail


@pytest.mark.skipif(
    not (sys.platform.startswith("linux") and shutil.which("bwrap")),
    reason="needs bubblewrap",
)
def test_real_sandbox_keeps_a_repository_under_tmp_visible(tmp_path: Path) -> None:
    """pytest's tmp_path lives under /tmp; the profile mounts a tmpfs there,
    which used to be applied AFTER the repository bind and hid it."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "marker").write_text("here\n")
    passed, _, tail = asyncio.run(
        loopmod._run_tests(root, "test -f marker && touch inside.txt", sandbox=True)
    )
    assert passed, tail
    assert (root / "inside.txt").exists()
