"""Content change-class candidates — roadmap Phase 16 (content targets).

A *content* candidate rewrites the TEXT that steers the agent — prompt, skill,
and few-shot files — not config and not source. It is the first change-class
that mutates a file, so it crosses the protected boundary that config
candidates (env overlays, zero file contact) never touch.

Safety spine (review requirement #1, the standing review target): the path
that is validated, the path that is written, and the path that is verified
after the write all derive from ONE value — the declared ``target``,
normalized once by ``protected_paths.canonical_repo_path`` — and the
post-write check reads the ACTUAL changed paths from git (``git status
--porcelain -z`` for the working tree plus ``git diff`` since the pre-write
HEAD, since the live sweep auto-commits). So a write that lands anywhere other
than the validated target (or touches a protected file, or more than one file)
fails closed, whether it stayed uncommitted or was already committed. There is
no second path computation that could disagree with the first.

Allowed targets are behavior text only (``pxx/prompts/``, ``pxx/commands/``).
A prompt IS the agent's policy, so a content candidate can propose changing
the rules the eval runs under — which is exactly why it is never trusted:
validation constrains WHERE it may write; the eval corpus (adversarial cases +
hidden checks) judges WHETHER the rewrite is good; a human promotes.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from collections.abc import Callable
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


@dataclass(frozen=True)
class AppliedContent:
    dest: Path  # the case-preserving path actually written
    base_sha: str  # pre-write HEAD — REQUIRED input to verify_only_touched_target


def _canonical_target(target: str) -> str | None:
    """The single derivation of the candidate's path — the same normalization
    the protected boundary uses. Returns None (→ reject) when unclassifiable."""
    return canonical_repo_path(target)


def _in_content_allowlist(canonical: str) -> bool:
    cf = canonical.casefold()  # canonical is case-preserving; fold at comparison
    return any(cf.startswith(pre.casefold()) for pre in CONTENT_TARGET_PREFIXES)


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


def _head_sha(repo_root: Path) -> str:
    r = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    return r.stdout.strip()


def apply_content_candidate(repo_root: Path, c: ContentCandidate) -> AppliedContent:
    """Write the candidate's content to its ONE canonical target and return the
    written path plus the PRE-write HEAD. Refuses to apply an invalid candidate
    (the integrity gate applies at apply time too, not only at proposal — a
    persisted candidate could be hand-edited).

    The returned ``base_sha`` is the required input to
    ``verify_only_touched_target``: the live sweep auto-commits, so verify must
    diff against the moment before the write, not rely on the tree being dirty
    (a committed escape would otherwise be invisible)."""
    result = validate_content_candidate(c)
    if not result.ok:
        raise ValueError(
            f"refusing to apply invalid content candidate: {result.reasons}"
        )
    canonical = _canonical_target(c.target)
    assert canonical is not None  # validate_content_candidate proved this

    dest = repo_root / canonical  # case-preserving — write to the declared casing
    # write_text FOLLOWS symlinks: a planted link inside the allowlisted dir
    # (or a symlinked parent) could land the write ON the grader. Reject any
    # destination whose real location isn't exactly where the canonical target
    # says it should be — checked BEFORE the write, so the grader is never
    # tampered even within the write→verify window.
    expected_parent = (repo_root.resolve() / canonical).parent
    if dest.is_symlink():
        raise ValueError(f"refusing to write through a symlink: {c.target!r}")
    if dest.parent.exists() and dest.parent.resolve() != expected_parent:
        raise ValueError(
            f"refusing: path parent redirects outside the target: {c.target!r}"
        )

    base_sha = _head_sha(repo_root)  # capture BEFORE the write
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(c.content, encoding="utf-8")
    return AppliedContent(dest=dest, base_sha=base_sha)


def changed_paths(repo_root: Path, base_sha: str | None = None) -> list[str]:
    """The ACTUAL changed paths, from git's own account — the single source of
    truth for "what did this candidate touch". Uses ``git status --porcelain -z
    --untracked-files=all`` (modified + staged + UNTRACKED — plain
    ``diff --name-only`` misses new files, and a content write can create one
    in a protected dir), plus committed changes since ``base_sha`` when given.
    A write can't hide by being uncommitted, untracked, or already committed.

    ``-z`` (NUL-separated) is load-bearing: without it git C-quotes and octal-
    escapes any path with a space or non-ASCII byte, and those quotes would
    survive into ``canonical_repo_path`` → a spurious "unexpected path".

    ``--no-renames`` is a SAFETY invariant, not an optimization: with rename
    detection ON (git's default for ``diff``, and available to ``status``), a
    ``D pxx/review_gate.py`` + ``A <allowed-target>`` pair collapses into a
    single ``R100 review_gate.py -> <allowed-target>`` and the protected
    DELETION never reaches ``is_protected_path`` — a fail-open escape (a
    poisoned prompt does ``git mv`` a grader onto its declared target). Forcing
    both reads to report a rename as separate ``D <source>`` + ``A <dest>``
    keeps the protected ``D`` visible. The two flags MUST stay together on
    :status: below — dropping ``--no-renames`` re-opens this exact hole via the
    rename-source skip branch."""
    paths: set[str] = set()
    status = subprocess.run(
        ["git", "status", "--porcelain", "--no-renames", "-z", "--untracked-files=all"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    tokens = [t for t in status.stdout.split("\0") if t]
    i = 0
    while i < len(tokens):
        entry = tokens[i]
        xy, path = entry[:2], entry[3:]  # "XY <path>"; path is raw (no quoting)
        if path:
            paths.add(path)
        # Dead while --no-renames is set (no R/C entries), kept belt-and-
        # suspenders: if that flag is ever dropped, this skip alone would hide
        # the rename SOURCE (the protected path) again. Tied to the flag above.
        if "R" in xy or "C" in xy:
            i += 1  # rename/copy: the next NUL field is the source — skip it
        i += 1
    if base_sha:
        r = subprocess.run(
            ["git", "diff", "--no-renames", "--name-only", "-z", f"{base_sha}..HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        paths.update(t for t in r.stdout.split("\0") if t)
    return sorted(paths)


def verify_only_touched_target(
    repo_root: Path, c: ContentCandidate, base_sha: str
) -> list[str]:
    """After applying, confirm the candidate touched ONLY its declared target.
    Derives the changed set from git (not from the candidate's own claim), and
    checks each path with is_protected_path — so a write that escaped to a
    protected file, or touched any file other than the target, is caught here
    regardless of what the candidate said. Returns violation messages (empty =
    clean). This is the requirement-#1 check: the verified path comes from the
    same place git wrote, not a re-parse of the candidate.

    ``base_sha`` is REQUIRED (no default): the live sweep auto-commits, so a
    tree-only check would see a committed escape as clean → a vacuous pass.
    Pass ``AppliedContent.base_sha`` from the matching apply so the pre-write
    HEAD and the verify can't be mismatched.

    The required arg stops a DROPPED sha, not a WRONG one — a ``git rev-parse``
    taken AFTER the auto-commit is a valid string whose ``base..HEAD`` diff is
    empty, and an all-negative check passes an empty set vacuously. So this also
    verifies POSITIVELY (G1): the declared target must APPEAR in the changed
    set. An empty or target-absent set is a violation ("nothing to evaluate"),
    not a clean pass — closing the vacuous class regardless of how ``base_sha``
    was derived."""
    violations: list[str] = []
    canonical = _canonical_target(c.target)
    cf_target = canonical.casefold() if canonical is not None else None
    target_seen = False
    for path in changed_paths(repo_root, base_sha):
        if is_protected_path(path):
            violations.append(f"touched protected path: {path}")
            continue
        cp = canonical_repo_path(path)
        # canonical is case-preserving; fold both sides symmetrically for the
        # boundary decision (same policy is_protected_path uses).
        if cp is not None and cf_target is not None and cp.casefold() == cf_target:
            target_seen = True
        else:
            violations.append(f"touched unexpected path (not the target): {path}")
    if not target_seen:
        violations.append(
            f"expected target not in changed set (nothing to evaluate): {c.target}"
        )
    return violations


# --- Increment 1: live-eval envelope --------------------------------------
#
# Wires a content candidate into the live sweep: clone → apply → run → verify
# → restore. The loop runner is INJECTED (as in candidate_eval.ArmRunner) so
# the orchestration is unit-testable without driving a real loop; the CLI wires
# the live loop. Two structural guards ride here (order CR-...-increment1):
#   G2 — verify is called with apply's OWN base_sha, never a fresh rev-parse.
#   G3 — the fixture must be clean before apply; a dirty tree false-flags verify
#        and can mask a real escape in the noise. Assert it, fail loud.

# runner(fixture_root, applied) -> loop return code (0 == success).
ContentLoopRunner = Callable[[Path, "AppliedContent"], int]


@dataclass(frozen=True)
class ContentEvalResult:
    candidate_id: str
    base_sha: str  # apply's pre-write HEAD — the one threaded into verify (G2)
    loop_rc: int
    violations: list[str]
    ok: bool


def _worktree_dirty(repo_root: Path) -> str:
    """The porcelain listing of any pending change, or '' when clean. Uses -z
    only to be consistent with changed_paths; the raw text is for the message."""
    r = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    return r.stdout.strip()


def clone_repo_for_content_eval(repo_src: Path, into: Path | None = None) -> Path:
    """A fresh, clean local clone of ``repo_src`` to evaluate a content
    candidate against — the live repo is never touched. ``--local`` is cheap
    (hardlinked objects). The caller restores by discarding the returned dir.

    A fixture git identity is set on the clone: ``git clone`` does not copy the
    source's local config, and the loop commits inside the fixture — without an
    identity those commits fail (exit 128) on any host lacking a global one
    (e.g. CI)."""
    dest = into or Path(tempfile.mkdtemp(prefix="pxx-content-eval-"))
    subprocess.run(
        ["git", "clone", "--quiet", "--local", str(repo_src), str(dest)],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    for key, val in (("user.email", "eval@pxx"), ("user.name", "pxx-eval")):
        subprocess.run(
            ["git", "config", key, val],
            cwd=dest,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
    return dest


def run_content_candidate_in_fixture(
    fixture_root: Path, c: ContentCandidate, runner: ContentLoopRunner
) -> ContentEvalResult:
    """Apply the candidate in a CLEAN fixture, run the loop, then verify only
    the target was touched — the safety envelope of the live sweep.

    G3: the fixture must start clean. A pre-dirtied tree is rejected BEFORE the
    write (a false positive in the changed set could otherwise mask a real
    escape). G2: verify is called with ``applied.base_sha`` — the pre-write HEAD
    apply captured — so a loop that auto-commits an escape is still caught,
    with no seam for a re-derived sha to slip through."""
    dirty = _worktree_dirty(fixture_root)
    if dirty:
        raise RuntimeError(
            f"fixture is not clean before apply (would mask escapes): {dirty!r}"
        )
    applied = apply_content_candidate(fixture_root, c)
    loop_rc = runner(fixture_root, applied)
    violations = verify_only_touched_target(fixture_root, c, applied.base_sha)
    return ContentEvalResult(
        candidate_id=c.candidate_id,
        base_sha=applied.base_sha,
        loop_rc=loop_rc,
        violations=violations,
        ok=(loop_rc == 0 and not violations),
    )


def evaluate_content_candidate(
    repo_src: Path,
    c: ContentCandidate,
    runner: ContentLoopRunner,
    *,
    keep: bool = False,
) -> ContentEvalResult:
    """Full envelope: clean-clone ``repo_src`` → apply → run → verify →
    restore. The clone isolates the live repo; it is discarded (restore) unless
    ``keep``. Validation runs inside apply, so an invalid candidate raises
    before any fixture work matters."""
    fixture = clone_repo_for_content_eval(repo_src)
    try:
        return run_content_candidate_in_fixture(fixture, c, runner)
    finally:
        if not keep:
            shutil.rmtree(fixture, ignore_errors=True)
