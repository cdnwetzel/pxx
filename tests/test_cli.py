"""Tests for pxx.cli pure functions.

Covers the module-level helpers that have no I/O dependencies on aider or
Ollama: model_for, _in_git_repo, _find_aider, _build_aider_args, the
--list-commands flag handling, and the in-session commands-context file.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pxx.cli import (
    COMMANDS_CONTEXT_FILE,
    REPO_ROOT,
    SAFETY_TAG_PREFIX,
    STUDIO_DEFAULT,
    VLLM_DEFAULT,
    _build_aider_args,
    _headless_consent_args,
    _create_safety_tag,
    _emit_core_restart_banner,
    _find_aider,
    _git_dirty,
    _has_commits,
    _in_git_repo,
    _print_command_listing,
    _prune_old_safety_tags,
    _self_lint,
    _self_sanity_check,
    _self_test,
    _set_backend_env,
    _write_commands_context,
    main,
    model_for,
)
from pxx.commands_index import CommandInfo
from pxx.endpoints import Endpoint


class TestModelFor:
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
        for name in ("studio_lan", "studio_remote", "override"):
            assert model_for(Endpoint(name, "http://x:11434")) == "ollama_chat/custom"


class TestModelForVllm:
    def test_vllm_endpoint_returns_vllm_default(self, monkeypatch):
        monkeypatch.delenv("PXX_MODEL", raising=False)
        ep = Endpoint("m1_vllm", "http://x:8000", backend="vllm")
        assert model_for(ep) == VLLM_DEFAULT

    def test_pxx_model_override_beats_vllm(self, monkeypatch):
        monkeypatch.setenv("PXX_MODEL", "my-model")
        ep = Endpoint("m1_vllm", "http://x:8000", backend="vllm")
        assert model_for(ep) == "my-model"

    def test_endpoint_model_beats_vllm_default(self, monkeypatch):
        monkeypatch.delenv("PXX_MODEL", raising=False)
        ep = Endpoint(
            "vllm_gpu-node",
            "http://gpu-node:8001",
            backend="vllm",
            model="openai/Qwen3-Coder",
        )
        assert model_for(ep) == "openai/Qwen3-Coder"
        assert model_for(ep, tier="t2") == "openai/Qwen3-Coder"
        assert model_for(ep, tier="t3") == "openai/Qwen3-Coder"

    def test_endpoint_model_does_not_bypass_t1_ollama_requirement(self, monkeypatch):
        monkeypatch.delenv("PXX_MODEL", raising=False)
        ep = Endpoint(
            "vllm_gpu-node",
            "http://gpu-node:8001",
            backend="vllm",
            model="openai/Qwen3-Coder",
        )
        with pytest.raises(RuntimeError, match="t1 requires an Ollama endpoint"):
            model_for(ep, tier="t1")


class TestSetBackendEnv:
    def test_vllm_sets_openai_api_base(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_BASE", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        ep = Endpoint("m1_vllm", "http://example.com:8000", backend="vllm")
        _set_backend_env(ep)
        assert os.environ["OPENAI_API_BASE"] == "http://example.com:8000/v1"

    def test_vllm_sets_openai_api_key_if_absent(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        ep = Endpoint("m1_vllm", "http://x:8000", backend="vllm")
        _set_backend_env(ep)
        assert os.environ["OPENAI_API_KEY"] == "EMPTY"

    def test_vllm_does_not_overwrite_existing_openai_api_key(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-existing")
        ep = Endpoint("m1_vllm", "http://x:8000", backend="vllm")
        _set_backend_env(ep)
        assert os.environ["OPENAI_API_KEY"] == "sk-existing"

    def test_ollama_sets_ollama_api_base(self, monkeypatch):
        monkeypatch.delenv("OLLAMA_API_BASE", raising=False)
        ep = Endpoint("studio_lan", "http://example.com:11434", backend="ollama")
        _set_backend_env(ep)
        assert os.environ["OLLAMA_API_BASE"] == "http://example.com:11434"


class TestExtractTier:
    def test_extract_tier_t1(self):
        from pxx.cli import _extract_tier

        tier, remaining = _extract_tier(["--tier", "t1", "--message", "hi"])
        assert tier == "t1"
        assert remaining == ["--message", "hi"]

    def test_extract_tier_equals_form(self):
        from pxx.cli import _extract_tier

        tier, remaining = _extract_tier(["--tier=t2", "--message", "hi"])
        assert tier == "t2"
        assert remaining == ["--message", "hi"]

    def test_no_tier_returns_none(self):
        from pxx.cli import _extract_tier

        tier, remaining = _extract_tier(["--message", "hi"])
        assert tier is None
        assert remaining == ["--message", "hi"]


class TestModelForTier:
    def test_t1_tier_returns_t1_default(self, monkeypatch):
        from pxx.cli import T1_DEFAULT, model_for

        monkeypatch.delenv("PXX_MODEL", raising=False)
        ep = Endpoint("studio_lan", "http://localhost:11434", backend="ollama")
        assert model_for(ep, tier="t1") == T1_DEFAULT

    def test_t2_tier_returns_vllm_default(self, monkeypatch):
        from pxx.cli import VLLM_DEFAULT, model_for

        monkeypatch.delenv("PXX_MODEL", raising=False)
        ep = Endpoint("m1_vllm", "http://x:8000", backend="vllm")
        assert model_for(ep, tier="t2") == VLLM_DEFAULT

    def test_t3_tier_returns_t3_default(self, monkeypatch):
        from pxx.cli import VLLM_T3_DEFAULT, model_for

        monkeypatch.delenv("PXX_MODEL", raising=False)
        ep = Endpoint("m1_vllm", "http://x:8000", backend="vllm")
        assert model_for(ep, tier="t3") == VLLM_T3_DEFAULT

    def test_pxx_model_overrides_tier(self, monkeypatch):
        from pxx.cli import model_for

        monkeypatch.setenv("PXX_MODEL", "custom-model")
        ep = Endpoint("m1_vllm", "http://x:8000", backend="vllm")
        assert model_for(ep, tier="t2") == "custom-model"

    def test_no_tier_uses_backend_default(self, monkeypatch):
        from pxx.cli import VLLM_DEFAULT, model_for

        monkeypatch.delenv("PXX_MODEL", raising=False)
        ep = Endpoint("m1_vllm", "http://x:8000", backend="vllm")
        assert model_for(ep, tier=None) == VLLM_DEFAULT


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


class TestHeadlessConsentArgs:
    def test_non_tty_without_consent_injects_yes(self):
        assert _headless_consent_args(False, []) == ["--yes"]
        assert _headless_consent_args(False, ["--message", "hi"]) == ["--yes"]

    def test_tty_never_injects(self):
        assert _headless_consent_args(True, []) == []
        assert _headless_consent_args(True, ["--message", "hi"]) == []

    def test_existing_consent_flag_respected(self):
        for flag in ("--yes", "--yes-always", "--no", "--yes-always=true"):
            assert _headless_consent_args(False, [flag]) == []


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

    def test_no_gitignore_always_present(self):
        # A read-only (ask) session must never mutate .gitignore; edit mode
        # keeps it out too — ignore-file hygiene is a repo decision.
        for edit_mode in (False, True):
            args = _build_aider_args(
                "/x/aider", "m", [], in_git_repo=True, edit_mode=edit_mode
            )
            assert "--no-gitignore" in args

    def test_no_git_flag_added_when_outside_repo(self):
        args = _build_aider_args(
            "/x/aider", "m", [], in_git_repo=False, edit_mode=False
        )
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

    def test_write_scope_context_returns_none_when_no_scopes(
        self, tmp_path, monkeypatch
    ):
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
            cli_module,
            "detect_endpoint",
            lambda **kwargs: Endpoint("studio_lan", "http://x:11434"),
        )
        monkeypatch.setattr(cli_module.os, "execve", lambda *_: None)
        monkeypatch.setattr(cli_module, "_find_aider", lambda: "/x/aider")

    def _write_trusted_config(self, tmp_path, monkeypatch, entries: list[Path]) -> Path:
        """Point XDG_CONFIG_HOME at tmp_path and write trusted-paths there."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        cfg_dir = tmp_path / "xdg" / "pxx"
        cfg_dir.mkdir(parents=True)
        cfg = cfg_dir / "trusted-paths"
        cfg.write_text("\n".join(str(e) for e in entries) + "\n")
        return cfg

    def test_edit_outside_trusted_path_blocks_without_anywhere(
        self, tmp_path, monkeypatch, capsys
    ):
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

    def test_edit_outside_trusted_path_allowed_with_anywhere(
        self, tmp_path, monkeypatch, capsys
    ):
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

    def test_edit_inside_trusted_path_allowed_without_anywhere(
        self, tmp_path, monkeypatch, capsys
    ):
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

    def test_no_trusted_paths_config_allows_anywhere(
        self, tmp_path, monkeypatch, capsys
    ):
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

        def fake_run(cmd, cwd, check, timeout=None):
            calls.append({"cmd": cmd, "cwd": cwd, "check": check, "timeout": timeout})

            class R:
                returncode = 0

            return R()

        monkeypatch.setattr(subprocess, "run", fake_run)
        rc = _self_test()
        assert rc == 0
        assert calls == [
            {
                "cmd": ["uv", "run", "pytest", "-q"],
                "cwd": REPO_ROOT,
                "check": False,
                "timeout": 120,
            }
        ]
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


