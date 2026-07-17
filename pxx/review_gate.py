"""Code review framework integration for #021 — workflow verdict computation."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
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


# The reviewer's output contract: where to write and the exact header format
# the parser accepts. Without this in the prompt, the reviewer has no way to
# know either — run #1's web-side verification predicted exactly that failure.
REVIEW_PROMPT = """Run a code review pass on this project.

Write your findings to review/claude/claude-findings.md (create the directory
if needed). Every finding MUST be a markdown header line in exactly this
format, one per finding:

### F-NNN — <short description> in <file>:<line> (P0, state: open)

where the severity is P0 (must fix), P1 (should fix), or P2 (minor), and the
state is `open`. If the code is clean, still write the file containing the
line: `# Review pass: no findings.`"""


def _review_backend() -> str:
    """Which reviewer produces the verdict.

    ``local`` (default) reviews the diff on your own hardware — sovereign, the
    right posture for unsupervised loops. ``claude`` uses the frontier agent,
    appropriate for human-approved (supervised) sessions where external calls
    are an accepted trade for sharper judgment. Set via PXX_REVIEW_BACKEND.
    """
    return os.environ.get("PXX_REVIEW_BACKEND", "local").strip().lower()


def review_mode() -> str:
    """Whether the reviewer's verdict GATES the loop or only advises it.

    ``blocking`` (default): REVISE heals, REJECT/NO_REVIEW stop the loop —
    the reviewer is a gate. ``advisory``: findings are still produced,
    recorded, and surfaced, but never block a run whose DETERMINISTIC gates
    (tests, lint, scope, regression) are green. Chosen when calibration shows
    no local reviewer both catches defects and stays quiet on correct code
    (2026-07-17): a blocking false-positive reviewer spins the healing loop,
    so advisory keeps the signal without letting a confidently-wrong model
    block correct work. Set via PXX_REVIEW_MODE.
    """
    return os.environ.get("PXX_REVIEW_MODE", "blocking").strip().lower()


# v2 (2026-07-17, calibration-driven): the v1 prompt produced fp_rate=1.00 on
# Qwen3-Coder — every clean diff flagged. The three measured FP classes were
# (a) intentional requested changes read as breaking regressions, (b) code
# absent from the diff reported as "missing implementation", (c) residual
# pre-fix problems hallucinated onto correct fixes. Hence: task context block,
# out-of-scope rule, concrete-failing-input bar.
LOCAL_REVIEW_INSTRUCTIONS = """You are a code reviewer with a high bar for \
reporting. MOST diffs you review are correct — finding nothing is the normal \
outcome, not a failure. Judge the code AS IT EXISTS AFTER the diff below. \
Lines starting with "-" were REMOVED — ignore them completely, they no longer \
exist.

Report a finding ONLY if you can name a concrete input, state, or call \
sequence that produces wrong behavior in the current code. Style, naming, \
comments, missing tests, hypothetical hardening, and "could be more robust" \
are NOT findings. Code that does not appear in the diff or its context lines \
is OUT OF SCOPE — never report missing implementations or callers you cannot \
see.

Write every finding as a markdown header line in EXACTLY this format, one per line:

### F-NNN — <short description> in <file>:<line> (P1, state: open)

Severity is P0 (must fix), P1 (should fix), or P2 (minor); state is `open`.
Number findings F-001, F-002, and so on. If the current code is correct, output
EXACTLY this single line and nothing else:

# Review pass: no findings.
"""

# The exact clean-bill line — the ONLY non-finding output that counts as a
# reviewed pass. Anything else (prose, apologies, refusals) is non-compliant
# and fails closed. Mirrors calibration._NO_FINDINGS_LINE.
NO_FINDINGS_LINE = "# Review pass: no findings."

_TASK_CONTEXT_TEMPLATE = """
The diff was written to satisfy this request; the requested change itself is
INTENTIONAL and must not be reported as a defect or breaking change:

