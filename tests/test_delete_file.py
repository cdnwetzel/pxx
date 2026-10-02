"""delete_file (2.6.1): a removal is a STAGED move, never an unlink.

The operator's doctrine this serves: no agent permanently deletes; every
removal goes to a staging area with the original path preserved, and a human
purges it. Before this tool the model had no way to remove anything and, told
to, spent twenty refused turns on rm/mv before leaving a two-line stub. Now:
one call, the file moves to <delete_staging>/<project>/<relative path>, the
run continues, and the diff shows the removal.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pxx.broker import ActionClass, PermissionProfile, RiskTier, classify
from pxx.config import ConfigError, Settings, load_settings
from pxx.errors import ScopeViolation
from pxx.events import EventBus
from pxx.safety import HookRunner, PermissionMode, ScopeGate
from pxx.tools import ToolContext, default_registry


def run(coro):
    return asyncio.run(coro)


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "old.py").write_text("print('old')\n")
    (root / "keep.py").write_text("print('keep')\n")
    return root


def _ctx(
    root: Path,
    staging: Path | None,
    bus: EventBus | None = None,
    permission: PermissionMode = PermissionMode.EDIT,
) -> ToolContext:
    return ToolContext(
        scope=ScopeGate(root),
        hooks=HookRunner(()),
        permission=permission,
        bus=bus or EventBus(),
        cwd=root,
        session_id="t",
        refuse_scope_violations=False,
        delete_staging=staging,
    )


def test_a_removal_is_a_move_into_staging_keeping_the_relative_path(tmp_path):
    root = _project(tmp_path)
    staging = tmp_path / "_DELETE_THIS"
    events = []
    bus = EventBus()

    async def _rec(e):
        events.append(e)

    bus.subscribe(_rec)
    out = run(
        default_registry().call(
            "delete_file", {"path": "src/old.py", "reason": "superseded"}, _ctx(root, staging, bus)
        )
    )
    assert out.startswith("staged for removal: src/old.py")
    assert not (root / "src" / "old.py").exists()
    staged = staging / "proj" / "src" / "old.py"
    assert staged.read_text() == "print('old')\n"
    changed = [e for e in events if e.kind == "file_changed"]
    assert changed and changed[-1].data["action"] == "staged_delete"
    assert changed[-1].data["staged_to"] == str(staged)
    assert changed[-1].data["reason"] == "superseded"


def test_repeated_removals_of_the_same_path_keep_every_copy(tmp_path):
    """Three removals within one clock second: the first lands at the plain
    name, the next two at timestamped names that never collide. Nothing
    staged is ever overwritten -- that promise is the tool's whole point."""
    root = _project(tmp_path)
    staging = tmp_path / "_DELETE_THIS"
    ctx = _ctx(root, staging)
    for body in ("first\n", "second\n", "third\n"):
        (root / "src" / "old.py").write_text(body)
        run(default_registry().call("delete_file", {"path": "src/old.py"}, ctx))
    kept = sorted(staging.joinpath("proj", "src").iterdir())
    assert len(kept) == 3
    assert sorted(p.read_text() for p in kept) == ["first\n", "second\n", "third\n"]
    assert kept[0].name == "old.py" and all(p.name.startswith("old.py.") for p in kept[1:])


def test_a_symlink_is_not_removed_and_its_target_is_untouched(tmp_path):
    root = _project(tmp_path)
    staging = tmp_path / "_DELETE_THIS"
    (root / "link.py").symlink_to(root / "keep.py")
    out = run(default_registry().call("delete_file", {"path": "link.py"}, _ctx(root, staging)))
    assert out.startswith("error: symbolic links are not removed")
    assert (root / "link.py").is_symlink() and (root / "keep.py").is_file()
    assert not staging.exists()