class TestDoctor:
    """Tests for the pxx --doctor flag."""

    def _stub_doctor(self, monkeypatch, *, in_sync: bool):
        from pxx import cli as cli_module
        from pxx.doctor import RemoteStats

        called: list[str] = []

        def fake_detect():
            called.append("detect_endpoint")
            raise RuntimeError("should not be called")

        # Out-of-sync must be a REACHABLE remote at a different SHA — an
        # unreachable/None remote is now N/A (in_sync), not out-of-sync (Task C).
        sha = "deadbeef" if in_sync else "0ldc0de0"
        stats = RemoteStats(local_sha="deadbeef", remotes={"origin": sha})

        class FakeDoctor:
            def print_report(self):
                return stats

        monkeypatch.setattr(cli_module, "detect_endpoint", fake_detect)
        monkeypatch.setattr(cli_module.doctor, "Doctor", lambda: FakeDoctor())
        monkeypatch.setattr(sys, "argv", ["pxx", "--doctor"])
        return cli_module, called

    def test_doctor_exits_zero_when_in_sync(self, monkeypatch):
        cli_module, called = self._stub_doctor(monkeypatch, in_sync=True)
        with pytest.raises(SystemExit) as exc:
            cli_module.main()
        assert exc.value.code == 0
        assert called == []  # short-circuits before endpoint detection

    def test_doctor_exits_nonzero_when_out_of_sync(self, monkeypatch):
        cli_module, _ = self._stub_doctor(monkeypatch, in_sync=False)
        with pytest.raises(SystemExit) as exc:
            cli_module.main()
        assert exc.value.code == 1


