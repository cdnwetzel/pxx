"""Review gate: parse reviewer output and enforce fail-closed policy.

A reviewer (an LLM, or a scripted stand-in in tests) inspects the post-round
diff and returns a structured verdict. pxx owns the *policy*: anything short
of an explicit ``APPROVE`` is fail-closed — unknown or missing verdicts parse
to ``NO_REVIEW``, and in ``BLOCKING`` mode ``NO_REVIEW``/``REVISE``/reviewer
unavailability all stop the loop. ``ADVISORY`` mode never blocks.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

import httpx

from .config import ModelRef
from .errors import PxxError

log = logging.getLogger("pxx.review")

SEVERITIES = ("low", "medium", "high")

#: Built-in fallback when ``pxx/prompts/review.md`` is not installed.
DEFAULT_REVIEW_PROMPT = """\
You are a strict code reviewer for an autonomous coding agent.
You are given the original task and the diff the agent produced.

Decide whether the diff correctly and completely implements the task.

Output format (machine-parsed — follow it exactly):

VERDICT: APPROVE
or
VERDICT: REVISE

When the verdict is REVISE, list concrete findings, one per line:
F-001 [high] path/to/file.py:42 short description of the problem
F-002 [low] path/to/other.py finding without a line number

Rules:
- APPROVE only when the diff fully satisfies the task with no defects.
- Findings must be specific and actionable; reference real files and lines
  from the diff.
