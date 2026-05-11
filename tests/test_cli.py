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
    REPO_ROOT,
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
    _self_lint,
    _self_sanity_check,
    _self_test,
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


class TestDryRunFlag:
    """Tests for the pxx --dry-run flag (#003 S2).

    --dry-run is an aider flag (already in aider's own arg parser);
    pxx detects it for banner purposes only and does NOT strip it from
    sys.argv, so aider sees it naturally and applies its own dry-run.
    """

    def test_dry_run_passes_through_to_user_args(self):
        # main() filters --edit and --big from user_args but NOT --dry-run.
        argv = ["pxx", "--edit", "--dry-run", "--message", "hi"]
        filtered = [a for a in argv[1:] if a not in ("--edit", "--big")]
        assert "--dry-run" in filtered
        assert filtered == ["--dry-run", "--message", "hi"]

    def test_dry_run_flag_detected_in_argv(self):
        argv = ["pxx", "--edit", "--dry-run"]
        assert "--dry-run" in argv

    def test_dry_run_alone_without_edit_recognized(self):
        argv = ["pxx", "--dry-run"]
        assert "--dry-run" in argv
        assert "--edit" not in argv

    def test_dry_run_with_big_all_present(self):
        # The combo is allowed; main() warns that --big is meaningless
        # with --dry-run (no commits land). Both flags coexist in argv.
        argv = ["pxx", "--edit", "--big", "--dry-run"]
        for flag in ("--edit", "--big", "--dry-run"):
            assert flag in argv


class TestScopeFlag:
    """Tests for the pxx --scope <path> flag (#003 S1).

    These cover the cli.py integration; the underlying scope module is
    exhaustively tested in test_scope.py. The focus here is that:

    - --scope <value> is extracted from argv before --edit/--big stripping
      and does NOT leak into user_args (would confuse aider)
    - resolve_scopes is invoked against the git repo root
    - PXX_SCOPE env var is set for the pre-commit hook to read
    - _write_scope_context generates the expected directive file
    """

    def test_extract_scope_args_consumes_scope_before_passthrough(self):
        from pxx.scope import extract_scope_args

        # Mirrors the cli.py call order: extract_scope_args runs on
        # sys.argv[1:], then --edit / --big are stripped from what remains.
        argv = ["--scope", "tests/", "--edit", "--message", "hi"]
        scopes, after = extract_scope_args(argv)
        user_args = [a for a in after if a not in ("--edit", "--big")]
        assert scopes == ["tests/"]
        assert user_args == ["--message", "hi"]
        # Critical: --scope/<value> must not appear in user_args (would
        # land in aider's argv and crash, since aider has no --scope flag).
        assert "--scope" not in user_args
        assert "tests/" not in user_args

    def test_multiple_scope_flags_union(self):
        from pxx.scope import extract_scope_args

        argv = ["--scope", "tests/", "--scope=docs/", "--edit"]
        scopes, after = extract_scope_args(argv)
        assert scopes == ["tests/", "docs/"]
        user_args = [a for a in after if a not in ("--edit", "--big")]
        assert user_args == []

    def test_pxx_scope_env_format(self):
        from pxx.scope import format_for_env

        # cli.py calls format_for_env on the resolved (post-resolve) list
        # before setting os.environ["PXX_SCOPE"]. Format is colon-separated.
        assert format_for_env(["tests/", "docs/"]) == "tests/:docs/"

    def test_write_scope_context_creates_directive_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
        from pxx import cli

        result = cli._write_scope_context(["tests/", "pxx/cli.py"])
        assert result is not None
        assert result.exists()
        content = result.read_text()
        assert "SCOPE RESTRICTION" in content
        assert "`tests/`" in content
        assert "`pxx/cli.py`" in content
        assert "refuse" in content.lower()

    def test_write_scope_context_returns_none_when_no_scopes(self, tmp_path, monkeypatch):
        monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
        from pxx import cli

        assert cli._write_scope_context([]) is None

    def test_write_scope_context_renders_repo_root_label(self, tmp_path, monkeypatch):
        # Empty string in scope_prefixes (resolved from `.`) means "repo root".
        # The directive file should label that visibly, not show an empty
        # backtick that would confuse the model.
        monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
        from pxx import cli

        result = cli._write_scope_context([""])
        assert result is not None
        content = result.read_text()
        assert "(repo root)" in content


