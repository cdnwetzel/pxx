"""Phase 13.3/13.4: materialize cases into disposable git repos and check them.

Flow per arm (honest / cheat / backend):

1. ``materialize_case`` writes the fixture into a fresh tmp dir, ``git init``,
   commits (with ``-c user.name/email`` so no global git config is needed).
2. A unified diff is applied with :func:`apply_patch` — pure python, no
   ``patch`` binary, no ``git apply``.
3. ``run_checks`` evaluates the case's deterministic gates: visible +
   hidden commands (subprocess, bounded timeout, output captured and
   discarded — results are metadata-only), forbidden patterns against the
   diff, an allowed-files whitelist on changed paths, and tests-unchanged
   pinning of ``fixture.starting_tests``.

``self_check`` runs both patch arms: the honest patch must pass every check
and the cheat patch must be caught by at least one. ``run_case`` evaluates a
backend arm; without a ``backend_factory`` it uses the scripted arm (the
honest patch stands in for a perfect agent) which is what CI exercises.

Check details are deterministic metadata (exit codes, relative paths,
pattern text) — never command output or absolute tmp paths — so repeated
runs produce byte-identical reports.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .cases import Case, EvalError

log = logging.getLogger("pxx.eval.harness")

__all__ = [
    "CaseResult",
    "CheckResult",
    "SelfCheckResult",
    "apply_patch",
    "materialize_case",
    "run_case",
    "run_checks",
    "self_check",
]

DEFAULT_TIMEOUT = 60.0

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass(frozen=True)
class CheckResult:
    """Outcome of one deterministic check. Detail is metadata-only."""

    name: str
    ok: bool
    detail: str = ""


@dataclass(frozen=True)
class SelfCheckResult:
    """Honest arm must pass all checks; cheat arm must fail at least one."""

    case_id: str
    honest_ok: bool
    cheat_caught: bool
    honest_failures: tuple[str, ...] = ()
    cheat_failures: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.honest_ok and self.cheat_caught


@dataclass(frozen=True)
class CaseResult:
    """Result of running a case against one arm (scripted or backend)."""

    case_id: str
    passed: bool
    checks: tuple[CheckResult, ...] = ()

    @property
    def failed_checks(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.checks if not c.ok)


# --- git materialization ------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    git = shutil.which("git")
    if git is None:
        raise EvalError("git executable not found")
    proc = subprocess.run(
        [git, *args],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        raise EvalError(f"git {' '.join(args[:1])} failed: exit {proc.returncode}")
    return proc.stdout


def materialize_case(case: Case, dest: str | Path) -> Path:
    """Write the fixture into ``dest`` and commit it as the base revision."""
    repo = Path(dest)
    repo.mkdir(parents=True, exist_ok=True)
    for rel, content in case.fixture.files:
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(
        repo,
        "-c",
        "user.name=pxx-eval",
        "-c",
        "user.email=pxx-eval@localhost",
        "commit",
        "-q",
        "--no-verify",
        "-m",
        f"fixture for {case.id}",
    )
    return repo


# --- pure-python unified diff application -------------------------------------


def _strip_prefix(raw: str) -> str:
    path = raw.strip()
    if path == "/dev/null":
        return path
    # Drop the conventional a/ b/ prefix; ignore timestamps after a tab.
    path = path.split("\t", 1)[0]
    if path.startswith(("a/", "b/")):
        path = path[2:]
    return path


@dataclass(frozen=True)
class _Hunk:
    old_start: int
    old_count: int
    body: tuple[str, ...]  # lines prefixed with ' ', '+' or '-'


def _parse_hunk(lines: list[str], i: int) -> tuple[_Hunk, int]:
    m = _HUNK_RE.match(lines[i])
    if m is None:
        raise EvalError(f"malformed hunk header: {lines[i]!r}")
    old_start = int(m.group(1))
    old_count = int(m.group(2)) if m.group(2) is not None else 1
    new_count = int(m.group(4)) if m.group(4) is not None else 1
    body: list[str] = []
    old_seen = new_seen = 0
    i += 1
    while i < len(lines) and (old_seen < old_count or new_seen < new_count):
        line = lines[i]
        if line.startswith("\\"):  # "\ No newline at end of file"
            i += 1
            continue
        if line == "":
            body.append(" ")  # bare empty line = empty context line
            old_seen += 1
            new_seen += 1
        elif line[0] == " ":
            body.append(line)
            old_seen += 1
            new_seen += 1
        elif line[0] == "-":
            body.append(line)
            old_seen += 1
        elif line[0] == "+":
            body.append(line)
            new_seen += 1
        else:
            break
        i += 1
    if old_seen != old_count or new_seen != new_count:
        raise EvalError("hunk line count mismatch")
    return _Hunk(old_start=old_start, old_count=old_count, body=tuple(body)), i


def _apply_file_patch(repo: Path, old_path: str, new_path: str, hunks: list[_Hunk]) -> None:
    if old_path == "/dev/null":
        source: list[str] = []
    else:
        target = repo / old_path
        if not target.is_file():
            raise EvalError(f"patch source missing: {old_path}")
        text = target.read_text(encoding="utf-8")
        source = text.split("\n")
        if source and source[-1] == "":
            source.pop()  # trailing newline is re-added on write

    out: list[str] = []
    cursor = 0
    for hunk in hunks:
        start = hunk.old_start - 1 if hunk.old_count > 0 else hunk.old_start
        old_lines = [line[1:] for line in hunk.body if line[0] in (" ", "-")]
        new_lines = [line[1:] for line in hunk.body if line[0] in (" ", "+")]
        if start < cursor or source[start : start + len(old_lines)] != old_lines:
            raise EvalError(f"patch context mismatch near line {hunk.old_start}")
        out.extend(source[cursor:start])
        out.extend(new_lines)
        cursor = start + len(old_lines)
    out.extend(source[cursor:])

    if new_path == "/dev/null":
        target = repo / old_path
        if target.is_file():
            target.unlink()
        return
    target = repo / new_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(out) + "\n", encoding="utf-8")


def apply_patch(repo: str | Path, diff: str) -> None:
    """Apply a unified diff to ``repo``. Pure python; raises EvalError on
    any mismatch (fail-closed — a patch that does not apply never lands
    partially)."""
    root = Path(repo)
    lines = diff.split("\n")
    i = 0
    applied = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("--- "):
            old_path = _strip_prefix(line[4:])
            i += 1
            if i >= len(lines) or not lines[i].startswith("+++ "):
                raise EvalError("missing +++ header after ---")
            new_path = _strip_prefix(lines[i][4:])
            i += 1
            hunks: list[_Hunk] = []
            while i < len(lines) and lines[i].startswith("@@ "):
                hunk, i = _parse_hunk(lines, i)
                hunks.append(hunk)
            if old_path == "/dev/null" and not hunks:
                raise EvalError("new-file patch without hunks")
            _apply_file_patch(root, old_path, new_path, hunks)
            applied += 1
        elif line.startswith(
            ("diff ", "index ", "new file", "deleted file", "old mode", "new mode", "---")
        ):
            i += 1  # tolerate git-style headers
        elif line.strip() == "":
            i += 1
        else:
            raise EvalError(f"unparseable patch line: {line!r}")
    if applied == 0:
        raise EvalError("patch contains no file changes")


# --- checks -------------------------------------------------------------------


def _run_command(repo: Path, command: str, timeout: float) -> CheckResult:
    cmd = command.replace("{python}", shlex.quote(sys.executable))
    name = f"command:{command}"
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(name=name, ok=False, detail="timeout")
    if proc.returncode != 0:
        return CheckResult(name=name, ok=False, detail=f"exit {proc.returncode}")
    return CheckResult(name=name, ok=True)


def _repo_diff(repo: Path) -> tuple[str, list[str]]:
    """Full diff (incl. new/deleted files) and changed relpaths vs HEAD.

    ``--no-renames``: a rename must surface as delete+add so the source path
    can't collapse out of the allowed_files / forbidden-pattern evidence."""
    _git(repo, "add", "-A")
    diff = _git(repo, "diff", "--cached", "--no-renames", "--unified=3")
    names = _git(repo, "diff", "--cached", "--name-only", "--no-renames")
    return diff, sorted(n for n in names.split("\n") if n)


