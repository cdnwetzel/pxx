"""Tests for pxx.scope — path-prefix scope handling (#003 S1, S3)."""

from __future__ import annotations

import io
import os
import subprocess
from pathlib import Path

import pytest

from pxx.scope import (
    extract_scope_args,
    format_for_env,
    is_in_scope,
    is_path_trusted,
    load_trusted_paths,
    resolve_scopes,
    scope_check_main,
    trusted_paths_config_path,
)


class TestExtractScopeArgs:
    def test_no_scope_flag(self):
        scopes, rest = extract_scope_args(["--edit", "--message", "hi"])
        assert scopes == []
        assert rest == ["--edit", "--message", "hi"]

    def test_single_scope_space_form(self):
        scopes, rest = extract_scope_args(["--scope", "tests/", "--edit"])
        assert scopes == ["tests/"]
        assert rest == ["--edit"]

    def test_single_scope_equals_form(self):
        scopes, rest = extract_scope_args(["--scope=tests/", "--edit"])
        assert scopes == ["tests/"]
        assert rest == ["--edit"]

    def test_multiple_scopes_union(self):
        scopes, rest = extract_scope_args(
            ["--scope", "tests/", "--scope=docs/", "--scope", "pxx/cli.py"]
        )
        assert scopes == ["tests/", "docs/", "pxx/cli.py"]
        assert rest == []

    def test_malformed_trailing_scope(self):
        # --scope at end of argv with no value should be dropped silently.
        scopes, rest = extract_scope_args(["--edit", "--scope"])
        assert scopes == []
        assert rest == ["--edit"]

    def test_scope_value_preserved_as_given(self):
        # Resolution happens in resolve_scopes; extract is verbatim.
        scopes, rest = extract_scope_args(["--scope", "/absolute/path"])
        assert scopes == ["/absolute/path"]
        assert rest == []


class TestResolveScopes:
    def _make_repo(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "tests").mkdir()
        (repo / "src").mkdir()
        (repo / "src" / "lib.py").write_text("# stub")
        return repo

    def test_relative_path_from_repo_root(self, tmp_path):
        repo = self._make_repo(tmp_path)
        result = resolve_scopes(["tests/"], repo, cwd=repo)
        assert result == ["tests/"]

    def test_relative_path_from_subdir(self, tmp_path):
        repo = self._make_repo(tmp_path)
        result = resolve_scopes(["../tests/"], repo, cwd=repo / "src")
        assert result == ["tests/"]

    def test_absolute_path_inside_repo(self, tmp_path):
        repo = self._make_repo(tmp_path)
        # Note: Path strips the trailing slash when stringified, so the raw
        # input has no slash and the result therefore has no slash either.
        result = resolve_scopes([str(repo / "tests")], repo)
        assert result == ["tests"]

    def test_absolute_path_with_explicit_trailing_slash(self, tmp_path):
        repo = self._make_repo(tmp_path)
        result = resolve_scopes([str(repo / "tests") + "/"], repo)
        assert result == ["tests/"]

    def test_absolute_path_outside_repo_raises(self, tmp_path):
        repo = self._make_repo(tmp_path)
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        with pytest.raises(ValueError, match="outside repo"):
            resolve_scopes([str(outside)], repo)

    def test_trailing_slash_preserved(self, tmp_path):
        repo = self._make_repo(tmp_path)
        with_slash = resolve_scopes(["tests/"], repo, cwd=repo)
        without_slash = resolve_scopes(["tests"], repo, cwd=repo)
        assert with_slash == ["tests/"]
        assert without_slash == ["tests"]

    def test_repo_root_scope_resolves_to_empty_string(self, tmp_path):
        repo = self._make_repo(tmp_path)
        result = resolve_scopes(["."], repo, cwd=repo)
        assert result == [""]

    def test_multiple_scopes_in_one_call(self, tmp_path):
        repo = self._make_repo(tmp_path)
        result = resolve_scopes(["tests/", "src/lib.py"], repo, cwd=repo)
        assert result == ["tests/", "src/lib.py"]


