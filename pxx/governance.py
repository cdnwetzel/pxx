"""Governance gate for pre-push validation (#022) — secrets, versions, verdicts."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GovernanceViolation:
    """A single governance check failure."""
    check: str  # "secrets", "version-sync", "review-pending"
    severity: str  # "error", "warning"
    detail: str


# Built-in secret patterns (stdlib only, no regex-heavy fingerprinting)
SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("api-key-literal", re.compile(r"(?i)(api[_-]?key|apikey)\s*=\s*['\"][^'\"]{8,}['\"]")),
    ("openai-key", re.compile(r"sk-[a-zA-Z0-9]{32,}")),
    ("aws-key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github-token", re.compile(r"ghp_[a-zA-Z0-9]{36}")),
    ("private-key-pem", re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----")),
    ("generic-password", re.compile(r"(?i)password\s*=\s*['\"][^'\"]{4,}['\"]")),
]


def scan_staged_secrets(repo_root: Path) -> list[GovernanceViolation]:
    """Scan staged files for secret patterns.

    Gets list of staged files via git diff --cached --name-only,
    then checks each file against SECRET_PATTERNS.
    Returns list of violations (severity="error").
    """
    violations = []

    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        staged_files = result.stdout.strip().splitlines()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return violations

    for filepath in staged_files:
        full_path = repo_root / filepath
        if not full_path.exists() or full_path.is_dir():
            continue

        try:
            content = full_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        for pattern_name, pattern in SECRET_PATTERNS:
            for line_num, line in enumerate(content.splitlines(), 1):
                if pattern.search(line):
                    violations.append(GovernanceViolation(
                        check="secrets",
                        severity="error",
                        detail=f"{filepath}:{line_num} matches {pattern_name}",
                    ))
                    break  # Report once per file per pattern

    return violations


def check_version_sync(repo_root: Path, config: dict) -> list[GovernanceViolation]:
    """Check version consistency across files per config.

    Config is a dict with optional "version_files" list:
    [{"path": "VERSION", "parser": "plaintext"}, ...]

    Parsers: plaintext (read+strip), changelog-header (regex),
    json:key (json.loads), py-assign:VAR (regex)
    """
    violations = []
    version_files = config.get("version_files", [])
    if not version_files:
        return violations

    versions = {}

    for file_spec in version_files:
        filepath = file_spec.get("path")
        parser = file_spec.get("parser", "plaintext")
        if not filepath:
            continue

        full_path = repo_root / filepath
        if not full_path.exists():
            violations.append(GovernanceViolation(
                check="version-sync",
                severity="warning",
                detail=f"{filepath} not found",
            ))
            continue

        try:
            content = full_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            violations.append(GovernanceViolation(
                check="version-sync",
                severity="error",
                detail=f"{filepath} read error",
            ))
            continue

        version = None

        if parser == "plaintext":
            version = content.strip()

        elif parser == "changelog-header":
            # Regex: ## [X.Y.Z]
            m = re.search(r"##\s+\[([^\]]+)\]", content)
            version = m.group(1) if m else None

        elif parser.startswith("json:"):
            key = parser.split(":", 1)[1]
            try:
                data = json.loads(content)
                version = data.get(key)
            except json.JSONDecodeError:
                violations.append(GovernanceViolation(
                    check="version-sync",
                    severity="error",
                    detail=f"{filepath} is invalid JSON",
                ))
                continue

        elif parser.startswith("py-assign:"):
            var_name = parser.split(":", 1)[1]
            pattern = rf'{var_name}\s*=\s*["\']([^"\']+)["\']'
            m = re.search(pattern, content)
            version = m.group(1) if m else None

        if version is None:
            violations.append(GovernanceViolation(
                check="version-sync",
                severity="warning",
                detail=f"{filepath} (parser={parser}) returned no version",
            ))
            continue

        if version not in versions:
            versions[version] = [filepath]
        else:
            versions[version].append(filepath)

    # Check for version mismatch
    if len(versions) > 1:
        detail_lines = [f"{v}: {', '.join(files)}" for v, files in versions.items()]
        violations.append(GovernanceViolation(
            check="version-sync",
            severity="error",
            detail=f"Version mismatch: {'; '.join(detail_lines)}",
        ))

    return violations


def check_review_verdict(repo_root: Path) -> list[GovernanceViolation]:
    """Check if there are unresolved review verdicts in workflow state.

    Reads .pxx/workflow_state.json and returns violation if:
    - phase == "review_pending" (review hasn't been run)
    - phase == "rejected" (review failed)
    """
    from pxx import workflow

    violations = []
    state = workflow.load_state(repo_root)
    if state is None:
        return violations

    if state.phase == "review_pending":
        violations.append(GovernanceViolation(
            check="review-pending",
            severity="warning",
            detail=f"Review pending: {state.review_verdict or 'no verdict yet'}. Run pxx --review",
        ))

    elif state.phase == "rejected":
        violations.append(GovernanceViolation(
            check="review-pending",
            severity="error",
            detail="Review rejected. Run pxx --review --heal or pxx --edit to fix",
        ))

    return violations


def run_governance_check(repo_root: Path) -> int:
    """Run all governance checks and report violations.

    Returns 0 if no errors, 1 if any error-severity violations found.
    Warnings are reported but don't fail the check.
    """
    if os.environ.get("PXX_GOVERNANCE_SKIP") == "1":
        return 0

    violations = []

    # Secrets scan (always runs)
    violations.extend(scan_staged_secrets(repo_root))

    # Version sync (if .pxx/governance.json exists)
    gov_config_path = repo_root / ".pxx" / "governance.json"
    if gov_config_path.exists():
        try:
            gov_config = json.loads(gov_config_path.read_text(encoding="utf-8"))
            violations.extend(check_version_sync(repo_root, gov_config))
        except (OSError, json.JSONDecodeError):
            pass  # Silently skip if config is malformed

    # Review verdict (if workflow state exists)
    violations.extend(check_review_verdict(repo_root))

    # Report violations
    errors = [v for v in violations if v.severity == "error"]
    warnings = [v for v in violations if v.severity == "warning"]

    for v in violations:
        prefix = "ERROR" if v.severity == "error" else "WARN"
        print(f"pxx {prefix}: {v.check}: {v.detail}", file=sys.stderr)

    if errors:
        print(f"\npxx: {len(errors)} error(s), {len(warnings)} warning(s)", file=sys.stderr)
        return 1

    if warnings:
        print(f"\npxx: {len(warnings)} warning(s) only", file=sys.stderr)

    return 0
