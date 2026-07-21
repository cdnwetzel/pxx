"""Tests for the built-in tools: fs, shell, memory."""

from __future__ import annotations

import asyncio
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from pxx.errors import HookDenied, HooksMissing, ScopeViolation
from pxx.events import EventBus
from pxx.safety import Hook, HookRunner, PermissionMode, ScopeGate
from pxx.tools import ToolContext, ToolRegistry, default_registry
from pxx.tools.shell import seatbelt_profile


def make_ctx(
    root: Path,
    *,
    permission: PermissionMode = PermissionMode.AUTO,
    hooks: tuple[Hook, ...] = (),
    bus: EventBus | None = None,
    memory: Any = None,
    sandbox_shell: bool = False,
) -> ToolContext:
    return ToolContext(
        scope=ScopeGate(root),
        hooks=HookRunner(hooks),
        permission=permission,
        bus=bus or EventBus(),
        cwd=root,
        memory=memory,
        session_id="test-session",
        sandbox_shell=sandbox_shell,
    )


def call(reg: ToolRegistry, name: str, args: dict[str, Any], ctx: ToolContext) -> str:
    return asyncio.run(reg.call(name, args, ctx))


@pytest.fixture
def reg() -> ToolRegistry:
    return default_registry()


# ---------------------------------------------------------------- read_file