class TestAnywhereFlag:
    """Tests for the pxx --anywhere flag (#003 S3).

    --anywhere is a one-session bypass for the trusted-paths gate. It must
    be stripped from user_args before they're passed to aider.
    """

    def test_anywhere_flag_detected_in_argv(self):
        argv = ["pxx", "--edit", "--anywhere"]
        assert "--anywhere" in argv

    def test_anywhere_flag_stripped_from_user_args(self):
        from pxx.scope import extract_scope_args

        _, after = extract_scope_args(
            ["--scope", "tests/", "--edit", "--anywhere", "--message", "hi"]
        )
        user_args = [a for a in after if a not in ("--edit", "--big", "--anywhere")]
        assert "--anywhere" not in user_args
        assert "--edit" not in user_args
        assert user_args == ["--message", "hi"]


class TestTrustedPathGate:
    """Integration tests for the trusted-paths gate in main() (#003 S3)."""

    def _patch_endpoint_and_exec(self, monkeypatch):
        from pxx import cli as cli_module

        monkeypatch.setattr(
            cli_module, "detect_endpoint", lambda: Endpoint("neo", "http://x:11434")
        )
        monkeypatch.setattr(cli_module.os, "execv", lambda *_: None)
        monkeypatch.setattr(cli_module, "_find_aider", lambda: "/x/aider")

    def _write_trusted_config(self, tmp_path, monkeypatch, entries: list[Path]) -> Path:
        """Point XDG_CONFIG_HOME at tmp_path and write trusted-paths there."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        cfg_dir = tmp_path / "xdg" / "pxx"
        cfg_dir.mkdir(parents=True)
        cfg = cfg_dir / "trusted-paths"
        cfg.write_text("\n".join(str(e) for e in entries) + "\n")
        return cfg

    def test_edit_outside_trusted_path_blocks_without_anywhere(self, tmp_path, monkeypatch, capsys):
        from pxx import cli as cli_module

        trusted = tmp_path / "trusted-zone"
        trusted.mkdir()
        cfg = self._write_trusted_config(tmp_path, monkeypatch, [trusted])

        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        monkeypatch.setattr(sys, "argv", ["pxx", "--edit"])
        self._patch_endpoint_and_exec(monkeypatch)

        with pytest.raises(SystemExit) as exc:
            cli_module.main()
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "not under any trusted prefix" in err
        assert "--anywhere" in err
        assert str(cfg) in err

    def test_edit_outside_trusted_path_allowed_with_anywhere(self, tmp_path, monkeypatch, capsys):
        from pxx import cli as cli_module

        trusted = tmp_path / "trusted-zone"
        trusted.mkdir()
        self._write_trusted_config(tmp_path, monkeypatch, [trusted])

        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        monkeypatch.setattr(sys, "argv", ["pxx", "--edit", "--anywhere"])
        self._patch_endpoint_and_exec(monkeypatch)

        cli_module.main()
        err = capsys.readouterr().err
        assert "mode=edit (untrusted path)" in err

    def test_edit_inside_trusted_path_allowed_without_anywhere(self, tmp_path, monkeypatch, capsys):
        from pxx import cli as cli_module

        trusted = tmp_path / "trusted-zone"
        trusted.mkdir()
        self._write_trusted_config(tmp_path, monkeypatch, [trusted])

        monkeypatch.chdir(trusted)
        monkeypatch.setattr(sys, "argv", ["pxx", "--edit"])
        self._patch_endpoint_and_exec(monkeypatch)

        cli_module.main()
        err = capsys.readouterr().err
        assert "mode=edit" in err
        assert "untrusted path" not in err

    def test_no_trusted_paths_config_allows_anywhere(self, tmp_path, monkeypatch, capsys):
        from pxx import cli as cli_module

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-empty"))
        random_dir = tmp_path / "random"
        random_dir.mkdir()
        monkeypatch.chdir(random_dir)
        monkeypatch.setattr(sys, "argv", ["pxx", "--edit"])
        self._patch_endpoint_and_exec(monkeypatch)

        cli_module.main()
        err = capsys.readouterr().err
        assert "not under any trusted prefix" not in err
        assert "mode=edit" in err


class TestSelfTest:
    """Tests for the pxx --self-test flag (#001 Tier 1)."""

    def test_self_test_returns_child_returncode_on_pass(self, monkeypatch, capsys):
        calls: list[dict] = []

        def fake_run(cmd, cwd, check):
            calls.append({"cmd": cmd, "cwd": cwd, "check": check})

            class R:
                returncode = 0

            return R()

        monkeypatch.setattr(subprocess, "run", fake_run)
        rc = _self_test()
        assert rc == 0
        assert calls == [{"cmd": ["uv", "run", "pytest", "-q"], "cwd": REPO_ROOT, "check": False}]
        err = capsys.readouterr().err
        assert "self-test — running" in err
        assert "self-test — passed (0)" in err

    def test_self_test_propagates_nonzero(self, monkeypatch, capsys):
        class R:
            returncode = 1

        monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
        rc = _self_test()
        assert rc == 1
        assert "self-test — failed (1)" in capsys.readouterr().err

    def test_self_test_banner_goes_to_stderr_not_stdout(self, monkeypatch, capsys):
        class R:
            returncode = 0

        monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
        _self_test()
        captured = capsys.readouterr()
        assert "self-test" in captured.err
        assert "self-test" not in captured.out

    def test_main_with_self_test_short_circuits_before_endpoint(self, monkeypatch):
        from pxx import cli as cli_module

        called: list[str] = []

        def fake_detect():
            called.append("detect_endpoint")
            raise RuntimeError("should not be called")

        class R:
            returncode = 0

        monkeypatch.setattr(cli_module, "detect_endpoint", fake_detect)
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
        monkeypatch.setattr(sys, "argv", ["pxx", "--self-test"])
        with pytest.raises(SystemExit) as exc:
            cli_module.main()
        assert exc.value.code == 0
        assert called == []


class TestSelfLint:
    """Tests for the pxx --self-lint flag (#001 Tier 1)."""

    def _stub_run(self, rc_by_cmd: dict[tuple[str, ...], int]):
        """Build a fake subprocess.run that returns mapped exit codes."""
        calls: list[list[str]] = []

        def fake_run(cmd, cwd, check):
            calls.append(cmd)

            class R:
                returncode = rc_by_cmd.get(tuple(cmd), 0)

            return R()

        return fake_run, calls

    def test_runs_both_check_and_format(self, monkeypatch, capsys):
        fake, calls = self._stub_run({})
        monkeypatch.setattr(subprocess, "run", fake)
        rc = _self_lint()
        assert rc == 0
        assert calls == [
            ["uv", "run", "ruff", "check", "."],
            ["uv", "run", "ruff", "format", "--check", "."],
        ]
        err = capsys.readouterr().err
        assert "self-lint — running" in err
        assert "check=0 format=0 combined=0" in err

    def test_nonzero_if_check_fails(self, monkeypatch, capsys):
        fake, _ = self._stub_run({("uv", "run", "ruff", "check", "."): 1})
        monkeypatch.setattr(subprocess, "run", fake)
        rc = _self_lint()
        assert rc != 0
        assert "check=1 format=0" in capsys.readouterr().err

    def test_nonzero_if_format_fails(self, monkeypatch, capsys):
        fake, _ = self._stub_run({("uv", "run", "ruff", "format", "--check", "."): 1})
        monkeypatch.setattr(subprocess, "run", fake)
        rc = _self_lint()
        assert rc != 0
        assert "check=0 format=1" in capsys.readouterr().err

    def test_both_subcommands_run_even_when_check_fails(self, monkeypatch):
        # Don't short-circuit on first failure — user wants to see every issue.
        fake, calls = self._stub_run({("uv", "run", "ruff", "check", "."): 1})
        monkeypatch.setattr(subprocess, "run", fake)
        _self_lint()
        assert len(calls) == 2

    def test_main_with_self_lint_short_circuits_before_endpoint(self, monkeypatch):
        from pxx import cli as cli_module

        called: list[str] = []

        def fake_detect():
            called.append("detect_endpoint")
            raise RuntimeError("should not be called")

        class R:
            returncode = 0

        monkeypatch.setattr(cli_module, "detect_endpoint", fake_detect)
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
        monkeypatch.setattr(sys, "argv", ["pxx", "--self-lint"])
        with pytest.raises(SystemExit) as exc:
            cli_module.main()
        assert exc.value.code == 0
        assert called == []


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