class TestIsInScope:
    def test_empty_prefixes_allows_anything(self):
        assert is_in_scope("any/path.py", []) is True

    def test_empty_string_prefix_allows_anything(self):
        assert is_in_scope("any/path.py", [""]) is True

    def test_exact_file_match_with_no_trailing_slash(self):
        assert is_in_scope("pxx/cli.py", ["pxx/cli.py"]) is True

    def test_dir_match_with_trailing_slash(self):
        assert is_in_scope("tests/test_cli.py", ["tests/"]) is True

    def test_dir_match_without_trailing_slash(self):
        # No trailing slash → directory-or-file. Subdir entry matches.
        assert is_in_scope("tests/test_cli.py", ["tests"]) is True

    def test_unrelated_file_not_in_scope(self):
        assert is_in_scope("docs/readme.md", ["tests/"]) is False

    def test_prefix_match_at_boundary_not_substring(self):
        # 'pxx/cli' should NOT match 'pxx/cli_helper.py' (no path boundary).
        assert is_in_scope("pxx/cli_helper.py", ["pxx/cli"]) is False
        # but 'pxx/cli.py' under prefix 'pxx/' does match
        assert is_in_scope("pxx/cli.py", ["pxx/"]) is True

    def test_multiple_prefixes_union(self):
        prefixes = ["tests/", "docs/"]
        assert is_in_scope("tests/x.py", prefixes) is True
        assert is_in_scope("docs/y.md", prefixes) is True
        assert is_in_scope("src/z.py", prefixes) is False

    def test_leading_slash_normalized(self):
        # File paths from git diff --cached --name-only typically don't have
        # leading slash, but be defensive.
        assert is_in_scope("/tests/test_cli.py", ["tests/"]) is True


class TestFormatForEnv:
    def test_single_scope(self):
        assert format_for_env(["tests/"]) == "tests/"

    def test_multi_scope_colon_separated(self):
        assert (
            format_for_env(["tests/", "docs/", "pxx/cli.py"])
            == "tests/:docs/:pxx/cli.py"
        )

    def test_empty(self):
        assert format_for_env([]) == ""


class TestScopeCheckMain:
    def test_no_scope_env_returns_0_no_output(self, monkeypatch, capsys):
        monkeypatch.delenv("PXX_SCOPE", raising=False)
        monkeypatch.setattr("sys.stdin", io.StringIO("any/file.py\nother/file.py\n"))
        rc = scope_check_main(["check"])
        assert rc == 0
        assert capsys.readouterr().out == ""

    def test_all_in_scope_returns_0_no_output(self, monkeypatch, capsys):
        monkeypatch.setenv("PXX_SCOPE", "tests/")
        monkeypatch.setattr("sys.stdin", io.StringIO("tests/a.py\ntests/b.py\n"))
        rc = scope_check_main(["check"])
        assert rc == 0
        assert capsys.readouterr().out == ""

    def test_out_of_scope_files_listed(self, monkeypatch, capsys):
        monkeypatch.setenv("PXX_SCOPE", "tests/")
        monkeypatch.setattr(
            "sys.stdin", io.StringIO("tests/a.py\nsrc/b.py\ndocs/c.md\n")
        )
        rc = scope_check_main(["check"])
        assert rc == 0  # CLI exits 0 — caller decides what to do with output
        out = capsys.readouterr().out
        assert "tests/a.py" not in out
        assert "src/b.py" in out
        assert "docs/c.md" in out

    def test_multi_scope_via_colon(self, monkeypatch, capsys):
        monkeypatch.setenv("PXX_SCOPE", "tests/:docs/")
        monkeypatch.setattr(
            "sys.stdin", io.StringIO("tests/a.py\nsrc/b.py\ndocs/c.md\n")
        )
        scope_check_main(["check"])
        out = capsys.readouterr().out
        assert "src/b.py" in out
        assert "tests/a.py" not in out
        assert "docs/c.md" not in out

    def test_invalid_subcommand_returns_2(self, monkeypatch, capsys):
        rc = scope_check_main(["unknown"])
        assert rc == 2

    def test_no_subcommand_returns_2(self, monkeypatch, capsys):
        rc = scope_check_main([])
        assert rc == 2