def test_staging_inside_the_project_is_refused(tmp_path):
    root = _project(tmp_path)
    for staging in (root, root / "trash"):
        out = run(
            default_registry().call("delete_file", {"path": "src/old.py"}, _ctx(root, staging))
        )
        assert out.startswith("error: delete_staging") and "inside the project root" in out
        assert (root / "src" / "old.py").exists()


def test_disabled_without_a_staging_root_and_nothing_moves(tmp_path):
    root = _project(tmp_path)
    out = run(default_registry().call("delete_file", {"path": "src/old.py"}, _ctx(root, None)))
    assert out.startswith("error: delete_file is not enabled")
    assert "a human will remove them" in out
    assert (root / "src" / "old.py").exists()


def test_missing_file_and_directory_are_errors_for_the_model_not_gate_events(tmp_path):
    root = _project(tmp_path)
    staging = tmp_path / "_DELETE_THIS"
    ctx = _ctx(root, staging)
    assert run(default_registry().call("delete_file", {"path": "src/nope.py"}, ctx)).startswith(
        "error: no such file"
    )
    assert run(default_registry().call("delete_file", {"path": "src"}, ctx)).startswith(
        "error: not a regular file"
    )
    assert (root / "src" / "old.py").exists()


def test_out_of_scope_and_protected_sources_are_gate_denials(tmp_path):
    root = _project(tmp_path)
    staging = tmp_path / "_DELETE_THIS"
    (tmp_path / "outside.py").write_text("x")
    ctx = _ctx(root, staging)
    with pytest.raises(ScopeViolation):
        run(default_registry().call("delete_file", {"path": str(tmp_path / "outside.py")}, ctx))
    assert (tmp_path / "outside.py").exists()
    (root / "pxx.toml").write_text("permission = 'edit'\n")
    with pytest.raises(ScopeViolation):  # protected path: the trusted control plane
        run(default_registry().call("delete_file", {"path": "pxx.toml"}, ctx))
    assert (root / "pxx.toml").exists()


def test_classified_delete_high_tier_and_allowed_in_edit_mode(tmp_path):
    spec = default_registry()._tools["delete_file"].spec
    action = classify("delete_file", spec, {"path": "src/old.py"})
    assert action.action_class is ActionClass.DELETE
    assert action.risk_tier is RiskTier.HIGH
    assert action.mutating
    assert "delete" in PermissionProfile.defaults().allowed(PermissionMode.EDIT)
    assert "delete" not in PermissionProfile.defaults().allowed(PermissionMode.PLAN)


def test_plan_mode_refuses_the_class(tmp_path):
    root = _project(tmp_path)
    staging = tmp_path / "_DELETE_THIS"
    with pytest.raises(ScopeViolation):
        run(
            default_registry().call(
                "delete_file",
                {"path": "src/old.py"},
                _ctx(root, staging, permission=PermissionMode.PLAN),
            )
        )
    assert (root / "src" / "old.py").exists()


def test_delete_staging_is_trusted_config_only(tmp_path, monkeypatch):
    # repo-local: ignored with a warning, like hooks (A0b)
    (tmp_path / "pxx.toml").write_text(f'delete_staging = "{tmp_path / "evil"}"\n')
    monkeypatch.setattr("pxx.config._USER_CONFIG", tmp_path / "nope-user.toml")
    monkeypatch.setattr("pxx.config._USER_ENV", tmp_path / "nope-env")
    assert load_settings(cwd=tmp_path).delete_staging == ""
    # user config: honoured, ~ expanded
    user = tmp_path / "user.toml"
    user.write_text('delete_staging = "~/_DELETE_THIS"\n')
    monkeypatch.setattr("pxx.config._USER_CONFIG", user)
    assert load_settings(cwd=tmp_path).delete_staging == str(Path("~/_DELETE_THIS").expanduser())
    assert Settings().delete_staging == ""
    user.write_text("delete_staging = 3\n")
    with pytest.raises(ConfigError):
        load_settings(cwd=tmp_path)