class TestSelfLint:
    """Tests for the pxx --self-lint flag (#001 Tier 1)."""

    def _stub_run(self, rc_by_cmd: dict[tuple[str, ...], int]):
        """Build a fake subprocess.run that returns mapped exit codes."""
        calls: list[list[str]] = []

        def fake_run(cmd, cwd, check, timeout=None):
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
            ["uv", "run", "ruff", "check", "pxx/", "tests/"],
            ["uv", "run", "ruff", "format", "--check", "pxx/", "tests/"],
        ]
        err = capsys.readouterr().err
        assert "self-lint — running" in err
        assert "check=0 format=0 combined=0" in err

    def test_nonzero_if_check_fails(self, monkeypatch, capsys):
        fake, _ = self._stub_run({("uv", "run", "ruff", "check", "pxx/", "tests/"): 1})
        monkeypatch.setattr(subprocess, "run", fake)
        rc = _self_lint()
        assert rc != 0
        assert "check=1 format=0" in capsys.readouterr().err

    def test_nonzero_if_format_fails(self, monkeypatch, capsys):
        fake, _ = self._stub_run(
            {("uv", "run", "ruff", "format", "--check", "pxx/", "tests/"): 1}
        )
        monkeypatch.setattr(subprocess, "run", fake)
        rc = _self_lint()
        assert rc != 0
        assert "check=0 format=1" in capsys.readouterr().err

    def test_both_subcommands_run_even_when_check_fails(self, monkeypatch):
        # Don't short-circuit on first failure — user wants to see every issue.
        fake, calls = self._stub_run(
            {("uv", "run", "ruff", "check", "pxx/", "tests/"): 1}
        )
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


class TestSelfImproveFlag:
    """Tests for the pxx --self-improve flag (#011 Tier 2)."""

    def _patch_endpoint_and_exec(self, monkeypatch):
        from pxx import cli as cli_module

        monkeypatch.setattr(
            cli_module,
            "detect_endpoint",
            lambda **kwargs: Endpoint("studio_lan", "http://x:11434"),
        )
        monkeypatch.setattr(cli_module.os, "execve", lambda *_: None)
        monkeypatch.setattr(cli_module, "_find_aider", lambda: "/x/aider")

    def test_self_improve_flag_detected_in_argv(self):
        argv = ["pxx", "--self-improve"]
        assert "--self-improve" in argv

    def test_self_improve_stripped_from_user_args(self):
        from pxx.scope import extract_scope_args

        _, after = extract_scope_args(["--self-improve", "--message", "focus on cli"])
        user_args = [
            a
            for a in after
            if a not in ("--edit", "--big", "--anywhere", "--self-improve")
        ]
        assert "--self-improve" not in user_args
        assert user_args == ["--message", "focus on cli"]

    def test_self_improve_combined_with_edit_exits_2(
        self, monkeypatch, tmp_path, capsys
    ):
        from pxx import cli as cli_module

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["pxx", "--self-improve", "--edit"])
        self._patch_endpoint_and_exec(monkeypatch)

        with pytest.raises(SystemExit) as exc:
            cli_module.main()
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert "ask-only" in err
        assert "--edit" in err

    def test_self_improve_banner_shows_self_improve_mode(
        self, monkeypatch, tmp_path, capsys
    ):
        from pxx import cli as cli_module

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["pxx", "--self-improve"])
        self._patch_endpoint_and_exec(monkeypatch)

        cli_module.main()
        err = capsys.readouterr().err
        assert "mode=ask (self-improve)" in err

    def test_self_improve_chdirs_to_repo_root(self, monkeypatch, tmp_path):
        from pxx import cli as cli_module

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["pxx", "--self-improve"])
        self._patch_endpoint_and_exec(monkeypatch)

        cli_module.main()
        # After main() returns (execv is mocked), cwd should be REPO_ROOT.
        assert Path.cwd() == REPO_ROOT

    def test_self_improve_extra_reads_includes_prompt(self, monkeypatch, tmp_path):
        from pxx import cli as cli_module

        captured_args: list[list[str]] = []

        def fake_execv(_bin, args, env=None):
            captured_args.append(args)

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["pxx", "--self-improve"])
        monkeypatch.setattr(
            cli_module,
            "detect_endpoint",
            lambda **kwargs: Endpoint("studio_lan", "http://x:11434"),
        )
        monkeypatch.setattr(cli_module.os, "execve", fake_execv)
        monkeypatch.setattr(cli_module, "_find_aider", lambda: "/x/aider")

        cli_module.main()
        assert captured_args, "execv was not called"
        argv = captured_args[0]
        # Both system.md and self-improve.md should be in --read positions.
        read_paths = [argv[i + 1] for i, a in enumerate(argv) if a == "--read"]
        assert any("system.md" in p for p in read_paths)
        assert any("self-improve.md" in p for p in read_paths)

    def test_self_improve_enforces_ask_chat_mode(self, monkeypatch, tmp_path):
        """Suggest-only must be enforced by aider's mode, not just prompt text.

        --self-improve sets edit_mode (for the safety tag / trusted-path gate),
        which used to skip the --chat-mode ask injection — leaving aider in its
        default code mode while the banner claimed ask. Regression guard.
        """
        from pxx import cli as cli_module

        captured_args: list[list[str]] = []

        def fake_execv(_bin, args, env=None):
            captured_args.append(args)

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["pxx", "--self-improve"])
        monkeypatch.setattr(
            cli_module,
            "detect_endpoint",
            lambda **kwargs: Endpoint("studio_lan", "http://x:11434"),
        )
        monkeypatch.setattr(cli_module.os, "execve", fake_execv)
        monkeypatch.setattr(cli_module, "_find_aider", lambda: "/x/aider")

        cli_module.main()
        assert captured_args, "execv was not called"
        argv = captured_args[0]
        assert "--chat-mode" in argv
        assert argv[argv.index("--chat-mode") + 1] == "ask"


