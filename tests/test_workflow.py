"""Tests for pxx.workflow: WORKFLOW.md contract loading (fail-closed)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pxx.errors import ConfigError
from pxx.workflow import load_workflow, validate_workflow, workflow_hash

REPO_ROOT = Path(__file__).resolve().parent.parent


def _write(root: Path, toml_body: str) -> Path:
    path = root / "WORKFLOW.md"
    path.write_text(f"# contract\n\n```toml\n{toml_body}\n```\n")
    return path


_VALID = """\
schema_version = 1
hooks = []

[states]
initial = "idle"
names = ["idle", "done"]
terminal = ["done"]

[budgets]
max_rounds = 5

[commands]
test = "pytest"

[permissions]
ask = ["read"]

[protected_paths]
paths = ["pxx/safety.py"]
"""


def test_repo_workflow_contract_is_valid() -> None:
    """The shipped WORKFLOW.md must always validate (CI pins this too)."""
    workflow = load_workflow(REPO_ROOT)
    assert workflow.schema_version == 1
    assert "test" in workflow.commands
    assert workflow.raw_hash


def test_load_valid_contract(tmp_path: Path) -> None:
    _write(tmp_path, _VALID)
    workflow = validate_workflow(tmp_path)
    assert workflow.initial_state == "idle"
    assert workflow.terminal_states == ("done",)
    assert workflow.budgets.max_rounds == 5
    assert workflow.permissions["ask"] == frozenset({"read"})
    assert workflow.protected_paths == ("pxx/safety.py",)


def test_missing_file_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_workflow(tmp_path)


def test_no_toml_fence_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "WORKFLOW.md").write_text("# no contract here\n")
    with pytest.raises(ConfigError, match="toml"):
        load_workflow(tmp_path)


def test_invalid_toml_fails_closed(tmp_path: Path) -> None:
    _write(tmp_path, "schema_version = [unclosed")
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_workflow(tmp_path)


def test_unknown_top_key_fails_closed(tmp_path: Path) -> None:
    _write(tmp_path, _VALID + "\nsurprise = 1\n")
    with pytest.raises(ConfigError, match="unknown keys"):
        load_workflow(tmp_path)


def test_missing_section_fails_closed(tmp_path: Path) -> None:
    _write(tmp_path, _VALID.replace('[permissions]\nask = ["read"]\n', ""))
    with pytest.raises(ConfigError, match="permissions"):
        load_workflow(tmp_path)


def test_bad_types_fail_closed(tmp_path: Path) -> None:
    _write(tmp_path, _VALID.replace("max_rounds = 5", 'max_rounds = "five"'))
    with pytest.raises(ConfigError, match="numeric"):
        load_workflow(tmp_path)


def test_unknown_action_class_fails_closed(tmp_path: Path) -> None:
    _write(tmp_path, _VALID.replace('ask = ["read"]', 'ask = ["read", "fly"]'))
    with pytest.raises(ConfigError, match="action classes"):
        load_workflow(tmp_path)


def test_unknown_permission_mode_fails_closed(tmp_path: Path) -> None:
    _write(tmp_path, _VALID.replace('ask = ["read"]', 'yolo = ["read"]'))
    with pytest.raises(ConfigError, match="unknown modes"):
        load_workflow(tmp_path)


def test_initial_state_must_be_listed(tmp_path: Path) -> None:
    _write(tmp_path, _VALID.replace('initial = "idle"', 'initial = "limbo"'))
    with pytest.raises(ConfigError, match="initial"):
        load_workflow(tmp_path)


def test_workflow_hash_absent_is_empty_and_present_is_stable(tmp_path: Path) -> None:
    assert workflow_hash(tmp_path) == ""
    _write(tmp_path, _VALID)
    first = workflow_hash(tmp_path)
    assert first and workflow_hash(tmp_path) == first
