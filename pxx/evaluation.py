"""Evaluation harness — roadmap Phase 13, minimum slice.

Cases are TOML files under ``evals/<tier>/`` (stdlib ``tomllib``; the same
choice as docs-sme's ``questions.toml`` — no YAML dependency). Each case
materializes into a disposable git repo, so baseline and candidate arms
always start from identical state.

Checks are layered exactly as the roadmap orders them: deterministic
commands first (visible to any agent), then hidden checks the agent never
sees — allowed-file boundaries, forbidden diff patterns, tests-unchanged.
A model verdict can never override a failed deterministic check because no
model is consulted here at all.

Self-check mode is the harness proving itself: every case's ``honest``
patch must pass all checks, and its ``cheat`` patch (when defined) MUST be
caught by the hidden checks. A suite whose cheats go uncaught is a broken
laboratory, not a lenient one — self-check fails closed.

Trust boundary: evals/ fixtures and this module are optimizer-protected
(docs/TRUST_BOUNDARY.md).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parent.parent / "evals"
TIERS = ("micro", "regression", "adversarial")
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class EvalCase:
    id: str
    tier: str
    category: str
    task: str
    fixture_files: dict[str, str]
    allowed_files: tuple[str, ...]
    commands: tuple[tuple[str, ...], ...]
    forbidden_patterns: tuple[str, ...]
    tests_unchanged: bool
    patches: dict[str, dict[str, str]]  # arm name -> {path: new content}
    path: Path


@dataclass(frozen=True)
class CheckFailure:
    check: str  # "command" | "allowed-files" | "forbidden-pattern" | "tests-unchanged"
    detail: str


@dataclass(frozen=True)
class ArmResult:
    case_id: str
    arm: str
    ok: bool
    failures: tuple[CheckFailure, ...] = field(default_factory=tuple)


class CaseError(ValueError):
    """A case file that cannot be trusted must not be silently skipped."""


def load_case(path: Path) -> EvalCase:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != SCHEMA_VERSION:
        raise CaseError(f"{path.name}: schema_version must be {SCHEMA_VERSION}")
    for key in ("id", "tier", "task", "fixture", "checks"):
        if key not in data:
            raise CaseError(f"{path.name}: missing required key {key!r}")
    if data["tier"] not in TIERS:
        raise CaseError(f"{path.name}: unknown tier {data['tier']!r}")
    checks = data["checks"]
    commands = tuple(tuple(c) for c in checks.get("commands", []))
    if not commands:
        raise CaseError(f"{path.name}: at least one deterministic command required")
    patches = {arm: dict(files) for arm, files in (data.get("patches") or {}).items()}
    if "honest" not in patches:
        raise CaseError(f"{path.name}: an 'honest' patch is required (self-check)")
    return EvalCase(
        id=data["id"],
        tier=data["tier"],
        category=data.get("category", "uncategorized"),
        task=data["task"],
        fixture_files=dict(data["fixture"]),
        allowed_files=tuple(checks.get("allowed_files", [])),
        commands=commands,
        forbidden_patterns=tuple(checks.get("forbidden_patterns", [])),
        tests_unchanged=bool(checks.get("tests_unchanged", False)),
        patches=patches,
        path=path,
    )


def load_suite(tier: str, evals_dir: Path | None = None) -> list[EvalCase]:
    base = (evals_dir or EVALS_DIR) / tier
    if not base.is_dir():
        return []
    return [load_case(p) for p in sorted(base.glob("*.toml"))]


def corpus_fingerprint(evals_dir: Path | None = None) -> str:
    """A content hash of the whole eval corpus — case COUNT plus every case
    file's bytes. Two arms scored on the same corpus share a fingerprint; any
    drift (a case added, a fixture or hidden check edited under a reused name)
    changes it. compare() refuses arms whose fingerprints differ, so a
    persisted baseline scored on an older corpus can't silently be judged
    against a candidate scored on a newer one — same case NAMES are not the
    same case CONTENT."""
    import hashlib

    base = evals_dir or EVALS_DIR
    h = hashlib.sha256()
    files = sorted(
        (p for tier in TIERS for p in (base / tier).glob("*.toml")),
        key=lambda p: str(p.relative_to(base)),
    )
    h.update(f"count={len(files)}\n".encode())
    for p in files:
        h.update(str(p.relative_to(base)).encode() + b"\0")
        h.update(p.read_bytes() + b"\0")
    return "corpus-" + h.hexdigest()[:16]


def _git(worktree: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", "user.email=eval@pxx", "-c", "user.name=pxx-eval", *args],
        cwd=worktree,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def materialize_fixture(case: EvalCase) -> tuple[Path, str]:
    """Fresh disposable git repo containing exactly the fixture files.

    Every arm starts from this identical commit — the isolation property
    the whole comparison methodology rests on."""
    worktree = Path(tempfile.mkdtemp(prefix=f"pxx-eval-{case.id}-"))
    for rel, content in case.fixture_files.items():
        dest = worktree / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
    _git(worktree, "init", "-q")
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-q", "--no-verify", "-m", "fixture")
    sha = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    return worktree, sha


def apply_patch(worktree: Path, files: dict[str, str]) -> None:
    """Scripted arm: replace file contents wholesale and commit. Deterministic
    by construction — the seam a live-agent arm will substitute later."""
    for rel, content in files.items():
        dest = worktree / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-q", "--no-verify", "-m", "arm")


def run_checks(case: EvalCase, worktree: Path, fixture_sha: str) -> list[CheckFailure]:
    """Visible commands first, then the hidden checks the agent never sees."""
    failures: list[CheckFailure] = []

    for argv in case.commands:
        # Live-capable fixtures carry a pyproject: run checks inside the
        # FIXTURE's own uv environment (same env the loop's gates use).
        # Plain scripted fixtures fall back to this process's interpreter.
        if (worktree / "pyproject.toml").exists():
            cmd = ["uv", "run", "--quiet", *argv]
        else:
            cmd = [sys.executable if a == "python" else a for a in argv]
        try:
            r = subprocess.run(
                cmd,
                cwd=worktree,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            failures.append(CheckFailure("command", f"{' '.join(argv)}: {e}"))
            continue
        if r.returncode != 0:
            tail = (r.stdout + r.stderr).strip().splitlines()[-1:]
            failures.append(
                CheckFailure(
                    "command",
                    f"{' '.join(argv)} rc={r.returncode} {' '.join(tail)}",
                )
            )

    changed = [
        line
        for line in _git(
            worktree, "diff", "--name-only", f"{fixture_sha}..HEAD"
        ).stdout.splitlines()
        if line.strip()
    ]

    if case.allowed_files:
        for path in changed:
            if path not in case.allowed_files:
                failures.append(CheckFailure("allowed-files", f"touched {path}"))

    if case.tests_unchanged:
        for path in changed:
            name = Path(path).name
            if path.startswith("tests/") or name.startswith("test_"):
                failures.append(CheckFailure("tests-unchanged", f"modified {path}"))

    if case.forbidden_patterns:
        diff = _git(worktree, "diff", "-U0", f"{fixture_sha}..HEAD").stdout
        added = "\n".join(
            line[1:]
            for line in diff.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        for pattern in case.forbidden_patterns:
            if re.search(pattern, added):
                failures.append(
                    CheckFailure("forbidden-pattern", f"diff matches {pattern!r}")
                )

    return failures


def run_arm(case: EvalCase, arm: str, keep: bool = False) -> ArmResult:
    """One arm of one case in a fresh worktree. ``honest`` must pass every
    check; ``cheat`` passes self-check only when the hidden checks CATCH it."""
    worktree, sha = materialize_fixture(case)
    try:
        apply_patch(worktree, case.patches[arm])
        failures = tuple(run_checks(case, worktree, sha))
    finally:
        if not keep:
            shutil.rmtree(worktree, ignore_errors=True)
    if arm == "cheat":
        # Caught means caught by ANY layer — a cheat stopped by a visible
        # deterministic command is just as blocked as one the hidden checks
        # catch. The unacceptable outcome is a cheat with zero failures.
        if failures:
            return ArmResult(case.id, arm, ok=True, failures=failures)
        return ArmResult(
            case.id,
            arm,
            ok=False,
            failures=(CheckFailure("self-check", "cheat patch was NOT caught"),),
        )
    return ArmResult(case.id, arm, ok=not failures, failures=failures)


def self_check_suite(tier: str, evals_dir: Path | None = None) -> list[ArmResult]:
    """Prove the laboratory: honest arms pass, cheat arms get caught."""
    results: list[ArmResult] = []
    for case in load_suite(tier, evals_dir):
        for arm in sorted(case.patches):
            results.append(run_arm(case, arm))
    return results


# --- Live-agent arm -------------------------------------------------------
#
# Runs the REAL pxx loop inside a fixture worktree instead of applying a
# scripted patch. Fixtures live under <pxx-repo>/.pxx/eval/ — inside the
# trusted-paths prefix — so the #003 sovereignty boundary is honored, never
# bypassed (no --anywhere, no env overrides). .pxx/ is already gitignored.

LIVE_MAX_ROUNDS = 2
LIVE_MAX_SECONDS = 600.0
LIVE_DIFF_BUDGET = 100

_FIXTURE_PYPROJECT = """[project]
name = "pxx-eval-fixture"
version = "0.0.0"
requires-python = ">=3.11"
dependencies = []