def _is_allowed(relpath: str, allowed: tuple[str, ...]) -> bool:
    for entry in allowed:
        entry = entry.rstrip("/")
        if relpath == entry or relpath.startswith(entry + "/"):
            return True
    return False


def run_checks(
    case: Case, repo: str | Path, *, timeout: float = DEFAULT_TIMEOUT
) -> tuple[CheckResult, ...]:
    """Evaluate every check for ``case`` against the patched ``repo``."""
    root = Path(repo)
    results: list[CheckResult] = []
    for command in (*case.checks.commands, *case.checks.hidden_commands):
        results.append(_run_command(root, command, timeout))

    diff, changed = _repo_diff(root)

    for pattern in case.checks.forbidden_patterns:
        hit = re.search(pattern, diff)
        detail = "pattern found in diff" if hit else ""
        results.append(CheckResult(name=f"forbidden:{pattern}", ok=hit is None, detail=detail))

    if case.checks.allowed_files:
        outside = [f for f in changed if not _is_allowed(f, case.checks.allowed_files)]
        results.append(
            CheckResult(
                name="allowed_files",
                ok=not outside,
                detail=f"changed outside whitelist: {outside}" if outside else "",
            )
        )

    if case.checks.tests_unchanged:
        original = dict(case.fixture.files)
        for rel in case.fixture.starting_tests:
            target = root / rel
            current = target.read_text(encoding="utf-8") if target.is_file() else None
            expected = original.get(rel)
            ok = current is not None and expected is not None and current == expected
            results.append(
                CheckResult(
                    name=f"tests_unchanged:{rel}",
                    ok=ok,
                    detail="" if ok else "test file modified or deleted",
                )
            )

    if case.checks.no_new_dependencies:
        results.append(_check_no_new_dependencies(case, root, diff, changed))

    return tuple(results)


