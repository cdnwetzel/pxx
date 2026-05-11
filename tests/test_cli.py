"""Tests for pxx.cli pure functions.

Covers the module-level helpers that have no I/O dependencies on aider or
Ollama: model_for, _in_git_repo, _find_aider, _build_aider_args, and the
--list-commands flag handling.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from pxx.cli import (
    NEO_DEFAULT,
    STUDIO_DEFAULT,
    _build_aider_args,
    _find_aider,
    _in_git_repo,
    _print_command_listing,
    main,
    model_for,
)
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
    def test_default_mode_is_ask(self):
        args = _build_aider_args("/x/aider", "m", [], in_git_repo=True, edit_mode=False)
        assert "--chat-mode" in args
        assert args[args.index("--chat-mode") + 1] == "ask"

    def test_edit_mode_is_code(self):
        args = _build_aider_args("/x/aider", "m", [], in_git_repo=True, edit_mode=True)
        assert args[args.index("--chat-mode") + 1] == "code"

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