def test_read_file_numbered_lines(reg: ToolRegistry, tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("one\ntwo\nthree\n")
    out = call(reg, "read_file", {"path": "a.txt"}, make_ctx(tmp_path))
    lines = out.splitlines()
    assert "3 lines total" in lines[0]
    assert lines[1].endswith("1\tone")
    assert lines[3].endswith("3\tthree")


def test_read_file_offset_limit(reg: ToolRegistry, tmp_path: Path) -> None:
    (tmp_path / "b.txt").write_text("\n".join(f"line{i}" for i in range(1, 11)))
    out = call(reg, "read_file", {"path": "b.txt", "offset": 4, "limit": 3}, make_ctx(tmp_path))
    assert "showing lines 4-6" in out
    assert "\tline4" in out and "\tline6" in out
    assert "line7" not in out


def test_read_file_caps_at_2000_lines(reg: ToolRegistry, tmp_path: Path) -> None:
    (tmp_path / "big.txt").write_text("\n".join(f"x{i}" for i in range(1, 2501)))
    out = call(reg, "read_file", {"path": "big.txt"}, make_ctx(tmp_path))
    assert "\tx2000" in out
    assert "x2001" not in out
    assert "showing lines 1-2000" in out


def test_read_file_binary_detected(reg: ToolRegistry, tmp_path: Path) -> None:
    (tmp_path / "bin.dat").write_bytes(b"\x89\x00\x01binary")
    out = call(reg, "read_file", {"path": "bin.dat"}, make_ctx(tmp_path))
    assert "binary file" in out


def test_read_file_missing_and_offset_past_end(reg: ToolRegistry, tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    assert "not a file" in call(reg, "read_file", {"path": "nope.txt"}, ctx)
    (tmp_path / "s.txt").write_text("only\n")
    assert "past end of file" in call(reg, "read_file", {"path": "s.txt", "offset": 99}, ctx)


def test_read_file_outside_scope_raises(reg: ToolRegistry, tmp_path: Path) -> None:
    with pytest.raises(ScopeViolation):
        call(reg, "read_file", {"path": "/etc/passwd"}, make_ctx(tmp_path))


# --------------------------------------------------------------- write_file


def test_write_file_creates_parents_and_emits(reg: ToolRegistry, tmp_path: Path) -> None:
    bus = EventBus()
    out = call(
        reg,
        "write_file",
        {"path": "sub/dir/new.txt", "content": "hello\n"},
        make_ctx(tmp_path, bus=bus),
    )
    assert "wrote" in out
    assert (tmp_path / "sub/dir/new.txt").read_text() == "hello\n"
    ev = next(e for e in bus.history if e.kind == "file_changed")
    assert ev.data["action"] == "created"
    assert ev.data["path"].endswith("sub/dir/new.txt")


def test_write_file_denied_in_ask_mode(reg: ToolRegistry, tmp_path: Path) -> None:
    with pytest.raises(ScopeViolation):
        call(
            reg,
            "write_file",
            {"path": "x.txt", "content": "y"},
            make_ctx(tmp_path, permission=PermissionMode.ASK),
        )
    assert not (tmp_path / "x.txt").exists()


def test_write_file_outside_scope_raises(reg: ToolRegistry, tmp_path: Path) -> None:
    with pytest.raises(ScopeViolation):
        call(
            reg,
            "write_file",
            {"path": "../escape.txt", "content": "y"},
            make_ctx(tmp_path),
        )


# ---------------------------------------------------------------- edit_file


def test_edit_file_unique_match(reg: ToolRegistry, tmp_path: Path) -> None:
    bus = EventBus()
    (tmp_path / "e.txt").write_text("alpha\nbeta\ngamma\n")
    out = call(
        reg,
        "edit_file",
        {"path": "e.txt", "old_string": "beta", "new_string": "BETA\nEXTRA"},
        make_ctx(tmp_path, bus=bus),
    )
    assert "edited" in out
    assert (tmp_path / "e.txt").read_text() == "alpha\nBETA\nEXTRA\ngamma\n"
    ev = next(e for e in bus.history if e.kind == "file_changed")
    assert ev.data["diff_lines"] == 3  # 1 old line + 2 new lines


def test_edit_file_zero_and_multiple_matches(reg: ToolRegistry, tmp_path: Path) -> None:
    (tmp_path / "m.txt").write_text("dup\ndup\n")
    ctx = make_ctx(tmp_path)
    out = call(reg, "edit_file", {"path": "m.txt", "old_string": "nope", "new_string": "x"}, ctx)
    assert "not found" in out
    out = call(reg, "edit_file", {"path": "m.txt", "old_string": "dup", "new_string": "x"}, ctx)
    assert "matches 2 locations" in out
    assert (tmp_path / "m.txt").read_text() == "dup\ndup\n"  # unchanged


# --------------------------------------------------------------- list_files


def test_list_files_skips_junk_dirs(reg: ToolRegistry, tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub/b.py").write_text("b")
    for junk in (".git", "__pycache__", "node_modules"):
        (tmp_path / junk).mkdir()
        (tmp_path / junk / "junk.txt").write_text("junk")
    out = call(reg, "list_files", {}, make_ctx(tmp_path))
    lines = out.splitlines()
    assert "a.txt" in lines
    assert "sub/" in lines
    assert str(Path("sub/b.py")) in lines
    assert not any("junk" in line for line in lines)


def test_list_files_pattern_and_limit(reg: ToolRegistry, tmp_path: Path) -> None:
    for i in range(5):
        (tmp_path / f"f{i}.py").write_text("x")
    (tmp_path / "note.md").write_text("x")
    ctx = make_ctx(tmp_path)
    out = call(reg, "list_files", {"pattern": "*.py"}, ctx)
    assert "note.md" not in out
    assert "f0.py" in out
    out = call(reg, "list_files", {"limit": 2}, ctx)
    assert "truncated at 2 entries" in out


# ------------------------------------------------------------- search_files


def test_search_files_finds_matches(reg: ToolRegistry, tmp_path: Path) -> None:
    (tmp_path / "s1.py").write_text("def target_fn():\n    pass\n")
    (tmp_path / "s2.py").write_text("nothing here\n")
    out = call(reg, "search_files", {"pattern": "target_fn"}, make_ctx(tmp_path))
    assert "s1.py:1:" in out
    assert "s2.py" not in out


def test_search_files_no_matches(reg: ToolRegistry, tmp_path: Path) -> None:
    (tmp_path / "s.txt").write_text("hello\n")
    out = call(reg, "search_files", {"pattern": "zzz_nope"}, make_ctx(tmp_path))
    assert out.startswith("no matches")


def test_search_files_python_fallback(
    reg: ToolRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("pxx.tools.fs.shutil.which", lambda _name: None)
    (tmp_path / "f.py").write_text("alpha\nNEEDLE here\nomega\n")
    (tmp_path / "bin.dat").write_bytes(b"\x00NEEDLE binary")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git/g").write_text("NEEDLE in git\n")
    out = call(reg, "search_files", {"pattern": "NEEDLE"}, make_ctx(tmp_path))
    assert "f.py:2:" in out
    assert "bin.dat" not in out  # binary skipped
    assert ".git" not in out


def test_search_files_fallback_bad_regex(
    reg: ToolRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("pxx.tools.fs.shutil.which", lambda _name: None)
    out = call(reg, "search_files", {"pattern": "([unclosed"}, make_ctx(tmp_path))
    assert "invalid regex" in out


def test_search_files_fallback_limit(
    reg: ToolRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("pxx.tools.fs.shutil.which", lambda _name: None)
    (tmp_path / "many.txt").write_text("\n".join(f"hit{i}" for i in range(10)))
    out = call(reg, "search_files", {"pattern": "hit", "limit": 3}, make_ctx(tmp_path))
    assert out.count("many.txt") == 3
    assert "truncated at 3 matches" in out


def test_search_files_outside_scope_raises(reg: ToolRegistry, tmp_path: Path) -> None:
    with pytest.raises(ScopeViolation):
        call(reg, "search_files", {"pattern": "x", "path": "/etc"}, make_ctx(tmp_path))


# ----------------------------------------------------------------- run_shell


def test_run_shell_auto_echo_and_exit_code(reg: ToolRegistry, tmp_path: Path) -> None:
    out = call(reg, "run_shell", {"command": "echo hello"}, make_ctx(tmp_path))
    assert "hello" in out
    assert "[exit 0]" in out


def test_run_shell_nonzero_exit_and_stderr(reg: ToolRegistry, tmp_path: Path) -> None:
    out = call(reg, "run_shell", {"command": "echo oops >&2; exit 3"}, make_ctx(tmp_path))
    assert "oops" in out  # stderr merged into stdout
    assert "[exit 3]" in out


def test_run_shell_timeout(reg: ToolRegistry, tmp_path: Path) -> None:
    out = call(reg, "run_shell", {"command": "sleep 5", "timeout": 1}, make_ctx(tmp_path))
    assert "timed out after 1s" in out


def test_run_shell_output_capped(reg: ToolRegistry, tmp_path: Path) -> None:
    cmd = "head -c 40000 /dev/zero | tr '\\0' x"
    out = call(reg, "run_shell", {"command": cmd}, make_ctx(tmp_path))
    assert "output truncated at 32768 bytes" in out


@pytest.mark.parametrize("mode", [PermissionMode.ASK, PermissionMode.PLAN])
def test_run_shell_never_in_read_only_modes(
    reg: ToolRegistry, tmp_path: Path, mode: PermissionMode
) -> None:
    with pytest.raises(ScopeViolation, match="not permitted"):
        call(reg, "run_shell", {"command": "echo hi"}, make_ctx(tmp_path, permission=mode))


def test_run_shell_edit_mode_requires_hook(reg: ToolRegistry, tmp_path: Path) -> None:
    with pytest.raises(HooksMissing, match=r"PreToolUse hook.*docs/CONFIG.md"):
        call(
            reg,
            "run_shell",
            {"command": "echo hi"},
            make_ctx(tmp_path, permission=PermissionMode.EDIT),
        )


def test_run_shell_edit_mode_with_allowing_hook(reg: ToolRegistry, tmp_path: Path) -> None:
    ctx = make_ctx(
        tmp_path,
        permission=PermissionMode.EDIT,
        hooks=(Hook(event="PreToolUse", command="true", matcher="run_shell"),),
    )
    out = call(reg, "run_shell", {"command": "echo via-hook"}, ctx)
    assert "via-hook" in out


def test_run_shell_edit_mode_with_denying_hook(reg: ToolRegistry, tmp_path: Path) -> None:
    ctx = make_ctx(
        tmp_path,
        permission=PermissionMode.EDIT,
        hooks=(Hook(event="PreToolUse", command="false", matcher="run_shell"),),
    )
    with pytest.raises(HookDenied):
        call(reg, "run_shell", {"command": "echo hi"}, ctx)


def test_run_shell_runs_in_scope_root(reg: ToolRegistry, tmp_path: Path) -> None:
    out = call(reg, "run_shell", {"command": "pwd"}, make_ctx(tmp_path))
    assert str(Path(out.splitlines()[0]).resolve()) == str(tmp_path.resolve())


# ------------------------------------------------------- run_shell sandbox


def test_seatbelt_profile_denies_writes_outside_root(tmp_path: Path) -> None:
    profile = seatbelt_profile(tmp_path)
    assert "(deny file-write*)" in profile
    assert f'(subpath "{tmp_path}")' in profile


def test_sandbox_requested_but_no_sandboxer_falls_back(
    reg: ToolRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("pxx.tools.shell.shutil.which", lambda _name: None)
    ctx = make_ctx(tmp_path, sandbox_shell=True)
    out = call(reg, "run_shell", {"command": "echo unsandboxed"}, ctx)
    assert "unsandboxed" in out


@pytest.mark.skipif(
    sys.platform != "darwin" or not shutil.which("sandbox-exec"),
    reason="macOS sandbox-exec not available",
)
def test_sandbox_blocks_writes_outside_scope(reg: ToolRegistry, tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}_outside"
    outside.mkdir(exist_ok=True)
    probe = outside / "probe.txt"
    probe.unlink(missing_ok=True)
    try:
        ctx = make_ctx(tmp_path, sandbox_shell=True)
        out = call(reg, "run_shell", {"command": f"echo x > {probe}"}, ctx)
        assert "[exit 0]" not in out
        assert not probe.exists()
        # writes inside scope still work
        out = call(reg, "run_shell", {"command": "echo ok > inside.txt"}, ctx)
        assert (tmp_path / "inside.txt").read_text() == "ok\n"
    finally:
        probe.unlink(missing_ok=True)


# ------------------------------------------------------------ memory tools


@dataclass
class FakeObs:
    kind: str
    content: str


class FakeMemory:
    def __init__(self) -> None:
        self.items: list[FakeObs] = []
        self.add_calls: list[dict[str, Any]] = []

    def add(self, project: str, kind: str, content: str, **kwargs: Any) -> int:
        self.items.append(FakeObs(kind, content))
        self.add_calls.append({"project": project, "kind": kind, **kwargs})
        return len(self.items)

    def search(self, project: str, query: str, *, k: int = 8) -> list[FakeObs]:
        return self.items[:k]


def test_recall_and_remember_without_memory(reg: ToolRegistry, tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path, memory=None)
    assert "not available" in call(reg, "recall_memory", {"query": "x"}, ctx)
    assert "not available" in call(reg, "remember", {"content": "x"}, ctx)


def test_recall_memory_formats_results(reg: ToolRegistry, tmp_path: Path) -> None:
    mem = FakeMemory()
    mem.add("proj", "decision", "use sqlite for memory")
    mem.add("proj", "gotcha", "rg not always present")
    out = call(reg, "recall_memory", {"query": "memory", "k": 5}, make_ctx(tmp_path, memory=mem))
    assert "- [decision] use sqlite for memory" in out
    assert "- [gotcha] rg not always present" in out


def test_recall_memory_empty(reg: ToolRegistry, tmp_path: Path) -> None:
    out = call(reg, "recall_memory", {"query": "nothing"}, make_ctx(tmp_path, memory=FakeMemory()))
    assert out.startswith("no memories")


def test_remember_stores_with_tags_and_session(reg: ToolRegistry, tmp_path: Path) -> None:
    mem = FakeMemory()
    out = call(
        reg,
        "remember",
        {"content": "tests need no network", "tags": "testing, pxx"},
        make_ctx(tmp_path, memory=mem),
    )
    assert out == "remembered (id 1)"
    call_kwargs = mem.add_calls[0]
    assert call_kwargs["tags"] == ["testing", "pxx"]
    assert call_kwargs["session_id"] == "test-session"
    assert call_kwargs["source"] == "tool"
    assert call_kwargs["project"] == tmp_path.name


def test_remember_works_in_read_only_mode(reg: ToolRegistry, tmp_path: Path) -> None:
    mem = FakeMemory()
    ctx = make_ctx(tmp_path, permission=PermissionMode.ASK, memory=mem)
    out = call(reg, "remember", {"content": "read-only can still remember"}, ctx)
    assert out.startswith("remembered")