REQUEST: {task}
"""


def build_review_prompt(diff: str, task: str | None = None) -> str:
    """The exact prompt the local reviewer sees — single source for both the
    production review pass and the calibration suite, so they cannot drift."""
    prompt = LOCAL_REVIEW_INSTRUCTIONS
    if task:
        prompt += _TASK_CONTEXT_TEMPLATE.format(task=task.strip())
    return prompt + "\nDiff under review:\n" + diff


def _git_diff(project_root: Path, diff_base: str | None) -> str:
    """The unified diff the local reviewer judges.

    With ``diff_base`` (the loop passes its start SHA) the range is
    ``diff_base..HEAD`` — exactly what the session changed. Standalone it falls
    back to the last commit.
    """
    rev = f"{diff_base}..HEAD" if diff_base else "HEAD~1..HEAD"
    r = subprocess.run(
        ["git", "diff", rev],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    return r.stdout


def _post_chat(url: str, model: str, prompt: str, timeout: float) -> str:
    """POST one message to an OpenAI-compatible /v1/chat/completions endpoint and
    return the assistant text. One code path serves both vLLM and Ollama (both
    expose the OpenAI shape). Transport/parse failures raise; the caller maps
    them to a failed pass (fail closed)."""
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "stream": False,
        }
    ).encode()
    req = urllib.request.Request(
        url.rstrip("/") + "/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer EMPTY"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read())
    return payload["choices"][0]["message"]["content"]


def _review_url() -> str:
    return os.environ.get("PXX_REVIEW_URL", "http://127.0.0.1:11434")


def _review_model() -> str:
    return os.environ.get("PXX_REVIEW_MODEL", "qwen2.5:7b-instruct")


def _get_models(url: str, timeout: float) -> dict:
    """GET the backend's /v1/models listing (OpenAI shape). Raises on transport
    or parse failure; the caller maps that to a preflight verdict."""
    with urllib.request.urlopen(
        url.rstrip("/") + "/v1/models", timeout=timeout
    ) as resp:
        return json.loads(resp.read())


def preflight_review_backend(timeout: float = 5.0) -> str | None:
    """Cheap usability check of the configured review backend, for callers that
    want to fail before spending an edit round (the loop). Returns an error
    message, or None when the backend looks usable.

    The local backend must answer /v1/models, and when the response carries a
    model list the configured id must be in it — a reachable server missing the
    model (live dogfood #2, run A) otherwise surfaces only as a 404 after the
    edit and test legs have already been paid for.
    """
    backend = _review_backend()
    if backend == "claude":
        if _get_claude_bin() is None:
            return "claude binary not found (PXX_REVIEW_BACKEND=claude)"
        return None
    if backend != "local":
        return f"unknown PXX_REVIEW_BACKEND={backend!r} (use 'local' or 'claude')"
    url = _review_url()
    model = _review_model()
    try:
        payload = _get_models(url, timeout)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
        return f"review endpoint unreachable ({url}): {e}"
    # Ollama reports zero installed models as "data": null (not []) — both mean
    # an authoritative empty listing. Only an absent "data" key is treated as an
    # unknown shape and waved through.
    data = payload.get("data") or []
    if "data" in payload and isinstance(data, list):
        ids = [m.get("id") for m in data if isinstance(m, dict)]
        if model not in ids:
            found = ", ".join(str(i) for i in ids) or "none"
            return f"model {model!r} not served at {url} (found: {found})"
    return None


def _run_local_review(
    project_root: Path,
    timeout: float,
    diff_base: str | None,
    task: str | None = None,
) -> int:
    """Sovereign review: feed the round's diff to a local OpenAI-compatible model
    (PXX_REVIEW_URL + PXX_REVIEW_MODEL) and write its findings to
    review/claude/claude-findings.md in the F-NNN format the parser expects.

    Deterministic and bounded — the reviewer sees exactly the diff, never roams
    the tree, and needs no file-write permission (pxx writes the file). That is
    precisely why the local path avoids the headless-permission failure the
    claude agent hits.
    """
    url = _review_url()
    model = _review_model()
    out_dir = project_root / "review" / "claude"
    out_file = out_dir / "claude-findings.md"
    diff = _git_diff(project_root, diff_base)

    if not diff.strip():
        # An empty session diff means no change landed — "nothing changed"
        # must never read as "reviewed and clean" (a no-op round on a green
        # baseline would otherwise launder into a terminal APPROVE; observed
        # in live eval attempt 1, 2026-07-17).
        print(
            "pxx: local review found an empty session diff — nothing to "
            "review; failing closed",
            file=sys.stderr,
        )
        return 1

    try:
        content = _post_chat(url, model, build_review_prompt(diff, task), timeout)
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError, OSError) as e:
        print(f"pxx: local review failed ({url}): {e}", file=sys.stderr)
        return 1

    stripped = content.strip()
    if not stripped:
        # An empty response is absent evidence, not a clean bill — writing the
        # "no findings" line here would turn reviewer failure into APPROVE.
        print(
            f"pxx: local review returned empty output ({url}) — failing closed",
            file=sys.stderr,
        )
        return 1

    # Output-contract compliance: a clean bill is ONLY the exact no-findings
    # line or ≥1 parseable F-NNN finding. Prose like "The code looks correct."
    # parses to zero findings and would otherwise read as APPROVE — a
    # fail-open the blocking-mode 7B default (recall ~0) hits routinely. A
    # reviewer that didn't follow the contract has produced no usable verdict;
    # fail closed. (Same check as calibration.judge_response, now in prod.)
    if stripped != NO_FINDINGS_LINE and not parse_findings(content):
        print(
            f"pxx: local review output is non-compliant (neither the "
            f"no-findings line nor an F-NNN finding) — failing closed:\n"
            f"    {stripped[:120]!r}",
            file=sys.stderr,
        )
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    out_file.write_text(stripped + "\n", encoding="utf-8")
    return 0


def _run_claude_review(project_root: Path, timeout: float) -> int:
    """Frontier review via the claude CLI agent.

    ``--permission-mode acceptEdits`` is required: the default headless posture
    denies the Write tool, so the agent reviews but writes no artifact →
    NO_REVIEW. This bit a live loop run before it was added.
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
            [claude_bin, "--print", "--permission-mode", "acceptEdits", REVIEW_PROMPT],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return 0 if result.returncode == 0 else 1
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        print(f"pxx: review pass failed: {e}", file=sys.stderr)
        return 1


def run_review_pass(
    project_root: Path,
    timeout: float | None = None,
    diff_base: str | None = None,
    task: str | None = None,
) -> int:
    """Produce review findings at review/claude/, using the selected backend.

    Returns 0 on success, 1 on failure. `timeout` lets a budgeted caller (the
    loop) charge the review leg; when None the standalone ceiling applies
    (PXX_REVIEW_TIMEOUT, default 900s). `diff_base` scopes the local reviewer to
    ``diff_base..HEAD`` — the loop passes its start SHA so the review sees
    exactly the session's changes.
    """
    if timeout is None:
        timeout = float(os.environ.get("PXX_REVIEW_TIMEOUT", "900"))
    backend = _review_backend()
    if backend == "local":
        return _run_local_review(project_root, timeout, diff_base, task)
    if backend == "claude":
        return _run_claude_review(project_root, timeout)
    print(
        f"pxx: unknown PXX_REVIEW_BACKEND={backend!r} (use 'local' or 'claude')",
        file=sys.stderr,
    )
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
            # Near-miss guard: a line that *looks like* a finding header but
            # fails the strict format (hyphen instead of em-dash, missing
            # state, etc.) must not vanish — fewer findings means APPROVE, so
            # parser strictness could launder findings into silence. Surface
            # it as an UNPARSEABLE finding, which fails closed into REVISE
            # and appears in the healing prompt.
            near = re.match(r"^### (F-\d+)\b(.*)", line)
            if near:
                findings.append(
                    Finding(
                        id=near.group(1),
                        severity="UNPARSEABLE",
                        state="open",
                        location="",
                        description=f"unparseable finding header: {line.strip()}",
                    )
                )
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
