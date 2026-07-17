"""Content change-class candidates — roadmap Phase 16 (content targets).

A *content* candidate rewrites the TEXT that steers the agent — prompt, skill,
and few-shot files — not config and not source. It is the first change-class
that mutates a file, so it crosses the protected boundary that config
candidates (env overlays, zero file contact) never touch.

Safety spine (review requirement #1, the standing review target): the path
that is validated, the path that is written, and the path that is verified
after the write all derive from ONE value — the declared ``target``,
normalized once by ``protected_paths.canonical_repo_path`` — and the
post-write check reads the ACTUAL changed paths from ``git diff --name-only``.
So a write that lands anywhere other than the validated target (or touches a
protected file, or more than one file) fails closed. There is no second path
computation that could disagree with the first.

Allowed targets are behavior text only (``pxx/prompts/``, ``pxx/commands/``).
A prompt IS the agent's policy, so a content candidate can propose changing
the rules the eval runs under — which is exactly why it is never trusted:
validation constrains WHERE it may write; the eval corpus (adversarial cases +
hidden checks) judges WHETHER the rewrite is good; a human promotes.
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from pxx.candidates import ValidationResult
from pxx.protected_paths import canonical_repo_path, is_protected_path

# Content candidates may write ONLY behavior text. Not source, not config,
# not tests, not the gates — those are protected and/or not "content".
CONTENT_TARGET_PREFIXES: tuple[str, ...] = ("pxx/prompts/", "pxx/commands/")


@dataclass(frozen=True)
class ContentCandidate:
    candidate_id: str
    target: str  # the ONE path — validated, written, and verified against
    content: str  # full replacement text for the target file
    baseline_sha256: str | None  # hash of the content it replaces (provenance)
    rationale: str
    from_observation: str
    protected_targets_touched: tuple[str, ...] = field(default_factory=tuple)


def _canonical_target(target: str) -> str | None:
    """The single derivation of the candidate's path — the same normalization
    the protected boundary uses. Returns None (→ reject) when unclassifiable."""
    return canonical_repo_path(target)


def _in_content_allowlist(canonical: str) -> bool:
    return any(canonical.startswith(pre.casefold()) for pre in CONTENT_TARGET_PREFIXES)


def validate_content_candidate(c: ContentCandidate) -> ValidationResult:
    """Fail closed. A content candidate is valid only if its ONE canonical
    target is classifiable, not protected, inside the content allowlist, and
    the proposal is non-empty and evidence-backed."""
    reasons: list[str] = []

    if c.protected_targets_touched:
        reasons.append(
            f"names protected target(s): {', '.join(c.protected_targets_touched)}"
        )

    canonical = _canonical_target(c.target)
    if canonical is None:
        # absolute, empty, backslash-mangled, or repo-escaping → cannot classify
        reasons.append(f"target {c.target!r} is not a safe repo-relative path")
        return ValidationResult(ok=False, reasons=tuple(reasons))

    # The boundary check — same decision the eval content-check uses. A target
    # that normalizes into protected space (incl. via ..) is rejected here.
    if is_protected_path(canonical):
        reasons.append(f"target {c.target!r} resolves into protected space")

    if not _in_content_allowlist(canonical):
        reasons.append(
            f"target {c.target!r} is not behavior text "
            f"(allowed: {', '.join(CONTENT_TARGET_PREFIXES)})"
        )

    if not c.content.strip():
        reasons.append("content is empty — a content candidate must propose text")
    if not c.rationale.strip():
        reasons.append("rationale is required")
    if not c.from_observation.strip():
        reasons.append("from_observation is required (candidates trace to evidence)")

    return ValidationResult(ok=not reasons, reasons=tuple(reasons))


def content_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def apply_content_candidate(repo_root: Path, c: ContentCandidate) -> Path:
    """Write the candidate's content to its ONE canonical target. Refuses to
    apply an invalid candidate (the integrity gate applies at apply time too,
    not only at proposal — a persisted candidate could be hand-edited)."""
    result = validate_content_candidate(c)
    if not result.ok:
        raise ValueError(
            f"refusing to apply invalid content candidate: {result.reasons}"
        )
    canonical = _canonical_target(c.target)
    assert canonical is not None  # validate_content_candidate proved this
    dest = repo_root / canonical
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(c.content, encoding="utf-8")
    return dest


def changed_paths(repo_root: Path, base_sha: str | None = None) -> list[str]:
    """The ACTUAL changed paths, from git's own account — the single source of
    truth for "what did this candidate touch". Uses ``git status --porcelain
    --untracked-files=all`` (modified + staged + UNTRACKED — plain
    ``diff --name-only`` misses new files, and a content write can create one
    in a protected dir), plus committed changes since ``base_sha`` when given.
    A write can't hide by being uncommitted or untracked."""
    paths: set[str] = set()
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    for line in status.stdout.splitlines():
        if line.strip():
            # "XY path" or "XY orig -> path" (rename); take the destination.
            paths.add(line[3:].split(" -> ")[-1].strip())
    if base_sha:
        r = subprocess.run(
            ["git", "diff", "--name-only", f"{base_sha}..HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        paths.update(line.strip() for line in r.stdout.splitlines() if line.strip())
    return sorted(paths)


def verify_only_touched_target(
    repo_root: Path, c: ContentCandidate, base_sha: str | None = None
) -> list[str]:
    """After applying, confirm the candidate touched ONLY its declared target.
    Derives the changed set from git (not from the candidate's own claim), and
    checks each path with is_protected_path — so a write that escaped to a
    protected file, or touched any file other than the target, is caught here
    regardless of what the candidate said. Returns violation messages (empty =
    clean). This is the requirement-#1 check: the verified path comes from the
    same place git wrote, not a re-parse of the candidate."""
    violations: list[str] = []
    canonical = _canonical_target(c.target)
    for path in changed_paths(repo_root, base_sha):
        if is_protected_path(path):
            violations.append(f"touched protected path: {path}")
            continue
        if canonical_repo_path(path) != canonical:
            violations.append(f"touched unexpected path (not the target): {path}")
    return violations
