"""Packaging metadata invariants that keep a stranger's install working.

The shipped `requires-python` is the only thing standing between a naive
`uv tool install pxx-orchestrator` / `pip install pxx-orchestrator` and a
broken tool env: the pinned `aider-chat` drags in `pydub`, which imports the
`audioop` stdlib module that PEP 594 removed in CPython 3.13. Without an upper
bound, uv/pip select the newest interpreter (3.13+), aider dies at import, and
pxx ships installable-but-broken by default. This test pins the bound so the
regression is caught in CI, not by the next first-time user.
"""

import tomllib
from pathlib import Path

from packaging.specifiers import SpecifierSet

_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _requires_python() -> str:
    data = tomllib.loads(_PYPROJECT.read_text())
    return data["project"]["requires-python"]


def test_requires_python_excludes_audioop_removed_interpreters() -> None:
    spec = SpecifierSet(_requires_python())
    # Supported today (aider-chat==0.86.2 is >=3.10,<3.13).
    assert spec.contains("3.11")
    assert spec.contains("3.12")
    # 3.13 removed `audioop` (PEP 594) → aider/pydub import crash. Must be
    # unresolvable so uv/pip never auto-select it.
    assert not spec.contains("3.13")
    assert not spec.contains("3.14")


def test_requires_python_keeps_the_floor() -> None:
    # The cap must not have moved the 3.11 floor pxx's modern syntax needs.
    spec = SpecifierSet(_requires_python())
    assert not spec.contains("3.10")
    assert spec.contains("3.11")


def test_version_lockstep_dunder_matches_pyproject() -> None:
    # Reviewers flagged version drift between pxx.__version__ and pyproject; the
    # dunder feeds agent_version_id, so the two MUST agree. Cheap regression pin.
    import pxx

    data = tomllib.loads(_PYPROJECT.read_text())
    assert pxx.__version__ == data["project"]["version"]
