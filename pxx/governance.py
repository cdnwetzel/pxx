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
    (
        "api-key-literal",
        re.compile(r"(?i)(api[_-]?key|apikey)\s*=\s*['\"][^'\"]{8,}['\"]"),
    ),
    ("openai-key", re.compile(r"sk-[a-zA-Z0-9]{32,}")),
    ("anthropic-key", re.compile(r"sk-ant-[a-zA-Z0-9]{32,}")),
    ("huggingface-token", re.compile(r"hf_[a-zA-Z0-9]{20,}")),
    ("aws-key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github-token", re.compile(r"ghp_[a-zA-Z0-9]{36}")),
    # Bearer token: intentionally broad to catch OAuth 2.0 tokens. May flag
    # legitimate test tokens or fixtures in non-secret contexts; false positives
    # are noise, not security bypass. User can suppress via gitignore if needed.
    ("bearer-token", re.compile(r"(?i)bearer\s+[a-zA-Z0-9\.\-_]{20,}")),
    ("private-key-pem", re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----")),
    ("generic-password", re.compile(r"(?i)password\s*=\s*['\"][^'\"]{4,}['\"]")),
]


def scan_staged_secrets(repo_root: Path) -> list[GovernanceViolation]:
    """Scan staged files (index) for secret patterns.

    Gets list of files in the index (staging area) via git diff --cached --name-only.
    This is a pre-commit check that catches secrets before they are committed.
    Checks each file against SECRET_PATTERNS and returns violations (severity="error").

    Note: Pre-push scanning of committed objects is a future enhancement.
    """
    violations = []

    try:
        # Scan staged files (git index)
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        staged_files = result.stdout.strip().splitlines()
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        # "Couldn't run the scanner" is NOT "no secrets" — a release/commit
        # gate that can't scan must fail closed, not wave the commit through.
        return [
            GovernanceViolation(
                check="secrets",
                severity="error",
                detail=f"secret scan could not run (git unavailable/timeout): {e}",
            )
        ]

    for filepath in staged_files:
        if not filepath:
            continue

        # Scan the STAGED (index) content, not the worktree. A secret can be
        # staged and then removed from the worktree — the commit still carries
        # it — so reading the worktree file would miss it. `git show :<path>`
        # reads the index blob being committed.
        try:
            show = subprocess.run(
                ["git", "show", f":{filepath}"],
                cwd=repo_root,
                capture_output=True,
                text=True,
                # A staged binary (image, pickle, ...) is not valid UTF-8;
                # strict decoding would raise UnicodeDecodeError and take down
                # the whole governance gate. Replace and scan what's scannable.
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=5,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        if show.returncode != 0:
            continue
        content = show.stdout

        for pattern_name, pattern in SECRET_PATTERNS:
            for line_num, line in enumerate(content.splitlines(), 1):
                if pattern.search(line):
                    violations.append(
                        GovernanceViolation(
                            check="secrets",
                            severity="error",
                            detail=f"{filepath}:{line_num} matches {pattern_name}",
                        )
                    )
                    break  # Report once per file per pattern

    return violations


# Public-content scanner (roadmap Phase 0.1): infrastructure identifiers that
# must never enter the PUBLIC repository, per the a256a04 de-identification
# contract. Only generic CLASSES are defined here — machine-specific literals
# (real hostnames, usernames, domains) load from untracked denylist files,
# because committing that list would itself be the leak.
#
# A line containing the allow pragma is exempt (for test fixtures and docs
# that discuss the patterns themselves).
CONTENT_ALLOW_PRAGMA = "pxx-content: allow"

PUBLIC_CONTENT_PATTERNS: list[tuple[str, re.Pattern]] = [
    (
        "private-ipv4",
        re.compile(
            r"\b(?:10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])|192\.168)"
            r"\.\d{1,3}\.\d{1,3}\b"
        ),
    ),
    # `.home` is deliberately absent (Path.home() would false-positive on
    # every use); the trailing lookahead skips method calls like x.local().
    (
        "internal-hostname-suffix",
        re.compile(r"\b[a-zA-Z0-9][a-zA-Z0-9-]*\.(?:local|lan|internal)\b(?!\()"),
    ),
    (
        "home-directory-path",
        re.compile(r"(?:/Users|/home)/([a-zA-Z][a-zA-Z0-9_-]*)"),
    ),
    (
        "unprotected-service-statement",
        re.compile(
            r"(?i)\b(?:no|without|lacks)\s+(?:request-level\s+|any\s+)?"
            r"auth(?:entication)?\b"
        ),
    ),
]

# Placeholder usernames that make a home path documentation, not a leak.
_HOME_PATH_PLACEHOLDERS = {
    "you",
    "user",
    "username",
    "your-username",
    "yourname",
    "example",
    "USER",
    "foo",
    "dev",
}

# Generated dependency metadata — four-part version strings false-positive
# the IP pattern, and lockfiles never carry infra topology.
_CONTENT_SKIP_FILENAMES = {"uv.lock", "package-lock.json", "poetry.lock", "Cargo.lock"}


def _content_denylist_paths(repo_root: Path) -> list[Path]:
    config_base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return [
        repo_root / "private" / "content-denylist.txt",
        Path(config_base) / "pxx" / "content-denylist",
    ]


def load_content_denylist(repo_root: Path) -> list[re.Pattern]:
    """Machine-local identifier literals (one per line, # comments) compiled
    case-insensitively. Both locations are untracked by design."""
    patterns: list[re.Pattern] = []
    for path in _content_denylist_paths(repo_root):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            term = line.strip()
            if not term or term.startswith("#"):
                continue
            patterns.append(re.compile(re.escape(term), re.IGNORECASE))
    return patterns


def _scan_content_lines(
    filepath: str, content: str, denylist: list[re.Pattern]
) -> list[GovernanceViolation]:
    violations = []
    seen: set[str] = set()
    for line_num, line in enumerate(content.splitlines(), 1):
        if CONTENT_ALLOW_PRAGMA in line:
            continue
        for pattern_name, pattern in PUBLIC_CONTENT_PATTERNS:
            if pattern_name in seen:
                continue
            m = pattern.search(line)
            if not m:
                continue
            if (
                pattern_name == "home-directory-path"
                and m.group(1) in _HOME_PATH_PLACEHOLDERS
            ):
                continue
            seen.add(pattern_name)
            violations.append(
                GovernanceViolation(
                    check="public-content",
                    severity="error",
                    detail=f"{filepath}:{line_num} matches {pattern_name}",
                )
            )
        for i, pattern in enumerate(denylist):
            key = f"denylist-{i}"
            if key in seen:
                continue
            if pattern.search(line):
                seen.add(key)
                # Deliberately do NOT echo the matched term — the report
                # itself must stay safe to paste into public artifacts.
                violations.append(
                    GovernanceViolation(
                        check="public-content",
                        severity="error",
                        detail=f"{filepath}:{line_num} matches private denylist entry #{i + 1}",
                    )
                )
    return violations


# Top-level paths that ship in the PyPI sdist/wheel (verified against the
# built 1.1.0 artifact). The release gate scans ONLY these — dev-only trees
# (review/, docs/, plans/, config/) never reach PyPI, so scanning them at
# release time would block publishes on other agents' review notes for no
# distribution risk. The pre-commit staged scan still guards the whole repo.
_SHIPPED_PREFIXES: tuple[str, ...] = ("pxx/", "tests/", "README.md", "pyproject.toml")
# NOTE (1.3.3): the --shipped scope above covers only the wheel/sdist. The whole
# PUBLIC tree (docs/, plans/, deploy/, config/, CLAUDE.md) is de-identified too;
# that is enforced by `pxx --check --all-files` (armed) in release.yml + ci.yml.
# The absence of that full-tree gate is why a fleet hostname survived to PyPI in
# 1.3.0/1.3.1 despite a green --shipped run.


def scan_public_content(
    repo_root: Path,
    full_tree: bool = False,
    shipped_only: bool = False,
    allow_empty_denylist: bool = False,
) -> list[GovernanceViolation]:
    """Scan for infrastructure identifiers that must not reach the public repo.

    Default mode scans STAGED content (`git show :<path>`, same mechanism as
    the secrets scanner) — the gate for new content. ``full_tree`` scans every
    tracked file's worktree content — the audit mode (`pxx --check
    --all-files`). ``shipped_only`` restricts the full-tree scan to files that
    actually ship to PyPI — the release gate (`pxx --check --shipped`).

    Bare fleet hostnames (a build host's short name) have no structural
    shape, so they are only caught by the untracked denylist. When an
    audit/release scan runs with ZERO denylist patterns loaded, hostname
    coverage is silently OFF — a green result would be false assurance
    ("no hostnames shipped") for a check that could not run. The scan makes
    that non-silent: it emits a coverage-disabled violation (error, so the gate
    fails) unless ``allow_empty_denylist`` downgrades it to a visible warning
    (a deliberate opt-out for a bare CI that scans structural patterns only).
    """
    violations: list[GovernanceViolation] = []
    denylist = load_content_denylist(repo_root)

    if (shipped_only or full_tree) and not denylist:
        violations.append(
            GovernanceViolation(
                check="public-content",
                severity="warning" if allow_empty_denylist else "error",
                detail=(
                    "hostname coverage DISABLED — 0 denylist patterns loaded "
                    "(private/content-denylist.txt or "
                    "~/.config/pxx/content-denylist); structural patterns "
                    "(IP/domain/home-path) still ran. "
                    + (
                        "allowed via --allow-empty-denylist."
                        if allow_empty_denylist
                        else "arm the denylist or pass --allow-empty-denylist "
                        "to scan structural-only deliberately."
                    )
                ),
            )
        )

    list_cmd = (
        ["git", "ls-files"]
        if (full_tree or shipped_only)
        else ["git", "diff", "--cached", "--name-only"]
    )
    try:
        result = subprocess.run(
            list_cmd,
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        # Fail closed: a content scan that couldn't list files must block the
        # release, not report a clean tree (the gate's whole purpose).
        return [
            GovernanceViolation(
                check="public-content",
                severity="error",
                detail=f"content scan could not run (git unavailable/timeout): {e}",
            )
        ]

    for filepath in result.stdout.strip().splitlines():
        if not filepath:
            continue
        if Path(filepath).name in _CONTENT_SKIP_FILENAMES:
            continue
        if shipped_only and not filepath.startswith(_SHIPPED_PREFIXES):
            continue
        if full_tree or shipped_only:
            full = repo_root / filepath
            try:
                content = full.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
        else:
            try:
                show = subprocess.run(
                    ["git", "show", f":{filepath}"],
                    cwd=repo_root,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                    timeout=5,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue
            if show.returncode != 0:
                continue
            content = show.stdout
        violations.extend(_scan_content_lines(filepath, content, denylist))

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
            violations.append(
                GovernanceViolation(
                    check="version-sync",
                    severity="warning",
                    detail=f"{filepath} not found",
                )
            )
            continue

        try:
            content = full_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            violations.append(
                GovernanceViolation(
                    check="version-sync",
                    severity="error",
                    detail=f"{filepath} read error",
                )
            )
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
                violations.append(
                    GovernanceViolation(
                        check="version-sync",
                        severity="error",
                        detail=f"{filepath} is invalid JSON",
                    )
                )
                continue

        elif parser.startswith("py-assign:"):
            var_name = parser.split(":", 1)[1]
            pattern = rf'{var_name}\s*=\s*["\']([^"\']+)["\']'
            m = re.search(pattern, content)
            version = m.group(1) if m else None

        if version is None:
            violations.append(
                GovernanceViolation(
                    check="version-sync",
                    severity="warning",
                    detail=f"{filepath} (parser={parser}) returned no version",
                )
            )
            continue

        if version not in versions:
            versions[version] = [filepath]
        else:
            versions[version].append(filepath)

    # Check for version mismatch
    if len(versions) > 1:
        detail_lines = [f"{v}: {', '.join(files)}" for v, files in versions.items()]
        violations.append(
            GovernanceViolation(
                check="version-sync",
                severity="error",
                detail=f"Version mismatch: {'; '.join(detail_lines)}",
            )
        )

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
        violations.append(
            GovernanceViolation(
                check="review-pending",
                severity="warning",
                detail=(
                    f"Review pending: {state.review_verdict or 'no verdict yet'}. "
                    f"Run pxx --review"
                ),
            )
        )

    elif state.phase == "rejected":
        violations.append(
            GovernanceViolation(
                check="review-pending",
                severity="error",
                detail="Review rejected. Run pxx --review --heal or pxx --edit to fix",
            )
        )

    return violations


def run_governance_check(
    repo_root: Path,
    full_content: bool = False,
    shipped_content: bool = False,
    allow_empty_denylist: bool = False,
) -> int:
    """Run all governance checks and report violations.

    Returns 0 if no errors, 1 if any error-severity violations found.
    Warnings are reported but don't fail the check.

    ``full_content`` switches the public-content scan from staged-only (the
    gate for new content) to every tracked file (the audit mode).

    PXX_GOVERNANCE_SKIP env var bypasses checks (tests only; set by pytest).
    """
    if os.environ.get("PXX_GOVERNANCE_SKIP") == "1":
        if not os.environ.get("PYTEST_CURRENT_TEST"):
            raise RuntimeError(
                "PXX_GOVERNANCE_SKIP is reserved for test environments only. "
                "It is set automatically by pytest via the "
                "PYTEST_CURRENT_TEST env var. "
                "Explicit use outside tests is not permitted."
            )
        return 0

    violations = []

    # Secrets scan (always runs)
    violations.extend(scan_staged_secrets(repo_root))

    # Public-content scan (always runs; the repo is public)
    violations.extend(
        scan_public_content(
            repo_root,
            full_tree=full_content,
            shipped_only=shipped_content,
            allow_empty_denylist=allow_empty_denylist,
        )
    )

    # Version sync (if .pxx/governance.json exists)
    gov_config_path = repo_root / ".pxx" / "governance.json"
    if gov_config_path.exists():
        try:
            gov_config = json.loads(gov_config_path.read_text(encoding="utf-8"))
            violations.extend(check_version_sync(repo_root, gov_config))
        except OSError as e:
            print(
                f"pxx WARNING: governance config read error ({gov_config_path})\n"
                f"  Check: file permissions, path exists. Details: {e}",
                file=sys.stderr,
            )
        except json.JSONDecodeError as e:
            print(
                f"pxx WARNING: governance config is invalid JSON ({gov_config_path})\n"
                f"  Run: jq . {gov_config_path} to find syntax errors. Details: {e}",
                file=sys.stderr,
            )

    # Review verdict (if workflow state exists)
    violations.extend(check_review_verdict(repo_root))

    # Report violations
    errors = [v for v in violations if v.severity == "error"]
    warnings = [v for v in violations if v.severity == "warning"]

    for v in violations:
        prefix = "ERROR" if v.severity == "error" else "WARN"
        print(f"pxx {prefix}: {v.check}: {v.detail}", file=sys.stderr)

    if errors:
        print(
            f"\npxx: {len(errors)} error(s), {len(warnings)} warning(s)",
            file=sys.stderr,
        )
        return 1

    if warnings:
        print(f"\npxx: {len(warnings)} warning(s) only", file=sys.stderr)

    return 0
