"""Per-project workflow state for the generate → review → approve cycle (#020)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

WORKFLOW_DIR = ".pxx"
WORKFLOW_FILE = "workflow_state.json"
SCHEMA_VERSION = 1


@dataclass
class WorkflowState:
    version: int = SCHEMA_VERSION
    phase: str = "idle"  # idle|generating|review_pending|approved|rejected
    session_id: str | None = None
    session_start_sha: str | None = None
    session_end_sha: str | None = None
    review_verdict: str | None = None  # APPROVE|REVISE|REJECT
    review_pass_sha: str | None = None
    scope: list[str] = field(default_factory=list)
    edit_mode: bool = False
    autonomous: bool = False
    ts_phase_changed: str = ""
    healing_attempts: int = 0
    run_id: str | None = None  # behavior identity (#011)
    agent_version_id: str | None = None


def state_path(repo_root: Path) -> Path:
    return repo_root / WORKFLOW_DIR / WORKFLOW_FILE


def load_state(repo_root: Path) -> WorkflowState | None:
    path = state_path(repo_root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data.pop("version", None)
        known = {f for f in WorkflowState.__dataclass_fields__}
        return WorkflowState(**{k: v for k, v in data.items() if k in known})
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def save_state(state: WorkflowState, repo_root: Path) -> None:
    dir_path = repo_root / WORKFLOW_DIR
    dir_path.mkdir(exist_ok=True)
    path = state_path(repo_root)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")
    os.replace(tmp, path)  # atomic on POSIX


def transition(state: WorkflowState, new_phase: str, **updates) -> WorkflowState:
    from pxx import audit

    d = asdict(state)
    d["phase"] = new_phase
    d["ts_phase_changed"] = audit.now_iso()
    d.update(updates)
    return WorkflowState(**d)


def resume_state(repo_root: Path) -> int:
    state = load_state(repo_root)
    if state is None or state.phase == "idle":
        print("pxx: nothing to resume.", file=sys.stderr)
        return 0

    if state.phase == "generating":
        commits = _commits_since(repo_root, state.session_start_sha)
        if commits:
            new_state = transition(
                state, "review_pending", session_end_sha=_head_sha(repo_root)
            )
            save_state(new_state, repo_root)
            print(
                f"pxx: session produced {len(commits)} commit(s). "
                "Run `pxx --review` to evaluate.",
                file=sys.stderr,
            )
        else:
            save_state(transition(state, "idle"), repo_root)
            print("pxx: prior session made no commits. State cleared.", file=sys.stderr)
        return 0

    if state.phase == "review_pending":
        verdict = state.review_verdict or "(none yet)"
        print(
            f"pxx: review pending — verdict: {verdict}. "
            "Run `pxx --review` to run a review pass.",
            file=sys.stderr,
        )
        return 0

    if state.phase == "approved":
        rng = (
            f"{state.session_start_sha[:7]}..{state.session_end_sha[:7]}"
            if state.session_start_sha and state.session_end_sha
            else "(unknown range)"
        )
        print(f"pxx: session approved. Commits: {rng}.", file=sys.stderr)
        save_state(transition(state, "idle"), repo_root)
        return 0

    if state.phase == "rejected":
        if state.review_verdict == "NO_REVIEW":
            # Healing NO_REVIEW is nonsensical — there are no findings to feed
            # back; the remedy is producing a review, not an edit round.
            remedy = "Run `pxx --review` to produce review evidence first."
        else:
            remedy = (
                "Run `pxx --review --heal --scope <path>` to feed findings "
                "back to aider, or `pxx --edit` to address manually."
            )
        print(
            f"pxx: session rejected (attempt {state.healing_attempts}). {remedy}",
            file=sys.stderr,
        )
        return 1

    return 0


def _commits_since(repo_root: Path, base_sha: str | None) -> list[str]:
    if not base_sha:
        return []
    try:
        r = subprocess.run(
            ["git", "log", "--oneline", f"{base_sha}..HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=3,
        )
        return [ln for ln in r.stdout.strip().splitlines() if ln]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []


def _head_sha(repo_root: Path) -> str | None:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=3,
        )
        return r.stdout.strip() or None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
