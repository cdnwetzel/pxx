"""The three version pins agree: pyproject.toml, ``pxx.__version__`` and the
``pxx-orchestrator`` entry in ``uv.lock``.

``uv lock`` derives the lock's own-package entry from pyproject, so a release
commit that bumps pyproject and the package but not the lock ships a tree whose
lock describes the previous release. CI syncs without ``--locked`` and the
release integrity step compared only pyproject and the package, so the 2.8.0
release PR carried a 2.7.0 lock entry unnoticed (2026-10-13). This test is the
earliest catch: it runs wherever pytest runs, with no uv on the box.
"""

import tomllib
from pathlib import Path

import pxx

ROOT = Path(__file__).resolve().parents[1]


def _lock_own_version() -> str:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    own = [p for p in lock["package"] if p["name"] == "pxx-orchestrator"]
    assert len(own) == 1, f"expected one pxx-orchestrator entry in uv.lock, found {len(own)}"
    return str(own[0]["version"])


def test_pyproject_package_and_lock_agree_on_the_version() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = str(pyproject["project"]["version"])
    assert declared == pxx.__version__, f"pyproject={declared} package={pxx.__version__}"
    assert declared == _lock_own_version(), f"pyproject={declared} uv.lock={_lock_own_version()}"
