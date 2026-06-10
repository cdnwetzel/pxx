"""Tests for the --with-docs helpers (#009)."""

from __future__ import annotations

import sys

from pxx import docs_sme


def test_resolve_from_python_version_file(tmp_path):
    (tmp_path / ".python-version").write_text("3.11\n")
    assert docs_sme.resolve_python_version(tmp_path) == "3.11"


def test_resolve_from_pyproject_requires_python(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nrequires-python = ">=3.12"\n'
    )
    assert docs_sme.resolve_python_version(tmp_path) == "3.12"


def test_python_version_file_wins_over_pyproject(tmp_path):
    (tmp_path / ".python-version").write_text("3.10\n")
    (tmp_path / "pyproject.toml").write_text('[project]\nrequires-python = ">=3.12"\n')
    assert docs_sme.resolve_python_version(tmp_path) == "3.10"


def test_resolve_falls_back_to_interpreter(tmp_path):
    expected = f"{sys.version_info.major}.{sys.version_info.minor}"
    assert docs_sme.resolve_python_version(tmp_path) == expected


def test_malformed_pyproject_falls_back(tmp_path):
    (tmp_path / "pyproject.toml").write_text("this is not toml :::")
    expected = f"{sys.version_info.major}.{sys.version_info.minor}"
    assert docs_sme.resolve_python_version(tmp_path) == expected


def test_sme_base_url_env_override(monkeypatch):
    monkeypatch.setenv("PXX_DOCS_SME_URL", "http://host:9999/")
    assert docs_sme.sme_base_url() == "http://host:9999"


def test_probe_sme_false_when_down():
    # Nothing listening on this port → graceful False, no exception.
    assert docs_sme.probe_sme("http://127.0.0.1:1", timeout=0.2) is False
