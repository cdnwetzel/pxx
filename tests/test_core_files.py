"""Tests for pxx._core_files (#008 M3).

Pure path-matching; no filesystem, no git. The post-commit hook (M1)
imports ``CORE_FILES`` via a separate python invocation, so the only
contract these tests need to pin is ``is_core``'s normalization rules.
"""

from __future__ import annotations

import pytest

from pxx._core_files import CORE_FILES, is_core


class TestCoreFilesConstant:
    def test_contains_cli_and_endpoints(self):
        assert "pxx/cli.py" in CORE_FILES
        assert "pxx/endpoints.py" in CORE_FILES

    def test_no_duplicates(self):
        assert len(CORE_FILES) == len(set(CORE_FILES))

    def test_all_paths_posix_relative(self):
        for path in CORE_FILES:
            assert not path.startswith("/"), f"{path!r} is absolute"
            assert not path.startswith("./"), f"{path!r} has leading ./"
            assert "\\" not in path, f"{path!r} contains backslash"


class TestIsCorePositive:
    @pytest.mark.parametrize("path", list(CORE_FILES))
    def test_exact_relative(self, path):
        assert is_core(path)

    def test_absolute_path_to_cli(self):
        assert is_core("/Users/x/code/pxx/pxx/cli.py")

    def test_absolute_path_to_endpoints(self):
        assert is_core("/home/dev/pxx/pxx/endpoints.py")

    def test_trailing_slash(self):
        assert is_core("pxx/cli.py/")

    def test_backslash_normalized(self):
        assert is_core("pxx\\cli.py")


class TestIsCoreNegative:
    @pytest.mark.parametrize(
        "path",
        [
            "README.md",
            "pxx/prompts/system.md",
            "pxx/commands/typecheck.md",
            "config/aider.conf.yml",
            "scripts/install-precommit-hook.sh",
            "pxx/audit.py",
            "tests/test_cli.py",
            "",
        ],
    )
    def test_non_core_paths(self, path):
        assert not is_core(path)

    def test_partial_suffix_does_not_match(self):
        # `cli.py` alone (no `pxx/` prefix) must not match — it could be
        # any project's cli.py, not pxx's.
        assert not is_core("cli.py")

    def test_similar_name_does_not_match(self):
        assert not is_core("pxx/cli_old.py")
        assert not is_core("pxx/cli.pyc")
