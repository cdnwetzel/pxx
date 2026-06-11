"""Dogfooding and self-improvement modes for pxx (#001).

These modes enable pxx to maintain and improve its own source code. By
providing specialized wrappers for testing, linting, and autonomous
fixing, pxx can iterate on itself with lower friction while adhering
to project conventions and safety constraints (like the diff cap).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Tier 3 (#012): tighter diff cap for autonomous sessions (default 60 vs
# the normal 100). Surfaced as a constant for testability.
SELF_FIX_DIFF_CAP = 60


def determine_session_class(
    edit_mode: bool,
    dry_run: bool,
    self_improve_mode: bool,
    self_fix_mode: bool,
) -> str:
    """Map boolean flags to a single session_class enum (#004)."""
    if self_fix_mode:
        return "self-fix"
    if self_improve_mode:
        return "self-improve"
    if dry_run and edit_mode:
        return "dry-run"
    if edit_mode:
        return "edit"
    return "ask"


def self_test(repo_root: Path) -> int:
    """Run `uv run pytest -q` against the pxx repo (#001 T1)."""
    cmd = ["uv", "run", "pytest", "-q"]
    print(
        f"pxx: self-test — running `{' '.join(cmd)}` in {repo_root}",
        file=sys.stderr,
    )
    # Bound the run so a hung test can't block the self-test indefinitely.
    rc = subprocess.run(cmd, cwd=repo_root, check=False, timeout=120).returncode
    status = "passed" if rc == 0 else "failed"
    print(f"pxx: self-test — {status} ({rc})", file=sys.stderr)
    return rc


def self_lint(repo_root: Path) -> int:
    """Run ruff check and ruff format --check against the pxx repo (#001 T1).

    Scoped to pxx/ and tests/ — services/* are separate packages with their
    own tooling, and linting the whole tree made the loop's lint gate
    structurally red on pre-existing service debt (live dogfood 2026-06-10).
    """
    check_cmd = ["uv", "run", "ruff", "check", "pxx/", "tests/"]
    format_cmd = ["uv", "run", "ruff", "format", "--check", "pxx/", "tests/"]

    print(
        f"pxx: self-lint — running `{' '.join(check_cmd)}` in {repo_root}",
        file=sys.stderr,
    )
    check_rc = subprocess.run(check_cmd, cwd=repo_root, check=False).returncode
    print(
        f"pxx: self-lint — running `{' '.join(format_cmd)}` in {repo_root}",
        file=sys.stderr,
    )
    format_rc = subprocess.run(format_cmd, cwd=repo_root, check=False).returncode

    combined = check_rc | format_rc
    print(
        f"pxx: self-lint — check={check_rc} format={format_rc} combined={combined}",
        file=sys.stderr,
    )
    return combined


def extract_self_fix_task(argv: list[str]) -> tuple[str | None, list[str]]:
    """Extract the positional task string immediately after ``--self-fix`` (#012)."""
    try:
        idx = argv.index("--self-fix")
    except ValueError:
        return None, argv
    if idx + 1 < len(argv) and not argv[idx + 1].startswith("-"):
        task = argv[idx + 1]
        return task, argv[: idx + 1] + argv[idx + 2 :]
    return None, argv
