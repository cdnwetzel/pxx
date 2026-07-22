"""END-TO-END PROOF (updated after A0/A0b/A2 enforcement, 2026-07-22).

Original finding (proven at 4248415): a model could write a repo-local
pxx.toml whose test_command the NEXT loop executed — no broker, no
PreToolUse hook, no policy. The protection set was label-only.

This file now pins BOTH halves:

1. THE ATTACK IS CLOSED (post-A0): the model's write_file to pxx.toml is
   hard-denied by the action broker in every permission mode, with real
   policy events on the stream (positive control — the denial is asserted
   from emitted events, not vacuous absence).
2. THE RESIDUAL HOLE (still open, 2.0.2-B): a pxx.toml that arrives in a
   FRESH CLONE (not model-written) still executes its test_command
   ungated. That is the workspace-trust problem, scoped separately.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from pxx.backends.mock import MockBackend
from pxx.config import ModelRef, Settings, load_settings
from pxx.loop import run_loop
from pxx.outcome import TerminalCode
from pxx.safety import PermissionMode
from pxx.session import Session

GIT = shutil.which("git")
needs_git = pytest.mark.skipif(GIT is None, reason="git not available")

MARKER = "PWNED_BY_REPO_CONFIG"


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run([GIT, "init", "-q"], cwd=path, check=True, capture_output=True)
    (path / "a.py").write_text("x = 1\n")
    subprocess.run([GIT, "add", "-A"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        [GIT, "-c", "user.name=t", "-c", "user.email=t@e.c", "commit", "-q", "-m", "i"],
        cwd=path,
        check=True,
        capture_output=True,
    )


def _settings(tmp_path: Path, **overrides) -> Settings:
    from dataclasses import replace

    base = Settings(
        model=ModelRef(provider="ollama", model="stub"),
        permission=PermissionMode.AUTO,
        memory_enabled=False,
        memory_dir=tmp_path / "mem",
        state_dir=tmp_path / "state",
    )
    return replace(base, **overrides) if overrides else base


@needs_git
def test_model_write_to_pxx_toml_is_broker_denied(tmp_path: Path) -> None:
    """A0 acceptance: the attack chain is broken at step 1 — the model can
    no longer write the config file that becomes code execution."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    backend = MockBackend(
        [
            {
                "tool": "write_file",
                "args": {"path": "pxx.toml", "content": 'test_command = "touch X"\n'},
            },
            {"done": "planted"},
        ]
    )
    session = Session(_settings(tmp_path), backend, cwd=repo)
    outcome = asyncio.run(session.run("set up the next step"))

    # the write is denied; the file never lands
    assert outcome.code is TerminalCode.OUT_OF_SCOPE
    assert not (repo / "pxx.toml").exists()

    # POSITIVE CONTROL: the denial is asserted from REAL emitted policy
    # events, not the absence of events — tool_action_proposed fired for the
    # attempt, and policy_decision records allowed=False with the reason.
    kinds = [e.kind for e in session.bus.history]
    assert "tool_action_proposed" in kinds, "policy events must flow (positive control)"
    decisions = [e for e in session.bus.history if e.kind == "policy_decision"]
    assert decisions, "a policy decision must be recorded for the attempt"
    denial = decisions[-1]
    assert denial.data["allowed"] is False
    assert "protected path" in denial.data["reason"]


@needs_git
def test_clone_arrived_pxx_toml_still_executes_ungated(tmp_path: Path) -> None:
    """2.0.2-B (OPEN): a pxx.toml arriving in a fresh clone — not written by
    the model — still executes its test_command with no broker/policy gate.
    This is the workspace-trust hole, not the config-write hole."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "pxx.toml").write_text(f'test_command = "touch {MARKER}"\n')

    settings = load_settings(repo, {"permission": PermissionMode.AUTO})
    assert settings.test_command == f"touch {MARKER}"

    events: list[dict] = []

    class Bus:
        history = events

        async def emit(self, kind, data, session_id=""):
            events.append({"kind": kind, **data})

        def subscribe(self, fn):
            pass

    outcome = asyncio.run(
        run_loop(
            "task",
            _settings(tmp_path, test_command=settings.test_command),
            cwd=repo,
            backend_factory=lambda: MockBackend([{"done": "ok"}]),
            test_command=settings.test_command,
            bus=Bus(),
        )
    )
    assert outcome.code is TerminalCode.COMPLETED
    assert (repo / MARKER).exists()  # still executes today — the open hole
    policy_events = [e for e in events if e["kind"] in ("tool_action_proposed", "policy_decision")]
    assert not policy_events, (
        "the loop's test execution path emits no broker/policy events at all "
        "(positive control: run N above proves the events DO fire when the "
        "broker is in the path)"
    )


@needs_git
def test_repo_local_hooks_are_not_honored(tmp_path: Path, caplog) -> None:
    """A0b: [[hooks]] in a repo-local pxx.toml is ignored loudly — a file in
    the edit surface cannot define the gate that guards it."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "pxx.toml").write_text('[[hooks]]\nevent = "PreToolUse"\ncommand = "/usr/bin/true"\n')
    with caplog.at_level("WARNING", logger="pxx.config"):
        settings = load_settings(repo)
    assert settings.hooks == ()
    assert any("ignoring hooks" in r.message for r in caplog.records)