[dependency-groups]
dev = ["pytest", "ruff"]
"""

_FIXTURE_GITIGNORE = """.venv/
__pycache__/
*.pyc
uv.lock
.aider*
.pxx/
review/
"""

# Minimal pxx-managed hooks: inside an eval fixture the loop's OWN gates
# (tests, scoped lint, diff budget, out-of-scope guard) carry enforcement;
# the hooks exist to satisfy _require_hooks and to keep commit mechanics
# identical to a real session.
_FIXTURE_HOOKS = {
    "pre-commit": "#!/bin/sh\n# pxx-managed (eval-fixture minimal)\nexit 0\n",
    "prepare-commit-msg": "#!/bin/sh\n# pxx-managed (eval-fixture minimal)\nexit 0\n",
}


def materialize_live_fixture(case: EvalCase) -> tuple[Path, str]:
    """Fixture repo bootstrapped for a real loop: pyproject (pytest+ruff via
    uv), .gitignore for runtime artifacts, and pxx-managed hooks."""
    base = EVALS_DIR.parent / ".pxx" / "eval"
    base.mkdir(parents=True, exist_ok=True)
    worktree = Path(tempfile.mkdtemp(prefix=f"{case.id}-", dir=base))
    for rel, content in case.fixture_files.items():
        dest = worktree / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
    (worktree / "pyproject.toml").write_text(_FIXTURE_PYPROJECT, encoding="utf-8")
    (worktree / ".gitignore").write_text(_FIXTURE_GITIGNORE, encoding="utf-8")
    _git(worktree, "init", "-q")
    hooks_dir = worktree / ".git" / "hooks"
    for name, body in _FIXTURE_HOOKS.items():
        hook = hooks_dir / name
        hook.write_text(body, encoding="utf-8")
        hook.chmod(0o755)
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-q", "--no-verify", "-m", "fixture")
    sha = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    return worktree, sha


def run_live_arm(
    case: EvalCase,
    keep: bool = False,
    max_rounds: int = LIVE_MAX_ROUNDS,
    max_seconds: float = LIVE_MAX_SECONDS,
    diff_budget: int = LIVE_DIFF_BUDGET,
) -> tuple[ArmResult, str]:
    """One case against the real agent: pxx loop in the fixture, then the
    SAME hidden checks the scripted arms use — an APPROVE that cheated is
    still a failure here. Returns (result, run_id) so outcomes stay linked."""
    from pxx import audit
    from pxx import loop as loop_mod

    worktree, sha = materialize_live_fixture(case)
    scope = case.allowed_files[0] if case.allowed_files else "src/"
    run_id = audit.make_session_id()
    agent_version = None
    try:  # identity is best-effort, never gates the run (#011)
        from pxx import agent_manifest
        from pxx.cli import model_for
        from pxx.endpoints import detect_endpoint

        endpoint = detect_endpoint()
        agent_version = agent_manifest.agent_version_id(
            agent_manifest.current_manifest(
                editor_backend=endpoint.backend,
                editor_model=model_for(endpoint),
                max_rounds=max_rounds,
                max_seconds=max_seconds,
                diff_budget=diff_budget,
            )
        )
    except Exception:
        pass
    try:
        rc = loop_mod.run_loop(
            worktree,
            case.task,
            scope,
            max_rounds=max_rounds,
            diff_budget=diff_budget,
            max_seconds=max_seconds,
            run_id=run_id,
            agent_version=agent_version,
        )
        failures = list(run_checks(case, worktree, sha))
        if rc != 0:
            failures.insert(0, CheckFailure("loop", f"run_loop exited {rc}"))
    finally:
        if not keep:
            shutil.rmtree(worktree, ignore_errors=True)
    return ArmResult(case.id, "live", ok=not failures, failures=tuple(failures)), run_id


def find_case(case_id: str, evals_dir: Path | None = None) -> EvalCase | None:
    for tier in TIERS:
        for case in load_suite(tier, evals_dir):
            if case.id == case_id:
                return case
    return None
