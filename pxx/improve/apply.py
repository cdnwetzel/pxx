"""Phase 16: the apply → verify envelope for content-like candidates.

Ported from the v1 live-eval envelope (with M0's F2/F3 hardening): a
candidate is applied to a repo, and the envelope PROVES it touched only its
declared target — committed AND worktree changes are read with
``--no-renames`` (a rename can't collapse the source away) and symlinked
targets are rejected before any write.

This is the write-side boundary B8 auto-promotion relies on: candidates are
applied by machines, so the machine must prove the write stayed inside the
declared single file.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..errors import CandidateInvalid, PxxError
from .candidates import Candidate, content_path, validate_candidate

log = logging.getLogger("pxx.improve.apply")


def canonical_repo_path(root: Path | str, rel: str) -> Path:
    """Resolve ``rel`` under ``root`` — symlinks resolved, escapes and
    symlinked targets rejected (fail closed)."""
    root_resolved = Path(root).resolve()
    if Path(root_resolved / rel).is_symlink() or any(
        (root_resolved / parent).is_symlink()
        for parent in Path(rel).parents
        if str(parent) not in ("", ".")
    ):
        raise CandidateInvalid(f"target path traverses a symlink: {rel!r}")
    resolved = (root_resolved / rel).resolve()
    if not str(resolved).startswith(str(root_resolved) + "/"):
        raise CandidateInvalid(f"target escapes the repo root: {rel!r}")
    return resolved


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        raise PxxError(f"git {' '.join(args)} failed in {root}: {proc.stderr.strip()[:200]}")
    return proc.stdout


def changed_paths(root: Path | str) -> set[str]:
    """All paths changed vs HEAD — committed AND worktree, rename detection
    OFF so a rename surfaces as delete+add (M0 F2 + F3)."""
    root = Path(root)
    paths: set[str] = set()
    out = _git(root, "diff", "--name-only", "--no-renames", "HEAD")
    paths.update(n for n in out.splitlines() if n.strip())
    out = _git(root, "status", "--porcelain", "--no-renames", "--untracked-files=all")
    for line in out.splitlines():
        if not line.strip():
            continue
        path = line[3:] if len(line) > 3 else line.strip()
        if " -> " in path:  # rename slipped through: keep BOTH endpoints
            src, _, dst = path.partition(" -> ")
            paths.add(src.strip('"'))
            paths.add(dst.strip('"'))
            continue
        paths.add(path.strip('"'))
    return paths


@dataclass(frozen=True)
class ApplyResult:
    """The verified outcome of applying one candidate."""

    candidate_id: str
    target: str  # repo-relative path that was written
    touched: tuple[str, ...]  # everything that changed (must be == {target})


def apply_candidate(candidate: Candidate, root: Path | str) -> ApplyResult:
    """Re-validate, write ONLY the candidate's declared target, then verify
    that nothing else changed (committed or worktree, rename-proof).

    Raises CandidateInvalid on any validation failure, and PxxError when the
    post-write verification finds the write escaped its target.
    """
    validate_candidate(candidate)  # re-validate: persisted input is untrusted
    root = Path(root)
    rel = content_path(candidate)  # derived ONCE: validated == written == verified
    dest = canonical_repo_path(root, rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(str(candidate.value), encoding="utf-8")
    touched = changed_paths(root)
    if not touched <= {rel}:
        raise PxxError(
            f"candidate {candidate.id!r} touched paths beyond its declared "
            f"target {rel!r}: {sorted(touched - {rel})} (refusing)"
        )
    return ApplyResult(candidate_id=candidate.id, target=rel, touched=tuple(sorted(touched)))


def restore_target(root: Path | str, rel: str) -> None:
    """Undo an apply: restore the target to its HEAD state (or remove it if
    it was created by the candidate)."""
    root = Path(root)
    proc = subprocess.run(
        ["git", "checkout", "--", rel],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        # path may be untracked (created by the candidate) — remove it
        target = root / rel
        if target.is_file() and not target.is_symlink():
            target.unlink()
            subprocess.run(
                ["git", "clean", "-f", "--", rel],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )


__all__ = [
    "ApplyResult",
    "apply_candidate",
    "canonical_repo_path",
    "changed_paths",
    "restore_target",
]