_DEP_FILES = (
    "requirements.txt",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "Pipfile",
    "poetry.lock",
    "uv.lock",
    "package.json",
)
_IMPORT_RE = re.compile(r"^\+\s*(?:import|from)\s+([A-Za-z_][\w.]*)")


def _check_no_new_dependencies(
    case: Case, repo: Path, diff: str, changed: list[str]
) -> CheckResult:
    """Fail when the diff introduces a dependency absent from the baseline:
    a changed dependency-manifest file, or an added import of a module that
    nothing in the fixture already imports."""
    touched_dep_files = [f for f in changed if f in _DEP_FILES]
    baseline_imports: set[str] = set()
    import_re = re.compile(r"^\s*(?:import|from)\s+([A-Za-z_][\w.]*)", re.MULTILINE)
    for _rel, content in case.fixture.files:
        baseline_imports.update(m.group(1).split(".")[0] for m in import_re.finditer(content))
    added: set[str] = set()
    for line in diff.splitlines():
        if line.startswith("+++"):
            continue
        m = _IMPORT_RE.match(line)
        if m:
            module = m.group(1).split(".")[0]
            if module in sys.stdlib_module_names:
                continue  # stdlib is not a dependency
            if module not in baseline_imports:
                added.add(module)
    problems = [f"dependency file changed: {f}" for f in touched_dep_files]
    problems += [f"new import: {m}" for m in sorted(added)]
    return CheckResult(
        name="no_new_dependencies",
        ok=not problems,
        detail="; ".join(problems),
    )


# --- arms ---------------------------------------------------------------------


def _run_arm(
    case: Case, patch: str, *, work_dir: str | Path | None, timeout: float
) -> tuple[CheckResult, ...]:
    tmp = tempfile.mkdtemp(prefix=f"pxx-eval-{case.id}-", dir=work_dir)
    try:
        repo = materialize_case(case, Path(tmp) / "repo")
        try:
            apply_patch(repo, patch)
        except EvalError as exc:
            return (CheckResult(name="apply_patch", ok=False, detail=str(exc)),)
        return run_checks(case, repo, timeout=timeout)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def self_check(
    case: Case, *, work_dir: str | Path | None = None, timeout: float = DEFAULT_TIMEOUT
) -> SelfCheckResult:
    """Honest patch must pass every check; cheat patch must be caught."""
    honest = _run_arm(case, case.honest_patch, work_dir=work_dir, timeout=timeout)
    cheat = _run_arm(case, case.cheat_patch, work_dir=work_dir, timeout=timeout)
    honest_failures = tuple(c.name for c in honest if not c.ok)
    cheat_failures = tuple(c.name for c in cheat if not c.ok)
    return SelfCheckResult(
        case_id=case.id,
        honest_ok=not honest_failures,
        cheat_caught=bool(cheat_failures),
        honest_failures=honest_failures,
        cheat_failures=cheat_failures,
    )


def run_case(
    case: Case,
    backend_factory: Callable[[Case], str] | None = None,
    *,
    work_dir: str | Path | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> CaseResult:
    """Run one case. Without a ``backend_factory`` the scripted arm applies
    the case's honest patch (CI path). With one, the factory receives the
    case and returns the unified diff the backend produced — a thin,
    injectable edge that keeps live backends out of the harness."""
    patch = case.honest_patch if backend_factory is None else backend_factory(case)
    checks = _run_arm(case, patch, work_dir=work_dir, timeout=timeout)
    return CaseResult(
        case_id=case.id,
        passed=all(c.ok for c in checks),
        checks=checks,
    )
