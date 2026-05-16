"""Tests for the pxx._core_files module (#008)."""

from __future__ import annotations

import pytest

from pxx._core_files import CORE_FILES, is_core


class TestCoreFilesRegistry:
    def test_registry_is_not_empty(self):
        assert len(CORE_FILES) > 0

    def test_registry_contains_only_pxx_paths(self):
        for path in CORE_FILES:
            assert path.startswith("pxx/")
            assert path.endswith(".py")


class TestIsCorePositive:
    @pytest.mark.parametrize(
        "path",
        [
            "pxx/cli.py",
            "pxx/endpoints.py",
            "pxx/audit.py",
            "./pxx/cli.py",
            "/Users/foo/ai/code_pro/pxx/pxx/cli.py",
            "pxx/cli.py/",
            "pxx\\cli.py",
        ],
    )
    def test_core_paths_resolve_true(self, path):
        # pxx/audit.py is now core as of CF-007
        assert is_core(path)

    def test_case_insensitive_on_darwin(self):
        # is_core uses PurePosixPath, which is case-sensitive.
        # But our comparison is suffix-based on normalized posix strings.
        # If we need case-insensitivity on Mac, we'd need to normalize further.
        # For now, we assume standard lowercase paths for core modules.
        assert is_core("pxx/CLI.py") is False


class TestIsCoreNegative:
    @pytest.mark.parametrize(
        "path",
        [
            "README.md",
            "pxx/prompts/system.md",
            "pxx/commands/typecheck.md",
            "config/aider.conf.yml",
            "scripts/install-precommit-hook.sh",
            "tests/test_cli.py",
            "",
        ],
    )
    def test_non_core_paths(self, path):
        assert not is_core(path)

    def test_none_returns_false(self):
        assert is_core(None) is False