- Do not request unrelated refactors or stylistic rewrites.
"""


class ReviewUnavailable(PxxError):
    """The reviewer backend could not produce a verdict (network/HTTP failure)."""


class Verdict(StrEnum):
    APPROVE = "APPROVE"
    REVISE = "REVISE"
    NO_REVIEW = "NO_REVIEW"


class ReviewMode(StrEnum):
    BLOCKING = "blocking"
    ADVISORY = "advisory"


@dataclass(frozen=True)
class Finding:
    id: str  # e.g. "F-001"
    severity: str  # "low" | "medium" | "high"
    file: str
    line: int | None
    message: str


@dataclass(frozen=True)
class ReviewResult:
    verdict: Verdict
    findings: tuple[Finding, ...]
    mode: ReviewMode
    blocked: bool
    review_error: str = ""  # "" | "unavailable" | "unparseable" | "empty"


@dataclass(frozen=True)
class ReviewPacket:
    """A review bound to the exact commit it reviewed (Phase 12 amend).

    A review approves a COMMIT, not a task: if HEAD advances past
    ``head_sha`` the packet is STALE and cannot approve the newer tree —
    the loop must re-review (fail-closed).
    """

    task: str
    base_sha: str
    head_sha: str  # the commit the verdict applies to
    verdict: str
    findings: tuple[Finding, ...]
    verify_command: str = ""
    reviewer: str = ""

    def is_stale(self, current_head: str) -> bool:
        """True when the tree moved past the reviewed commit."""
        return bool(self.head_sha) and current_head != self.head_sha


class Reviewer(Protocol):
    async def review(self, diff: str, task: str) -> str:
        """Return the raw reviewer text for ``diff`` against ``task``."""
        ...


_VERDICT_RE = re.compile(r"VERDICT\s*:\s*([A-Za-z_]+)", re.IGNORECASE)

#: Evidence anchors for a finding: a backticked concrete input/command, or a
#: file path inside the message text.
_BACKTICK_RE = re.compile(r"`[^`\n]+`")
_PATH_IN_MSG_RE = re.compile(r"[\w./-]+\.(?:py|md|toml|yaml|yml|json|js|ts|go|rs|sh|sql|txt)\b")


def _has_evidence(finding: Finding) -> bool:
    """A finding must be evidence-linked (Phase 14.4): file+line, OR a
    concrete anchor (backticked failing input/command, a named path) in the
    message. Generic 'looks wrong' findings have no locus and are rejected."""
    if finding.file and finding.line is not None:
        return True
    if _BACKTICK_RE.search(finding.message):
        return True
    return bool(_PATH_IN_MSG_RE.search(finding.message))


#: Tolerant finding parser: "F-001 [high] src/a.py:12 message". Brackets
#: optional, ":"/"-" separators tolerated, line number optional.
_FINDING_RE = re.compile(
    r"""
    ^\s*
    (?P<id>F-\d+)                          # F-001
    [\s\-:—]*
    [\[\(]?(?P<sev>low|medium|high)[\]\)]?  # [high] or (high) or high
    [\s\-:—]*
    (?P<file>[^\s:]+)                      # path
    (?::(?P<line>\d+))?                    # optional :line
    [\s\-:—]*
    (?P<msg>.*?)\s*$
    """,
    re.IGNORECASE | re.MULTILINE | re.VERBOSE,
)


@dataclass(frozen=True)
class ParsedReview:
    """Full parse result: verdict, kept findings, and rejection accounting."""

    verdict: Verdict
    findings: tuple[Finding, ...]
    had_verdict_line: bool
    raw_findings: int  # finding-shaped lines seen (before evidence filter)
    dropped: int  # evidence-less findings rejected (Phase 14.4)


def parse_review_full(text: str) -> ParsedReview:
    """Parse raw reviewer text; see :func:`parse_review` for the policy."""
    verdict = Verdict.NO_REVIEW
    had_verdict_line = False
    if m := _VERDICT_RE.search(text or ""):
        had_verdict_line = True
        try:
            verdict = Verdict(m.group(1).upper())
        except ValueError:
            verdict = Verdict.NO_REVIEW
    findings: list[Finding] = []
    raw_count = 0
    dropped = 0
    for f in _FINDING_RE.finditer(text or ""):
        raw_count += 1
        severity = f.group("sev").lower()
        if severity not in SEVERITIES:
            severity = "medium"
        finding = Finding(
            id=f.group("id").upper(),
            severity=severity,
            file=f.group("file"),
            line=int(f.group("line")) if f.group("line") else None,
            message=f.group("msg").strip() or "(no details)",
        )
        if _has_evidence(finding):
            findings.append(finding)
        else:
            dropped += 1
            log.info("dropping evidence-less finding %s (no concrete anchor)", finding.id)
    if raw_count and not findings and verdict is Verdict.REVISE:
        verdict = Verdict.NO_REVIEW
    return ParsedReview(
        verdict=verdict,
        findings=tuple(findings),
        had_verdict_line=had_verdict_line,
        raw_findings=raw_count,
        dropped=dropped,
    )


def parse_review(text: str) -> tuple[Verdict, list[Finding]]:
    """Parse raw reviewer text into a verdict and structured findings.

    Fail-closed: a missing or unknown ``VERDICT:`` line yields ``NO_REVIEW``.
    Finding lines tolerate minor format drift. Findings must be
    evidence-linked (Phase 14.4): a finding with no concrete anchor is
    DROPPED — it cannot force a healing cycle, and if every finding is
    rejected the review is unusable and maps to ``NO_REVIEW`` (never a
    vacuous APPROVE, never a generic block).
    """
    full = parse_review_full(text)
    return full.verdict, list(full.findings)


class NativeReviewer:
    """Reviewer backed by an OpenAI-compatible ``/v1/chat/completions`` endpoint."""

    def __init__(
        self,
        model: ModelRef,
        prompt_path: Path | None = None,
        *,
        timeout: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._model = model
        self._timeout = timeout
        self._transport = transport
        if prompt_path is None:
            prompt_path = Path(__file__).resolve().parent / "prompts" / "review.md"
        try:
            self._system_prompt = Path(prompt_path).read_text()
        except OSError:
            log.debug("review prompt %s unavailable; using built-in default", prompt_path)
            self._system_prompt = DEFAULT_REVIEW_PROMPT

    async def review(self, diff: str, task: str) -> str:
        model = self._model
        headers = {"Content-Type": "application/json"}
        if model.api_key:
            headers["Authorization"] = f"Bearer {model.api_key}"
        payload = {
            "model": model.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": f"# Task\n\n{task}\n\n# Diff under review\n\n{diff}"},
            ],
        }
        url = f"{model.endpoint}/v1/chat/completions"
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
            content = data["choices"][0]["message"]["content"]
        except httpx.HTTPError as exc:
            raise ReviewUnavailable(f"reviewer request failed: {exc}") from exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ReviewUnavailable(f"malformed reviewer response: {exc!r}") from exc
        if not isinstance(content, str):
            raise ReviewUnavailable("malformed reviewer response: content is not text")
        return content


async def review_changes(
    diff: str,
    task: str,
    reviewer: Reviewer,
    mode: ReviewMode = ReviewMode.BLOCKING,
) -> ReviewResult:
    """Run the reviewer and apply fail-closed policy.

    ``ReviewUnavailable`` or ``NO_REVIEW`` block in ``BLOCKING`` mode;
    ``REVISE`` blocks in ``BLOCKING`` mode; ``ADVISORY`` never blocks.
    """
    try:
        text = await reviewer.review(diff, task)
    except ReviewUnavailable as exc:
        log.warning("reviewer unavailable: %s", exc)
        return ReviewResult(
            verdict=Verdict.NO_REVIEW,
            findings=(),
            mode=mode,
            blocked=mode is ReviewMode.BLOCKING,
            review_error="unavailable",
        )
    full = parse_review_full(text)
    verdict, findings = full.verdict, full.findings
    review_error = ""
    if verdict is Verdict.NO_REVIEW:
        review_error = "empty" if full.raw_findings else "unparseable"
    blocked = mode is ReviewMode.BLOCKING and verdict is not Verdict.APPROVE
    return ReviewResult(
        verdict=verdict,
        findings=tuple(findings),
        mode=mode,
        blocked=blocked,
        review_error=review_error,
    )


def build_healing_prompt(
    task: str, findings: list[Finding] | tuple[Finding, ...], round_no: int
) -> str:
    """Build the next-round prompt instructing the agent to fix the findings."""
    lines = [
        f"You are in healing round {round_no} of an autonomous edit -> test -> review loop.",
        "",
        "# Original task",
        "",
        task,
        "",
        "# Findings you MUST address",
        "",
    ]
    for f in findings:
        loc = f.file + (f":{f.line}" if f.line is not None else "")
        lines.append(f"- {f.id} [{f.severity}] {loc} — {f.message}")
    lines += [
        "",
        "Address every finding above with the minimal change that resolves it.",
        "Do not refactor unrelated code. All findings must be resolved.",
    ]
    return "\n".join(lines)
