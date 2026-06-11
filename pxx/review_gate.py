"""Code review framework integration for #021 — workflow verdict computation."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Finding:
    """A single code review finding (F-NNN)."""

    id: str
    severity: str  # P0, P1, P2
    state: str  # proposed, open, in-progress, resolved, wontfix, superseded
    location: str
    description: str


def framework_path() -> Path:
    """Get path to code_review framework (default ~/ai/code_review)."""
    return Path(os.environ.get("PXX_CODE_REVIEW_PATH", "~/ai/code_review")).expanduser()


def _get_claude_bin() -> str | None:
    """Get path to claude binary."""
    override = os.environ.get("PXX_CLAUDE_BIN")
    if override:
        return override
    # Try shutil.which but handle import locally
    import shutil

    return shutil.which("claude")


def run_review_pass(project_root: Path) -> int:
    """Invoke code_review framework and return exit code.

    Runs: claude --print "run a review pass on this project"
    from project_root. Returns 0 on success, 1 on failure.
    """
    claude_bin = _get_claude_bin()
    if not claude_bin:
        print(
            "pxx: claude binary not found. Install: uv tool install claude",
            file=sys.stderr,
        )
        return 1

    try:
        result = subprocess.run(
            [claude_bin, "--print", "run a review pass on this project"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=300,
        )
        return 0 if result.returncode == 0 else 1
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        print(f"pxx: review pass failed: {e}", file=sys.stderr)
        return 1


def parse_findings(md_content: str) -> list[Finding]:
    """Parse findings from markdown content (claude-*.md format).

    Looks for headers like:
    ### F-NNN — description (P0/P1/P2, state: open/proposed/resolved/etc)

    The severity group is deliberately permissive (any word, normalized to
    upper-case): a finding with a malformed severity must become a *visible*
    finding that compute_verdict can fail closed on — silently dropping the
    line would let it vanish into an APPROVE-on-silence.
    """
    findings = []
    # Match: ### F-NNN — description (severity, state: value)
    pattern = r"^### (F-\d+) — (.+?)\s+\((\w+),\s*(?:state:\s*)?([a-z\-]+)\)"

    for line in md_content.splitlines():
        m = re.match(pattern, line)
        if not m:
            continue
        finding_id, description, severity, state = m.groups()
        severity = severity.upper()
        # Extract location from description if present (e.g., "title in file.py:L42")
        location = ""
        if " in " in description:
            _, location = description.rsplit(" in ", 1)

        findings.append(
            Finding(
                id=finding_id,
                severity=severity,
                state=state,
                location=location,
                description=description,
            )
        )
    return findings


def collect_active_findings(project_root: Path) -> list[Finding]:
    """Read review/claude/ directory and return active findings (not resolved/wontfix)."""
    review_dir = project_root / "review" / "claude"
    if not review_dir.exists():
        return []

    all_findings = []
    for md_file in sorted(review_dir.glob("claude-*.md")):
        try:
            content = md_file.read_text(encoding="utf-8")
            all_findings.extend(parse_findings(content))
        except (OSError, UnicodeDecodeError):
            pass

    # Filter to active states (exclude resolved, wontfix, superseded)
    active_states = {"proposed", "open", "in-progress"}
    return [f for f in all_findings if f.state in active_states]


def has_review_evidence(project_root: Path) -> bool:
    """True iff a review actually left artifacts to judge (any claude-*.md).

    "No findings" is only meaningful when a review demonstrably ran. Without
    evidence, the verdict must be NO_REVIEW, not APPROVE — reviewer silence is
    absence of information, not approval.
    """
    review_dir = project_root / "review" / "claude"
    return review_dir.exists() and any(review_dir.glob("claude-*.md"))


def compute_verdict(findings: list[Finding]) -> str:
    """Compute verdict: APPROVE, REVISE, or REJECT. Fails closed.

    - P0 active findings → REJECT
    - any finding with an unknown severity (not P0/P1/P2) → REVISE — a parse
      glitch or unrecognized label must never silently approve
    - P1 active findings (no P0) → REVISE
    - Only P2 or empty → APPROVE

    Severity comparison is case-normalized so "p0" cannot slip past REJECT.
    """
    severities = {f.severity.upper() for f in findings}

    if "P0" in severities:
        return "REJECT"
    if "P1" in severities or any(s not in {"P0", "P1", "P2"} for s in severities):
        return "REVISE"
    return "APPROVE"


def build_healing_prompt(findings: list[Finding]) -> str:
    """Build aider --message prompt for --heal mode.

    Includes P1 findings and any unknown-severity findings (the latter cause a
    fail-closed REVISE, so they must surface here — otherwise a REVISE verdict
    could come with an empty healing prompt and the loop would spin on nothing).
    P0 is the REJECT path; P2 never blocks.
    """
    heal_findings = [f for f in findings if f.severity.upper() not in {"P0", "P2"}]
    if not heal_findings:
        return ""

    lines = [
        "Address the following code review findings:",
        "",
    ]
    for f in heal_findings:
        lines.append(f"- {f.id}: {f.description}")
        if f.location:
            lines.append(f"  Location: {f.location}")

    return "\n".join(lines)
