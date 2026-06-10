"""Tests for the machine-local env-file loader (~/.config/pxx/env)."""

from __future__ import annotations

import os
from pathlib import Path

from pxx import _load_env_file


def test_sets_defaults_from_file(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("PXX_TEST_KEY", raising=False)
    f = tmp_path / "env"
    f.write_text("# fleet config\nPXX_TEST_KEY=hello\n\nPXX_OTHER='quoted'\n")

    _load_env_file(f)

    assert os.environ.pop("PXX_TEST_KEY") == "hello"
    assert os.environ.pop("PXX_OTHER") == "quoted"


def test_real_env_wins_over_file(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PXX_TEST_KEY", "from-env")
    f = tmp_path / "env"
    f.write_text("PXX_TEST_KEY=from-file\n")

    _load_env_file(f)

    assert os.environ["PXX_TEST_KEY"] == "from-env"


def test_missing_file_is_silent(tmp_path: Path):
    _load_env_file(tmp_path / "does-not-exist")  # must not raise


def test_garbage_lines_ignored(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("PXX_TEST_KEY", raising=False)
    f = tmp_path / "env"
    f.write_text("just words\n=novalue\n# comment\nPXX_TEST_KEY=ok\n")

    _load_env_file(f)

    assert os.environ.pop("PXX_TEST_KEY") == "ok"
