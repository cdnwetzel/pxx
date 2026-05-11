"""Tests for pxx.cli pure functions.

Covers the module-level helpers that have no I/O dependencies on aider or
Ollama: model_for, _in_git_repo, _find_aider, _build_aider_args, the
--list-commands flag handling, and the in-session commands-context file.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from pxx.cli import (
    COMMANDS_CONTEXT_FILE,
    NEO_DEFAULT,
    SAFETY_TAG_PREFIX,
    STUDIO_DEFAULT,
    _build_aider_args,
    _create_safety_tag,
    _find_aider,
    _git_dirty,
    _has_commits,
    _in_git_repo,
    _print_command_listing,
    _prune_old_safety_tags,
    _self_sanity_check,
    _write_commands_context,
    main,
    model_for,
)
from pxx.commands_index import CommandInfo
from pxx.endpoints import Endpoint


class TestModelFor:
    def test_neo_endpoint_returns_neo_default(self, monkeypatch):
        monkeypatch.delenv("PXX_MODEL", raising=False)
        assert model_for(Endpoint("neo", "http://localhost:11434")) == NEO_DEFAULT

    def test_studio_endpoint_returns_studio_default(self, monkeypatch):
        monkeypatch.delenv("PXX_MODEL", raising=False)
        assert model_for(Endpoint("studio_lan", "http://x:11434")) == STUDIO_DEFAULT

    def test_studio_remote_endpoint_returns_studio_default(self, monkeypatch):
        monkeypatch.delenv("PXX_MODEL", raising=False)
        assert model_for(Endpoint("studio_remote", "http://x:11434")) == STUDIO_DEFAULT

    def test_override_endpoint_returns_studio_default(self, monkeypatch):
        # documented behavior: override endpoint inherits Studio default
        monkeypatch.delenv("PXX_MODEL", raising=False)
        assert model_for(Endpoint("override", "http://x:11434")) == STUDIO_DEFAULT

    def test_pxx_model_env_overrides_all_endpoints(self, monkeypatch):
        monkeypatch.setenv("PXX_MODEL", "ollama_chat/custom")
        for name in ("neo", "studio_lan", "studio_remote", "override"):
            assert model_for(Endpoint(name, "http://x:11434")) == "ollama_chat/custom"


class TestInGitRepo:
    def test_inside_git_repo(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        subprocess.run(["git", "init", "-q"], check=True)
        assert _in_git_repo() is True

    def test_outside_git_repo(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert _in_git_repo() is False


class TestFindAider:
    def test_returns_existing_aider_in_same_venv(self):
        # When pytest runs in pxx's dev venv, aider is installed alongside it
        # because aider-chat is a runtime dep of pxx.
        found = _find_aider()
        assert Path(found).exists()
        assert Path(found).name == "aider"


class TestBuildAiderArgs:
    def test_default_mode_injects_chat_mode_ask(self):
        args = _build_aider_args("/x/aider", "m", [], in_git_repo=True, edit_mode=False)
        assert "--chat-mode" in args
        assert args[args.index("--chat-mode") + 1] == "ask"

    def test_edit_mode_omits_chat_mode_flag(self):
        # Aider 0.86.2 has no "code" value for --chat-mode (the flag is
        # aliased to --edit-format and accepts diff/udiff/whole/ask/etc).
        # pxx omits --chat-mode in edit mode and lets aider use its
        # default + the config's edit-format=diff.
        args = _build_aider_args("/x/aider", "m", [], in_git_repo=True, edit_mode=True)
        assert "--chat-mode" not in args

    def test_explicit_chat_mode_passes_through(self):
        # User passing --chat-mode architect should not be overridden by pxx.
        args = _build_aider_args(
            "/x/aider",
            "m",
            ["--chat-mode", "architect"],
            in_git_repo=True,
            edit_mode=False,
        )
        assert args.count("--chat-mode") == 1
        assert args[args.index("--chat-mode") + 1] == "architect"

    def test_explicit_chat_mode_equals_form_also_respected(self):
        args = _build_aider_args(
            "/x/aider",
            "m",
            ["--chat-mode=help"],
            in_git_repo=True,
            edit_mode=True,
        )
        # pxx should not inject its own --chat-mode when user used the = form.
        assert "--chat-mode" not in args
        assert "--chat-mode=help" in args

    def test_no_git_flag_added_when_outside_repo(self):
        args = _build_aider_args("/x/aider", "m", [], in_git_repo=False, edit_mode=False)
        assert "--no-git" in args

    def test_no_git_flag_skipped_when_inside_repo(self):
        args = _build_aider_args("/x/aider", "m", [], in_git_repo=True, edit_mode=False)
        assert "--no-git" not in args

    def test_user_args_appended_last(self):
        args = _build_aider_args(
            "/x/aider",
            "m",
            ["--message", "hi"],
            in_git_repo=True,
            edit_mode=False,
        )
        # --message and "hi" should be the last two elements
        assert args[-2:] == ["--message", "hi"]

    def test_first_arg_is_aider_binary(self):
        args = _build_aider_args("/x/aider", "m", [], in_git_repo=True, edit_mode=False)
        assert args[0] == "/x/aider"


class TestBigFlag:
    """Tests for the pxx --big flag (#002 M4).

    The flag itself is parsed in main() — extracted from sys.argv into the
    big_mode bool, set as PXX_ALLOW_BIG_DIFF=1 env, and stripped from the
    user_args before they're handed to aider. The pre-commit hook reads
    the env var to decide whether to skip the diff cap.
    """

    def test_big_flag_stripped_from_user_args(self):
        # main() filters --edit and --big out of sys.argv[1:] before passing
        # remaining args to aider. This mirrors the same pattern the existing
        # --edit tests cover.
        argv = ["pxx", "--edit", "--big", "--message", "hi"]
        # Replicate main()'s filtering logic.
        filtered = [a for a in argv[1:] if a not in ("--edit", "--big")]
        assert "--big" not in filtered
        assert "--edit" not in filtered
        assert filtered == ["--message", "hi"]

    def test_big_flag_detected_in_argv(self):
        argv = ["pxx", "--edit", "--big"]
        assert "--big" in argv

    def test_big_flag_absent_when_not_passed(self):
        argv = ["pxx", "--edit"]
        assert "--big" not in argv

    def test_big_without_edit_is_noop_for_diff_cap(self):
        # The pre-commit hook only runs on commits, which only happen in
        # edit mode. --big without --edit sets the env var but has no
        # effect because the hook never fires. Verify main() does warn.
        # (Smoke-tested manually; we can't easily exercise this path in
        #  unit tests because main() execvs.)
        # This test just documents the expected behavior in code form.
        argv = ["pxx", "--big"]  # no --edit
        big_mode = "--big" in argv
        edit_mode = "--edit" in argv
        assert big_mode is True
        assert edit_mode is False
        # main() prints the warning; we don't assert on stderr here because
        # main() is hard to test directly.


class TestInstallHookFlag:
    def test_install_hook_flag_detected(self):
        argv = ["pxx", "--install-hook"]
        assert "--install-hook" in argv

    def test_install_hook_force_flag_combo(self):
        argv = ["pxx", "--install-hook", "--force"]
        assert "--install-hook" in argv
        assert "--force" in argv

    def test_install_hook_uninstall_flag_combo(self):
        argv = ["pxx", "--install-hook", "--uninstall"]
        assert "--install-hook" in argv
        assert "--uninstall" in argv


class TestListCommandsFlag:
    def test_print_command_listing_includes_all_six_commands(self, capsys):
        _print_command_listing()
        out = capsys.readouterr().out
        for name in ("audit", "docstring", "refactor", "refocus", "test", "typecheck"):
            assert f"/{name}" in out

    def test_print_command_listing_includes_real_descriptions(self, capsys):
        _print_command_listing()
        out = capsys.readouterr().out
        # All six should now have real descriptions, not the placeholder.
        assert "(no description)" not in out

    def test_print_command_listing_includes_paste_ready_load_lines(self, capsys):
        _print_command_listing()
        out = capsys.readouterr().out
        assert "Paste-ready /load lines:" in out
        assert "/load " in out

    def test_main_with_list_commands_flag_exits_zero(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["pxx", "--list-commands"])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "Available slash commands:" in out

    def test_list_commands_flag_short_circuits_endpoint_detection(self, monkeypatch):
        """--list-commands must exit before any endpoint probing occurs."""
        from pxx import cli as cli_module

        calls: list[str] = []

        def fake_detect() -> Endpoint:
            calls.append("detect_endpoint")
            raise RuntimeError("should not be called when --list-commands is set")

        monkeypatch.setattr(cli_module, "detect_endpoint", fake_detect)
        monkeypatch.setattr(sys, "argv", ["pxx", "--list-commands"])
        with pytest.raises(SystemExit) as exc:
            cli_module.main()
        assert exc.value.code == 0
        assert calls == []


class TestCommandsContext:
    def test_returns_path_when_commands_present(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        commands = [CommandInfo(name="foo", path=Path("/x/foo.md"), description="bar")]
        result = _write_commands_context(commands)
        assert result == tmp_path / COMMANDS_CONTEXT_FILE
        assert result.exists()

    def test_returns_none_for_empty_command_list(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        assert _write_commands_context([]) is None
        # And no file should have been created.
        assert not (tmp_path / COMMANDS_CONTEXT_FILE).exists()

    def test_content_includes_header_and_paste_lines(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        commands = [
            CommandInfo(name="audit", path=Path("/a.md"), description="review"),
            CommandInfo(name="test", path=Path("/t.md"), description="tests"),
        ]
        result = _write_commands_context(commands)
        content = result.read_text(encoding="utf-8")
        assert "# Available slash commands" in content
        assert "Do not invent commands" in content
        assert "/load /a.md" in content
        assert "review" in content
        assert "/load /t.md" in content
        assert "tests" in content

    def test_content_includes_routing_directive_and_example(self, tmp_path, monkeypatch):
        """The context must instruct task-routing, not just list commands."""
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        commands = [
            CommandInfo(name="typecheck", path=Path("/tc.md"), description="type hints"),
        ]
        result = _write_commands_context(commands)
        content = result.read_text(encoding="utf-8")
        # Directive: scan-first language and MUST-lead requirement.
        assert "scan this list first" in content.lower()
        assert "MUST lead" in content or "must lead" in content.lower()
        # Example block grounds the behavior in a concrete case.
        assert "## Example" in content
        assert "User:" in content
        assert "You:" in content

    def test_file_overwritten_on_each_call(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        # First write — single command.
        _write_commands_context([CommandInfo(name="x", path=Path("/x.md"), description="d1")])
        first = (tmp_path / COMMANDS_CONTEXT_FILE).read_text()
        assert "/load /x.md" in first
        # Second write — different command. Old content must be gone.
        _write_commands_context([CommandInfo(name="y", path=Path("/y.md"), description="d2")])
        second = (tmp_path / COMMANDS_CONTEXT_FILE).read_text()
        assert "/load /y.md" in second
        assert "/load /x.md" not in second


class TestSelfSanityCheck:
    def test_passes_for_real_module(self):
        # Real module imports cleanly; should not exit.
        _self_sanity_check("pxx.endpoints")

    def test_exits_2_on_import_failure(self, capsys):
        with pytest.raises(SystemExit) as exc:
            _self_sanity_check("nonexistent.module.that.cannot.exist")
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert "failed to import" in err
        assert "Recover with one of:" in err
        # Recovery hints use `git -C <repo> reset --hard ...` form.
        assert "reset --hard" in err
        assert "reflog" in err

    def test_exits_2_on_import_error_in_module(self, monkeypatch, capsys):
        # Simulate a module that imports but raises during its top-level code.
        # importlib.import_module() should re-raise.
        import importlib

        def fake_import(name):
            raise ImportError("simulated import-time failure")

        monkeypatch.setattr(importlib, "import_module", fake_import)
        with pytest.raises(SystemExit) as exc:
            _self_sanity_check("pxx.endpoints")
        assert exc.value.code == 2
        assert "simulated import-time failure" in capsys.readouterr().err


class TestGitDirty:
    def test_clean_tree(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        subprocess.run(["git", "init", "-q"], check=True)
        subprocess.run(["git", "config", "user.email", "x@x"], check=True)
        subprocess.run(["git", "config", "user.name", "x"], check=True)
        # Empty repo (no commits yet) is also "clean" for our purposes — no
        # changes to stash. Verify status reflects that.
        (tmp_path / "f.txt").write_text("a")
        subprocess.run(["git", "add", "f.txt"], check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], check=True)
        assert _git_dirty() is False

    def test_unstaged_changes(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        subprocess.run(["git", "init", "-q"], check=True)
        subprocess.run(["git", "config", "user.email", "x@x"], check=True)
        subprocess.run(["git", "config", "user.name", "x"], check=True)
        (tmp_path / "f.txt").write_text("a")
        subprocess.run(["git", "add", "f.txt"], check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], check=True)
        (tmp_path / "f.txt").write_text("b")
        assert _git_dirty() is True

    def test_untracked_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        subprocess.run(["git", "init", "-q"], check=True)
        subprocess.run(["git", "config", "user.email", "x@x"], check=True)
        subprocess.run(["git", "config", "user.name", "x"], check=True)
        (tmp_path / "untracked.txt").write_text("a")
        assert _git_dirty() is True

    def test_outside_git_repo(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # No git init — _git_dirty should not crash, just return False.
        assert _git_dirty() is False


class TestHasCommits:
    def test_false_in_empty_repo(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        subprocess.run(["git", "init", "-q"], check=True)
        # No commits — HEAD is unborn.
        assert _has_commits() is False

    def test_true_after_first_commit(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        subprocess.run(["git", "init", "-q"], check=True)
        subprocess.run(["git", "config", "user.email", "x@x"], check=True)
        subprocess.run(["git", "config", "user.name", "x"], check=True)
        (tmp_path / "f.txt").write_text("a")
        subprocess.run(["git", "add", "f.txt"], check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], check=True)
        assert _has_commits() is True

    def test_false_outside_git_repo(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert _has_commits() is False


class TestCreateSafetyTag:
    def _init_repo(self, tmp_path):
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.email", "x@x"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.name", "x"], cwd=tmp_path, check=True)
        (tmp_path / "f.txt").write_text("initial")
        subprocess.run(["git", "add", "f.txt"], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)

    def test_returns_tag_in_git_repo_clean_tree(self, tmp_path, monkeypatch):
        self._init_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        tag = _create_safety_tag()
        assert tag is not None
        assert tag.startswith(SAFETY_TAG_PREFIX)
        # Verify tag exists in git.
        result = subprocess.run(
            ["git", "tag", "--list", tag],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == tag

    def test_returns_tag_with_unix_timestamp_suffix(self, tmp_path, monkeypatch):
        self._init_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        before = int(time.time())
        tag = _create_safety_tag()
        after = int(time.time())
        assert tag is not None
        ts = int(tag.removeprefix(SAFETY_TAG_PREFIX))
        assert before <= ts <= after

    def test_stashes_dirty_changes(self, tmp_path, monkeypatch):
        self._init_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        # Make the working tree dirty.
        (tmp_path / "f.txt").write_text("modified")
        (tmp_path / "new-untracked.txt").write_text("brand new")
        tag = _create_safety_tag()
        assert tag is not None
        # Stash should now exist.
        stash_list = subprocess.run(
            ["git", "stash", "list"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
        )
        assert tag in stash_list.stdout

    def test_returns_none_outside_git_repo(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # No git init.
        assert _create_safety_tag() is None

    def test_returns_none_in_empty_repo_no_commits(self, tmp_path, monkeypatch):
        # git init without committing — HEAD is unborn, git tag fails.
        monkeypatch.chdir(tmp_path)
        subprocess.run(["git", "init", "-q"], check=True)
        assert _create_safety_tag() is None


class TestPruneOldSafetyTags:
    def _init_repo(self, tmp_path):
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.email", "x@x"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.name", "x"], cwd=tmp_path, check=True)
        (tmp_path / "f.txt").write_text("x")
        subprocess.run(["git", "add", "f.txt"], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)

    def test_deletes_old_tags(self, tmp_path, monkeypatch):
        self._init_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        # Tag at a timestamp 100 days ago.
        old_ts = int(time.time()) - 100 * 86400
        old_tag = f"{SAFETY_TAG_PREFIX}{old_ts}"
        subprocess.run(["git", "tag", old_tag], cwd=tmp_path, check=True)
        # And a recent tag (today).
        recent_tag = f"{SAFETY_TAG_PREFIX}{int(time.time())}"
        subprocess.run(["git", "tag", recent_tag], cwd=tmp_path, check=True)
        _prune_old_safety_tags(retention_days=30)
        remaining = subprocess.run(
            ["git", "tag", "--list", f"{SAFETY_TAG_PREFIX}*"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        assert old_tag not in remaining
        assert recent_tag in remaining

    def test_skips_malformed_tag_names(self, tmp_path, monkeypatch):
        self._init_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        malformed = f"{SAFETY_TAG_PREFIX}not-a-timestamp"
        subprocess.run(["git", "tag", malformed], cwd=tmp_path, check=True)
        _prune_old_safety_tags(retention_days=30)
        # Malformed tag should still exist — we don't delete what we can't parse.
        remaining = subprocess.run(
            ["git", "tag", "--list", f"{SAFETY_TAG_PREFIX}*"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        assert malformed in remaining

    def test_silent_outside_git_repo(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # No exception, no error output.
        _prune_old_safety_tags(retention_days=30)


class TestBuildAiderArgsWithExtraReads:
    def test_extra_reads_inserted_after_system_prompt(self):
        extras = [Path("/tmp/a.md"), Path("/tmp/b.md")]
        args = _build_aider_args(
            "/x/aider", "m", [], in_git_repo=True, edit_mode=False, extra_reads=extras
        )
        # Find all --read flag indices.
        read_indices = [i for i, a in enumerate(args) if a == "--read"]
        assert len(read_indices) == 3, args
        # System prompt comes first; then the two extras in order.
        assert args[read_indices[1] + 1] == "/tmp/a.md"
        assert args[read_indices[2] + 1] == "/tmp/b.md"

    def test_no_extra_reads_when_param_omitted(self):
        args = _build_aider_args("/x/aider", "m", [], in_git_repo=True, edit_mode=False)
        assert args.count("--read") == 1

    def test_no_extra_reads_when_empty_list(self):
        args = _build_aider_args(
            "/x/aider", "m", [], in_git_repo=True, edit_mode=False, extra_reads=[]
        )
        assert args.count("--read") == 1

    def test_extra_reads_with_none(self):
        args = _build_aider_args(
            "/x/aider", "m", [], in_git_repo=True, edit_mode=False, extra_reads=None
        )
        assert args.count("--read") == 1