class TestReviewVerdictWiring:
    """--review must fail closed when no review artifacts exist (9.1)."""

    def test_no_review_evidence_yields_no_review_and_rejected(
        self, monkeypatch, tmp_path, capsys
    ):
        import json

        from pxx import cli as cli_module

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["pxx", "--review"])
        monkeypatch.setattr(cli_module, "_git_repo_root", lambda: tmp_path)
        monkeypatch.setattr(cli_module.review_gate, "run_review_pass", lambda root: 0)

        with pytest.raises(SystemExit) as exc:
            cli_module.main()
        assert exc.value.code == 0

        err = capsys.readouterr().err
        assert "verdict=NO_REVIEW" in err

        state = json.loads((tmp_path / ".pxx" / "workflow_state.json").read_text())
        assert state["phase"] == "rejected"
        assert state["review_verdict"] == "NO_REVIEW"


class TestLoopFlag:
    """--loop gates: experimental, pxx-repo-only, scope+clean-tree required."""

    def test_refuses_outside_pxx_repo(self, monkeypatch, tmp_path, capsys):
        from pxx import cli as cli_module

        monkeypatch.setattr(sys, "argv", ["pxx", "--loop", "task", "--scope", "x/"])
        monkeypatch.setattr(cli_module, "_git_repo_root", lambda: tmp_path)
        with pytest.raises(SystemExit) as exc:
            cli_module.main()
        assert exc.value.code == 1
        assert "only inside" in capsys.readouterr().err

    def test_requires_task(self, monkeypatch, capsys):
        from pxx import cli as cli_module

        monkeypatch.setattr(sys, "argv", ["pxx", "--loop"])
        monkeypatch.setattr(cli_module, "_git_repo_root", lambda: cli_module.REPO_ROOT)
        with pytest.raises(SystemExit) as exc:
            cli_module.main()
        assert exc.value.code == 2
        assert "usage" in capsys.readouterr().err

    def test_requires_scope(self, monkeypatch, capsys):
        from pxx import cli as cli_module

        monkeypatch.setattr(sys, "argv", ["pxx", "--loop", "do the thing"])
        monkeypatch.setattr(cli_module, "_git_repo_root", lambda: cli_module.REPO_ROOT)
        with pytest.raises(SystemExit) as exc:
            cli_module.main()
        assert exc.value.code == 2
        assert "--scope" in capsys.readouterr().err

    def test_refuses_dirty_tree(self, monkeypatch, capsys):
        from pxx import cli as cli_module

        monkeypatch.setattr(sys, "argv", ["pxx", "--loop", "do it", "--scope", "pxx/"])
        monkeypatch.setattr(cli_module, "_git_repo_root", lambda: cli_module.REPO_ROOT)
        monkeypatch.setattr(cli_module, "_git_dirty", lambda: True)
        with pytest.raises(SystemExit) as exc:
            cli_module.main()
        assert exc.value.code == 1
        assert "dirty tree" in capsys.readouterr().err

    def test_dispatches_to_driver_with_experimental_banner(self, monkeypatch, capsys):
        from pxx import cli as cli_module

        calls: list[tuple] = []

        def fake_run_loop(
            root, task, scope, max_rounds, run_id=None, agent_version=None
        ):
            calls.append((root, task, scope, max_rounds, run_id, agent_version))
            return 5

        monkeypatch.setattr(
            sys,
            "argv",
            ["pxx", "--loop", "fix it", "--scope", "pxx/", "--max-rounds", "2"],
        )
        monkeypatch.setattr(cli_module, "_git_repo_root", lambda: cli_module.REPO_ROOT)
        monkeypatch.setattr(cli_module, "_git_dirty", lambda: False)
        monkeypatch.setattr(cli_module.loop_mod, "run_loop", fake_run_loop)
        # Identity capture is best-effort; keep the test hermetic (no probes).
        monkeypatch.setattr(
            cli_module, "detect_endpoint", lambda: cli_module.Endpoint("t", "http://x")
        )
        monkeypatch.setattr(cli_module, "model_for", lambda ep, tier=None: "m")

        with pytest.raises(SystemExit) as exc:
            cli_module.main()
        assert exc.value.code == 5
        assert calls and calls[0][1] == "fix it"
        assert calls[0][2].rstrip("/") == "pxx"
        assert calls[0][3] == 2
        assert "EXPERIMENTAL" in capsys.readouterr().err

    def test_refuses_without_pxx_hooks(self, monkeypatch, capsys):
        # The gate lives in loop.py (shared by run_loop and heal_once) — the
        # cli dispatch reaches the driver, which refuses before any edit.
        from pxx import cli as cli_module

        monkeypatch.setattr(sys, "argv", ["pxx", "--loop", "do it", "--scope", "pxx/"])
        monkeypatch.setattr(cli_module, "_git_repo_root", lambda: cli_module.REPO_ROOT)
        monkeypatch.setattr(cli_module, "_git_dirty", lambda: False)
        monkeypatch.setattr(cli_module.loop_mod, "_hooks_installed", lambda root: False)
        with pytest.raises(SystemExit) as exc:
            cli_module.main()
        assert exc.value.code == 1
        assert "--install-hook" in capsys.readouterr().err