class TestScopeCliInvocation:
    """End-to-end: invoke as `python3 -m pxx.scope check` via subprocess.

    Proves the module's __main__ entry works the way the hook expects.
    """

    def test_subprocess_invocation_filters_out_of_scope(self, monkeypatch, tmp_path):
        env = os.environ.copy()
        env["PXX_SCOPE"] = "tests/"
        result = subprocess.run(
            ["python3", "-m", "pxx.scope", "check"],
            input="tests/a.py\nsrc/b.py\n",
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert result.returncode == 0
        # src/b.py should appear in stdout; tests/a.py should not.
        assert "src/b.py" in result.stdout
        assert "tests/a.py" not in result.stdout

    def test_subprocess_invocation_with_no_scope_env(self, tmp_path):
        env = os.environ.copy()
        env.pop("PXX_SCOPE", None)
        result = subprocess.run(
            ["python3", "-m", "pxx.scope", "check"],
            input="any/file.py\n",
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""


class TestTrustedPathsConfigPath:
    def test_uses_xdg_config_home_when_set(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        assert trusted_paths_config_path() == tmp_path / "pxx" / "trusted-paths"

    def test_falls_back_to_dot_config(self, monkeypatch):
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        assert (
            trusted_paths_config_path()
            == Path.home() / ".config" / "pxx" / "trusted-paths"
        )

    def test_empty_xdg_config_home_falls_back(self, monkeypatch):
        # POSIX ${VAR:-default} treats empty same as unset.
        monkeypatch.setenv("XDG_CONFIG_HOME", "")
        assert (
            trusted_paths_config_path()
            == Path.home() / ".config" / "pxx" / "trusted-paths"
        )


class TestLoadTrustedPaths:
    def test_missing_file_returns_empty(self, tmp_path):
        assert load_trusted_paths(tmp_path / "does-not-exist") == []

    def test_empty_file_returns_empty(self, tmp_path):
        cfg = tmp_path / "trusted-paths"
        cfg.write_text("")
        assert load_trusted_paths(cfg) == []

    def test_comments_and_blanks_skipped(self, tmp_path):
        cfg = tmp_path / "trusted-paths"
        target = tmp_path / "real-dir"
        target.mkdir()
        cfg.write_text(f"# a comment\n\n{target}  # inline comment\n   \n")
        assert load_trusted_paths(cfg) == [str(target.resolve())]

    def test_tilde_expansion(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        sub = fake_home / "projects"
        sub.mkdir()
        monkeypatch.setenv("HOME", str(fake_home))
        cfg = tmp_path / "trusted-paths"
        cfg.write_text("~/projects\n")
        assert load_trusted_paths(cfg) == [str(sub.resolve())]

    def test_multiple_entries(self, tmp_path):
        a = tmp_path / "a"
        b = tmp_path / "b"
        a.mkdir()
        b.mkdir()
        cfg = tmp_path / "trusted-paths"
        cfg.write_text(f"{a}\n{b}\n")
        assert load_trusted_paths(cfg) == [str(a.resolve()), str(b.resolve())]


class TestIsPathTrusted:
    def test_empty_prefixes_trusts_everything(self, tmp_path):
        ok, closest = is_path_trusted(tmp_path, [])
        assert ok is True
        assert closest is None

    def test_exact_match_is_trusted(self, tmp_path):
        ok, closest = is_path_trusted(tmp_path, [str(tmp_path)])
        assert ok is True
        assert closest is None

    def test_subdirectory_is_trusted(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        ok, closest = is_path_trusted(sub, [str(tmp_path)])
        assert ok is True
        assert closest is None

    def test_sibling_is_not_trusted_returns_closest(self, tmp_path):
        trusted = tmp_path / "trusted"
        sibling = tmp_path / "elsewhere"
        trusted.mkdir()
        sibling.mkdir()
        ok, closest = is_path_trusted(sibling, [str(trusted)])
        assert ok is False
        assert closest == str(trusted)

    def test_prefix_string_match_does_not_count_as_trusted(self, tmp_path):
        # /a/foo should not be trusted just because /a/foobar is. Requires
        # a path-boundary separator, not raw startswith.
        foo = tmp_path / "foo"
        foobar = tmp_path / "foobar"
        foo.mkdir()
        foobar.mkdir()
        ok, _ = is_path_trusted(foobar, [str(foo)])
        assert ok is False

    def test_closest_picks_longest_shared_prefix(self, tmp_path):
        deep = tmp_path / "a" / "b" / "c"
        other = tmp_path / "x"
        deep.mkdir(parents=True)
        other.mkdir()
        target = tmp_path / "a" / "b" / "different"
        target.mkdir(parents=True)
        ok, closest = is_path_trusted(target, [str(other), str(deep)])
        assert ok is False
        assert closest == str(deep)
