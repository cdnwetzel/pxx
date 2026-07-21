"""Tests for pxx.improve.apply: the apply→verify envelope (B4.3)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from pxx.errors import CandidateInvalid
from pxx.improve.apply import (
    apply_candidate,
    canonical_repo_path,
    changed_paths,
    restore_target,
)
from pxx.improve.candidates import CandidateClass, make_candidate

GIT = shutil.which("git")
needs_git = pytest.mark.skipif(GIT is None, reason="git not available")


def _git(root: Path, *args: str) -> None:
    subprocess.run([GIT, *args], cwd=root, check=True, capture_output=True)


def _init_repo(path: Path, files: dict[str, str] | None = None) -> None:
    _git(path, "init", "-q")
    for rel, content in (files or {}).items():
        target = path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    _git(path, "add", "-A")
    _git(
        path,
        "-c",
        "user.name=test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "init",
    )


def _candidate(target: str, value: str = "# new prompt\n"):
    return make_candidate("c1", CandidateClass.CONTENT, target, value, "better prompt", ("run-1",))


@needs_git
def test_apply_touches_only_declared_target(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, files={"pxx/prompts/review.md": "old\n"})
    result = apply_candidate(_candidate("pxx/prompts/review.md"), repo)
    assert result.target == "pxx/prompts/review.md"
    assert result.touched == ("pxx/prompts/review.md",)
    assert changed_paths(repo) == {"pxx/prompts/review.md"}


@needs_git
def test_apply_creates_new_target_file(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    result = apply_candidate(_candidate("pxx/prompts/new.md"), repo)
    assert (repo / "pxx/prompts/new.md").read_text() == "# new prompt\n"
    assert result.touched == ("pxx/prompts/new.md",)


@needs_git
def test_apply_rejects_protected_and_traversal(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    with pytest.raises(CandidateInvalid):
        apply_candidate(_candidate("pxx/safety.py"), repo)
    with pytest.raises(CandidateInvalid):
        apply_candidate(_candidate("../evil.md"), repo)


@needs_git
def test_symlink_target_rejected(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    outside = tmp_path / "outside.md"
    outside.write_text("x\n")
    (repo / "pxx").mkdir()
    (repo / "pxx/prompts").mkdir()
    (repo / "pxx/prompts" / "link.md").symlink_to(outside)
    with pytest.raises(CandidateInvalid, match="symlink"):
        canonical_repo_path(repo, "pxx/prompts/link.md")


@needs_git
def test_rename_escape_is_caught(tmp_path: Path) -> None:
    """M0 F3 in the envelope: a rename committed after the write surfaces
    BOTH paths, so only-touched-target verification refuses it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, files={"pxx/prompts/review.md": "old\n"})
    apply_candidate(_candidate("pxx/prompts/review.md"), repo)
    # simulate an escape AFTER the write: rename a protected file into the repo
    (repo / "pxx/safety.py").write_text("x\n")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@e.c", "commit", "-q", "-m", "plant")
    _git(repo, "mv", "pxx/safety.py", "pxx/prompts/moved.md")
    changed = changed_paths(repo)
    assert "pxx/safety.py" in changed and "pxx/prompts/moved.md" in changed
    assert not changed <= {"pxx/prompts/review.md"}


@needs_git
def test_restore_target(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, files={"pxx/prompts/review.md": "old\n"})
    apply_candidate(_candidate("pxx/prompts/review.md"), repo)
    restore_target(repo, "pxx/prompts/review.md")
    assert (repo / "pxx/prompts/review.md").read_text() == "old\n"
    apply_candidate(_candidate("pxx/prompts/new.md"), repo)
    restore_target(repo, "pxx/prompts/new.md")
    assert not (repo / "pxx/prompts/new.md").exists()


@needs_git
def test_tampered_candidate_revalidated_on_apply(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    candidate = _candidate("pxx/prompts/review.md")
    tampered = type(candidate)(
        id=candidate.id,
        change_class=candidate.change_class,
        target=candidate.target,
        value=candidate.value,
        rationale=candidate.rationale,
        evidence=candidate.evidence,
        content_hash="0" * 64,  # hand-edited
        baseline_budgets=candidate.baseline_budgets,
    )
    with pytest.raises(CandidateInvalid, match="tampered"):
        apply_candidate(tampered, repo)