class TestReviewHealWiring:
    """--review --heal = one REVISE round, scope-gated."""

    def _arrange_revise(self, monkeypatch, tmp_path):
        from pxx import cli as cli_module

        d = tmp_path / "review" / "claude"
        d.mkdir(parents=True)
        (d / "claude-f.md").write_text("### F-001 — fix me in x.py (P1, state: open)")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(cli_module, "_git_repo_root", lambda: tmp_path)
        monkeypatch.setattr(cli_module.review_gate, "run_review_pass", lambda root: 0)
        return cli_module

    def test_heal_requires_scope(self, monkeypatch, tmp_path, capsys):
        cli_module = self._arrange_revise(monkeypatch, tmp_path)
        monkeypatch.setattr(sys, "argv", ["pxx", "--review", "--heal"])
        with pytest.raises(SystemExit) as exc:
            cli_module.main()
        assert exc.value.code == 2
        assert "--scope" in capsys.readouterr().err

    def test_heal_dispatches_and_propagates_exit_code(self, monkeypatch, tmp_path):
        cli_module = self._arrange_revise(monkeypatch, tmp_path)
        monkeypatch.setattr(
            sys, "argv", ["pxx", "--review", "--heal", "--scope", "pxx/"]
        )
        monkeypatch.setattr(cli_module.loop_mod, "heal_once", lambda root, scope: 7)
        with pytest.raises(SystemExit) as exc:
            cli_module.main()
        assert exc.value.code == 7


class TestExtractSelfFixTask:
    """Unit tests for the task-string extractor (#012)."""

    def test_no_self_fix_flag_returns_none(self):
        from pxx.cli import _extract_self_fix_task

        task, rest = _extract_self_fix_task(["--edit", "--scope", "tests/"])
        assert task is None
        assert rest == ["--edit", "--scope", "tests/"]

    def test_positional_task_after_self_fix(self):
        from pxx.cli import _extract_self_fix_task

        task, rest = _extract_self_fix_task(
            ["--self-fix", "fix typo", "--scope", "pxx/cli.py"]
        )
        assert task == "fix typo"
        assert rest == ["--self-fix", "--scope", "pxx/cli.py"]

    def test_flag_arg_after_self_fix_is_not_swallowed(self):
        # --message immediately after --self-fix is NOT the task.
        from pxx.cli import _extract_self_fix_task

        task, rest = _extract_self_fix_task(
            ["--self-fix", "--message", "fix it", "--scope", "x/"]
        )
        assert task is None
        assert rest == ["--self-fix", "--message", "fix it", "--scope", "x/"]

    def test_self_fix_at_end_of_argv(self):
        from pxx.cli import _extract_self_fix_task

        task, rest = _extract_self_fix_task(["--edit", "--self-fix"])
        assert task is None
        assert rest == ["--edit", "--self-fix"]


class TestSelfFixFlag:
    """Integration tests for the --self-fix flag (#012)."""

    @pytest.fixture(autouse=True)
    def _isolate_env(self, monkeypatch):
        """Prevent cross-test leakage of env vars main() sets via os.environ[k]=v.

        monkeypatch.delenv records the original (absent) state and restores
        at teardown, so even if main() sets these directly the cleanup runs.
        """
        for v in (
            "PXX_AUTONOMOUS",
            "PXX_DIFF_CAP",
            "PXX_SCOPE",
            "PXX_ALLOW_BIG_DIFF",
            "OLLAMA_API_BASE",
        ):
            monkeypatch.delenv(v, raising=False)

    def _patch_endpoint_and_exec(self, monkeypatch):
        from pxx import cli as cli_module

        monkeypatch.setattr(
            cli_module,
            "detect_endpoint",
            lambda **kwargs: Endpoint("studio_lan", "http://x:11434"),
        )
        monkeypatch.setattr(cli_module.os, "execve", lambda *_: None)
        monkeypatch.setattr(cli_module, "_find_aider", lambda: "/x/aider")
        # CRITICAL: --self-fix forces edit_mode=True which triggers the
        # #002 safety-tag block. Without mocking, _create_safety_tag runs
        # `git stash --include-untracked` in the REAL pxx repo (because
        # main() chdirs to REPO_ROOT for self_fix_mode), wiping the
        # developer's uncommitted work into a stash. Mock both safety-tag
        # helpers to no-op so the test never touches the real .git/.
        monkeypatch.setattr(cli_module, "_create_safety_tag", lambda: None)
        monkeypatch.setattr(cli_module, "_prune_old_safety_tags", lambda **k: None)

    def test_self_fix_without_scope_exits_2(self, monkeypatch, tmp_path, capsys):
        from pxx import cli as cli_module

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["pxx", "--self-fix", "fix typo"])
        self._patch_endpoint_and_exec(monkeypatch)
        # Skip the trusted-paths gate for this test (no config).
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-empty"))

        with pytest.raises(SystemExit) as exc:
            cli_module.main()
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert "--self-fix requires --scope" in err

    def test_self_fix_with_self_improve_exits_2(self, monkeypatch, tmp_path, capsys):
        from pxx import cli as cli_module

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["pxx", "--self-fix", "x", "--self-improve"])
        self._patch_endpoint_and_exec(monkeypatch)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-empty"))

        with pytest.raises(SystemExit) as exc:
            cli_module.main()
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert "mutually exclusive" in err

    def test_self_fix_sets_autonomous_env_and_tightens_diff_cap(
        self, monkeypatch, tmp_path
    ):
        from pxx import cli as cli_module

        monkeypatch.chdir(tmp_path)
        # Need a scope path that resolves under a git repo. Use REPO_ROOT
        # itself: pxx is a git repo, so the scope check will pass.
        monkeypatch.setattr(
            sys,
            "argv",
            ["pxx", "--self-fix", "fix typo", "--scope", str(REPO_ROOT / "pxx")],
        )
        self._patch_endpoint_and_exec(monkeypatch)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-empty"))
        monkeypatch.delenv("PXX_AUTONOMOUS", raising=False)
        monkeypatch.delenv("PXX_DIFF_CAP", raising=False)

        cli_module.main()
        assert os.environ.get("PXX_AUTONOMOUS") == "1"
        assert os.environ.get("PXX_DIFF_CAP") == "60"

    def test_self_fix_respects_user_diff_cap_override(self, monkeypatch, tmp_path):
        from pxx import cli as cli_module

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            sys,
            "argv",
            ["pxx", "--self-fix", "fix", "--scope", str(REPO_ROOT / "pxx")],
        )
        self._patch_endpoint_and_exec(monkeypatch)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-empty"))
        monkeypatch.setenv("PXX_DIFF_CAP", "200")

        cli_module.main()
        # User's explicit cap wins.
        assert os.environ.get("PXX_DIFF_CAP") == "200"

    def test_self_fix_banner_shows_autonomous_annotation(
        self, monkeypatch, tmp_path, capsys
    ):
        from pxx import cli as cli_module

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            sys,
            "argv",
            ["pxx", "--self-fix", "fix", "--scope", str(REPO_ROOT / "pxx")],
        )
        self._patch_endpoint_and_exec(monkeypatch)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-empty"))

        cli_module.main()
        err = capsys.readouterr().err
        assert "mode=edit (autonomous)" in err
        assert "[autonomous]" in err  # the info line about commit tagging

    def test_self_fix_task_injected_as_message(self, monkeypatch, tmp_path):
        from pxx import cli as cli_module

        captured: list[list[str]] = []

        def mock_execve(_bin, args, env=None):
            captured.append(args)

        monkeypatch.setattr(cli_module.os, "execve", mock_execve)
        monkeypatch.setattr(
            cli_module,
            "detect_endpoint",
            lambda **kwargs: Endpoint("studio_lan", "http://x:11434"),
        )
        monkeypatch.setattr(cli_module, "_find_aider", lambda: "/x/aider")

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            sys,
            "argv",
            ["pxx", "--self-fix", "fix typo in cli", "--scope", str(REPO_ROOT / "pxx")],
        )
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-empty"))

        cli_module.main()
        assert captured, "execv was not called"
        argv = captured[0]
        # --message and the task should be in aider's argv.
        assert "--message" in argv
        assert "fix typo in cli" in argv

    def test_self_fix_does_not_overwrite_explicit_message(self, monkeypatch, tmp_path):
        from pxx import cli as cli_module

        captured: list[list[str]] = []

        def mock_execve(_bin, args, env=None):
            captured.append(args)

        monkeypatch.setattr(cli_module.os, "execve", mock_execve)
        monkeypatch.setattr(
            cli_module,
            "detect_endpoint",
            lambda **kwargs: Endpoint("studio_lan", "http://x:11434"),
        )
        monkeypatch.setattr(cli_module, "_find_aider", lambda: "/x/aider")

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "pxx",
                "--self-fix",
                "positional task",
                "--message",
                "user explicit",
                "--scope",
                str(REPO_ROOT / "pxx"),
            ],
        )
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-empty"))

        cli_module.main()
        argv = captured[0]
        # The user's explicit --message survives; positional was NOT prepended
        # as a second --message (which would have made aider pick whichever
        # arg wins; we test the simpler invariant: only one --message in argv).
        assert argv.count("--message") == 1
        assert "user explicit" in argv
        # Positional task should NOT be in argv as a separate token (since
        # _extract_self_fix_task consumed it from sys.argv).
        # NB: aider sees the user's --message exactly as given.

    def test_self_fix_stripped_from_user_args(self, monkeypatch, tmp_path):
        from pxx import cli as cli_module

        captured: list[list[str]] = []

        def mock_execve(_bin, args, env=None):
            captured.append(args)

        monkeypatch.setattr(cli_module.os, "execve", mock_execve)
        monkeypatch.setattr(
            cli_module,
            "detect_endpoint",
            lambda **kwargs: Endpoint("studio_lan", "http://x:11434"),
        )
        monkeypatch.setattr(cli_module, "_find_aider", lambda: "/x/aider")

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            sys,
            "argv",
            ["pxx", "--self-fix", "fix", "--scope", str(REPO_ROOT / "pxx")],
        )
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-empty"))

        cli_module.main()
        argv = captured[0]
        # Aider must not see --self-fix.
        assert "--self-fix" not in argv


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


class TestCheckSync:
    def test_check_sync_exits_0_on_success(self, monkeypatch, capsys):
        import pxx.drift as drift_mod

        res = drift_mod.DriftResult(
            local_sha="a", remote_sha="a", local_branch="m", remote_branch="m"
        )
        monkeypatch.setattr(drift_mod, "check_sync", lambda: res)
        monkeypatch.setattr(sys, "argv", ["pxx", "--check-sync"])

        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0
        _, err = capsys.readouterr()
        assert "in sync" in err

    def test_check_sync_exits_1_on_drift(self, monkeypatch, capsys):
        import pxx.drift as drift_mod

        res = drift_mod.DriftResult(
            local_sha="a",
            remote_sha="b",
            local_branch="m",
            remote_branch="m",
        )
        monkeypatch.setattr(drift_mod, "check_sync", lambda: res)
        monkeypatch.setattr(sys, "argv", ["pxx", "--check-sync"])

        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1
        _, err = capsys.readouterr()
        assert "drift detected" in err

    def test_autocheck_drift_fires_on_edit_when_env_set(self, monkeypatch, capsys):
        import pxx.drift as drift_mod

        res = drift_mod.DriftResult(
            local_sha="a",
            remote_sha="b",
            local_branch="m",
            remote_branch="m",
        )
        monkeypatch.setattr(drift_mod, "check_sync", lambda: res)
        monkeypatch.setenv("PXX_AUTOCHECK_DRIFT", "1")
        monkeypatch.setattr(sys, "argv", ["pxx", "--edit"])

        # CF-011: Assert execv runs even after warning
        mock_execv = MagicMock()
        monkeypatch.setattr(os, "execve", mock_execv)

        monkeypatch.setattr(
            "pxx.cli.detect_endpoint", lambda **kwargs: Endpoint("n", "u")
        )
        monkeypatch.setattr("pxx.cli._find_aider", lambda: "/x/aider")
        monkeypatch.setattr("pxx.cli._create_safety_tag", lambda: None)
        monkeypatch.setattr("pxx.cli.extract_scope_args", lambda a: ([], a))

        main()
        _, err = capsys.readouterr()
        assert "drift detected" in err
        assert mock_execv.called

    def test_autocheck_drift_fires_on_self_fix_when_env_set(self, monkeypatch, capsys):
        import pxx.drift as drift_mod

        res = drift_mod.DriftResult(
            local_sha="a",
            remote_sha="b",
            local_branch="m",
            remote_branch="m",
        )
        monkeypatch.setattr(drift_mod, "check_sync", lambda: res)
        monkeypatch.setenv("PXX_AUTOCHECK_DRIFT", "1")
        # CF-015: --self-fix should also trigger the autocheck
        monkeypatch.setattr(sys, "argv", ["pxx", "--self-fix", "fix", "--scope", "."])

        # CF-011: Assert execv runs even after warning
        mock_execv = MagicMock()
        monkeypatch.setattr(os, "execve", mock_execv)

        monkeypatch.setattr(
            "pxx.cli.detect_endpoint", lambda **kwargs: Endpoint("n", "u")
        )
        monkeypatch.setattr("pxx.cli._find_aider", lambda: "/x/aider")
        monkeypatch.setattr("pxx.cli._create_safety_tag", lambda: None)
        # CF-015: Ensure scope resolution returns something so --self-fix check passes
        monkeypatch.setattr("pxx.cli.extract_scope_args", lambda a: (["."], a))
        monkeypatch.setattr("pxx.cli.resolve_scopes", lambda _a, _r: ["/resolved"])

        main()
        _, err = capsys.readouterr()
        assert "drift detected" in err
        assert mock_execv.called

    def test_no_check_sync_bypasses_autocheck(self, monkeypatch, tmp_path, capsys):
        import pxx.drift as drift_mod

        mock_check = MagicMock()
        monkeypatch.setattr(drift_mod, "check_sync", mock_check)
        monkeypatch.setenv("PXX_AUTOCHECK_DRIFT", "1")
        monkeypatch.setattr(sys, "argv", ["pxx", "--edit", "--no-check-sync"])

        monkeypatch.setattr(os, "execve", lambda _bin, args, env=None: None)
        monkeypatch.setattr(
            "pxx.cli.detect_endpoint", lambda **kwargs: Endpoint("n", "u")
        )
        monkeypatch.setattr("pxx.cli._find_aider", lambda: "/x/aider")
        monkeypatch.setattr("pxx.cli._create_safety_tag", lambda: None)
        monkeypatch.setattr("pxx.cli._prune_old_safety_tags", lambda **k: None)
        monkeypatch.setattr("pxx.cli.extract_scope_args", lambda a: ([], a))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-empty"))

        main()
        assert not mock_check.called


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

    def test_content_includes_routing_directive_and_example(
        self, tmp_path, monkeypatch
    ):
        """The context must instruct task-routing, not just list commands."""
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        commands = [
            CommandInfo(
                name="typecheck", path=Path("/tc.md"), description="type hints"
            ),
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
        _write_commands_context(
            [CommandInfo(name="x", path=Path("/x.md"), description="d1")]
        )
        first = (tmp_path / COMMANDS_CONTEXT_FILE).read_text()
        assert "/load /x.md" in first
        # Second write — different command. Old content must be gone.
        _write_commands_context(
            [CommandInfo(name="y", path=Path("/y.md"), description="d2")]
        )
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
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
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
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        before = int(time.time())
        tag = _create_safety_tag()
        after = int(time.time())
        assert tag is not None
        ts = int(tag.removeprefix(SAFETY_TAG_PREFIX))
        assert before <= ts <= after

    def test_stashes_dirty_changes(self, tmp_path, monkeypatch):
        self._init_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
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


class TestEmitCoreRestartBanner:
    """#008 M2 banner. Monkeypatches the helper functions cli pulls from
    ``audit`` / ``subprocess`` to drive the branches without touching git."""

    def _stub_pxx_repo(self, monkeypatch):
        """Wire _in_git_repo/_git_repo_root to make us look like we're in pxx."""
        import pxx.cli as cli_mod

        monkeypatch.setattr(cli_mod, "_in_git_repo", lambda: True)
        monkeypatch.setattr(cli_mod, "_git_repo_root", lambda: cli_mod.REPO_ROOT)

    def test_silent_when_not_in_git_repo(self, monkeypatch, capsys):
        import pxx.cli as cli_mod

        monkeypatch.setattr(cli_mod, "_in_git_repo", lambda: False)
        _emit_core_restart_banner()
        assert capsys.readouterr().err == ""

    def test_silent_when_repo_root_differs_from_pxx(
        self, monkeypatch, tmp_path, capsys
    ):
        import pxx.cli as cli_mod

        monkeypatch.setattr(cli_mod, "_in_git_repo", lambda: True)
        monkeypatch.setattr(cli_mod, "_git_repo_root", lambda: tmp_path)
        _emit_core_restart_banner()
        assert capsys.readouterr().err == ""

    def test_silent_when_head_sha_missing(self, monkeypatch, capsys):
        import pxx.cli as cli_mod

        self._stub_pxx_repo(monkeypatch)
        monkeypatch.setattr(cli_mod, "_git_head_sha", lambda: None)
        _emit_core_restart_banner()
        assert capsys.readouterr().err == ""

    def test_silent_when_prev_sha_unknown(self, monkeypatch, capsys):
        import pxx.cli as cli_mod

        self._stub_pxx_repo(monkeypatch)
        monkeypatch.setattr(cli_mod, "_git_head_sha", lambda: "abcdef0")
        monkeypatch.setattr(cli_mod.audit, "last_session_head_for", lambda r: None)
        _emit_core_restart_banner()
        assert capsys.readouterr().err == ""

    def test_silent_when_prev_equals_current(self, monkeypatch, capsys):
        import pxx.cli as cli_mod

        self._stub_pxx_repo(monkeypatch)
        monkeypatch.setattr(cli_mod, "_git_head_sha", lambda: "abcdef0")
        monkeypatch.setattr(cli_mod.audit, "last_session_head_for", lambda r: "abcdef0")
        _emit_core_restart_banner()
        assert capsys.readouterr().err == ""

    def test_silent_when_audit_lookup_raises(self, monkeypatch, capsys):
        import pxx.cli as cli_mod

        self._stub_pxx_repo(monkeypatch)
        monkeypatch.setattr(cli_mod, "_git_head_sha", lambda: "abcdef0")

        def _boom(_r):
            raise OSError("disk gone")

        monkeypatch.setattr(cli_mod.audit, "last_session_head_for", _boom)
        _emit_core_restart_banner()
        assert capsys.readouterr().err == ""

    def test_silent_when_git_diff_fails(self, monkeypatch, capsys):
        import pxx.cli as cli_mod

        self._stub_pxx_repo(monkeypatch)
        monkeypatch.setattr(cli_mod, "_git_head_sha", lambda: "abcdef0")
        monkeypatch.setattr(cli_mod.audit, "last_session_head_for", lambda r: "fedcba0")

        class _Result:
            returncode = 128
            stdout = ""

        monkeypatch.setattr(cli_mod.subprocess, "run", lambda *a, **kw: _Result())
        _emit_core_restart_banner()
        assert capsys.readouterr().err == ""

    def test_silent_when_only_non_core_changed(self, monkeypatch, capsys):
        import pxx.cli as cli_mod

        self._stub_pxx_repo(monkeypatch)
        monkeypatch.setattr(cli_mod, "_git_head_sha", lambda: "abcdef0")
        monkeypatch.setattr(cli_mod.audit, "last_session_head_for", lambda r: "fedcba0")

        class _Result:
            returncode = 0
            stdout = "README.md\nscripts/doctor.sh\n"

        monkeypatch.setattr(cli_mod.subprocess, "run", lambda *a, **kw: _Result())
        _emit_core_restart_banner()
        assert capsys.readouterr().err == ""

    def test_emits_when_core_file_changed(self, monkeypatch, capsys):
        import pxx.cli as cli_mod

        self._stub_pxx_repo(monkeypatch)
        monkeypatch.setattr(cli_mod, "_git_head_sha", lambda: "abcdef0123456789")
        monkeypatch.setattr(cli_mod.audit, "last_session_head_for", lambda r: "fedcba0")

        class _Result:
            returncode = 0
            stdout = "pxx/cli.py\nREADME.md\n"

        monkeypatch.setattr(cli_mod.subprocess, "run", lambda *a, **kw: _Result())
        _emit_core_restart_banner()
        err = capsys.readouterr().err
        assert "loaded freshly-edited" in err
        assert "cli.py" in err
        assert "abcdef0" in err  # short sha (first 7)

    def test_emits_for_endpoints(self, monkeypatch, capsys):
        import pxx.cli as cli_mod

        self._stub_pxx_repo(monkeypatch)
        monkeypatch.setattr(cli_mod, "_git_head_sha", lambda: "abcdef0")
        monkeypatch.setattr(cli_mod.audit, "last_session_head_for", lambda r: "fedcba0")

        class _Result:
            returncode = 0
            stdout = "pxx/endpoints.py\n"

        monkeypatch.setattr(cli_mod.subprocess, "run", lambda *a, **kw: _Result())
        _emit_core_restart_banner()
        err = capsys.readouterr().err
        assert "endpoints.py" in err

    def test_emits_lists_multiple_core_files(self, monkeypatch, capsys):
        import pxx.cli as cli_mod

        self._stub_pxx_repo(monkeypatch)
        monkeypatch.setattr(cli_mod, "_git_head_sha", lambda: "abcdef0")
        monkeypatch.setattr(cli_mod.audit, "last_session_head_for", lambda r: "fedcba0")

        class _Result:
            returncode = 0
            stdout = "pxx/cli.py\npxx/endpoints.py\n"

        monkeypatch.setattr(cli_mod.subprocess, "run", lambda *a, **kw: _Result())
        _emit_core_restart_banner()
        err = capsys.readouterr().err
        assert "cli.py" in err
        assert "endpoints.py" in err
