"""Reviewer calibration suite (Phase 14).

Runs a reviewer (the ``pxx.review.Reviewer`` protocol) against a fixed corpus
of TOML cases under ``evals/calibration/`` and scores it on:

- ``recall`` — fraction of expected-flag cases the reviewer actually flagged
  (known critical defects must be caught);
- ``fp_rate`` — fraction of expected-clean cases it flagged (acceptable or
  noisy-but-harmless changes must be left alone);
- ``format_compliance`` — fraction of reviewer responses that parsed to a
  definitive verdict (``APPROVE``/``REVISE``) among calls that returned;
- ``availability`` — fraction of calls that returned parseable output without
  raising.

Scoring goes through the production ``pxx.review.parse_review`` path so
calibration can never drift from what the runtime actually enforces.

Fail-closed: reviewer exceptions and unparseable output count as *flagged*
(the change is blocked, never silently approved) while degrading the
availability/format metrics. Thresholds are explicit module constants; any
breach means the reviewer fails calibration (``report.ok`` is ``False`` and
``breaches(report)`` names each breached threshold) so a CLI can exit 2.
"""

from __future__ import annotations

import logging
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .errors import ConfigError, PxxError
from .review import SEVERITIES, Finding, Reviewer, Verdict, parse_review

log = logging.getLogger("pxx.calibration")

#: Calibration thresholds — any breach fails the reviewer.
MIN_RECALL = 0.75
MAX_FP_RATE = 0.25
MIN_FORMAT_COMPLIANCE = 0.9
MIN_AVAILABILITY = 0.75
MIN_AGREEMENT = 0.75  # overall verdict agreement with ground truth

_SEVERITY_RANK = {sev: rank for rank, sev in enumerate(SEVERITIES)}

_REQUIRED_FIELDS = ("id", "kind", "diff", "task", "expect")


class CaseKind(StrEnum):
    CRITICAL = "critical"  # known critical defect — MUST be flagged
    ACCEPTABLE = "acceptable"  # good change — must NOT be flagged
    NOISY = "noisy"  # noisy but harmless — must NOT be flagged
    MALFORMED = "malformed"  # malformed-review tolerance — must fail closed
    EDGE = "edge"  # edge cases (empty diff, line-number drift, ...)


class Expect(StrEnum):
    FLAG = "flag"
    CLEAN = "clean"


@dataclass(frozen=True)
class Case:
    """One calibration case: a diff under review plus the expected outcome."""

    id: str
    kind: CaseKind
    diff: str
    task: str
    expect: Expect
    min_severity: str | None = None  # flag cases: weakest finding that counts


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    kind: CaseKind
    expect: Expect
    verdict: Verdict
    findings: tuple[Finding, ...]
    available: bool  # reviewer returned without raising
    parseable: bool  # output parsed to a definitive verdict (not NO_REVIEW)
    flagged: bool  # fail-closed: anything short of APPROVE blocks the change
    passed: bool  # case expectation met (incl. min_severity for flag cases)


@dataclass(frozen=True)
class CalibrationReport:
    recall: float
    fp_rate: float
    format_compliance: float
    availability: float
    results: tuple[CaseResult, ...]
    warnings: tuple[str, ...] = ()  # dimensions that silently couldn't run
    agreement: float = 0.0  # overall verdict agreement with ground truth

    @property
    def ok(self) -> bool:
        """True when no threshold is breached."""
        return not breaches(self)


def load_case(path: str | Path) -> Case:
    """Load and validate one calibration case TOML. Fail-closed on bad input."""
    path = Path(path)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"calibration case {path}: {exc}") from exc
    missing = [field for field in _REQUIRED_FIELDS if field not in data]
    if missing:
        raise ConfigError(f"calibration case {path}: missing fields {missing}")
    try:
        kind = CaseKind(data["kind"])
        expect = Expect(data["expect"])
    except ValueError as exc:
        raise ConfigError(f"calibration case {path}: {exc}") from exc
    min_severity = data.get("min_severity")
    if min_severity is not None and min_severity not in _SEVERITY_RANK:
        raise ConfigError(f"calibration case {path}: unknown min_severity {min_severity!r}")
    return Case(
        id=str(data["id"]),
        kind=kind,
        diff=str(data["diff"]),
        task=str(data["task"]),
        expect=expect,
        min_severity=min_severity,
    )


def load_cases(directory: str | Path) -> tuple[Case, ...]:
    """Load every ``*.toml`` case in ``directory``, sorted by filename."""
    directory = Path(directory)
    paths = sorted(directory.glob("*.toml"))
    if not paths:
        raise ConfigError(f"no calibration cases found in {directory}")
    cases = tuple(load_case(p) for p in paths)
    ids = [c.id for c in cases]
    if len(set(ids)) != len(ids):
        raise ConfigError(f"duplicate calibration case ids in {directory}: {ids}")
    return cases


async def run_calibration(reviewer: Reviewer, cases: Iterable[Case]) -> CalibrationReport:
    """Score ``reviewer`` against ``cases`` using production parsing.

    Reviewer exceptions count against availability and never crash the run.
    """
    results: list[CaseResult] = []
    for case in cases:
        results.append(await _run_case(reviewer, case))
    report = _build_report(tuple(results))
    log.info(
        "calibration: recall=%.3f fp_rate=%.3f format=%.3f availability=%.3f ok=%s",
        report.recall,
        report.fp_rate,
        report.format_compliance,
        report.availability,
        report.ok,
    )
    return report


async def _run_case(reviewer: Reviewer, case: Case) -> CaseResult:
    available = True
    try:
        text = await reviewer.review(case.diff, case.task)
    except Exception as exc:  # telemetry/suppress: reviewer errors never crash us
        log.warning("calibration case %s: reviewer raised %r", case.id, exc)
        available = False
        verdict, findings = Verdict.NO_REVIEW, []
    else:
        verdict, findings = parse_review(text)
    parseable = available and verdict is not Verdict.NO_REVIEW
    # Fail closed: anything short of an explicit APPROVE flags the change.
    flagged = verdict is not Verdict.APPROVE
    passed = _case_passed(case, flagged=flagged, findings=findings)
    return CaseResult(
        case_id=case.id,
        kind=case.kind,
        expect=case.expect,
        verdict=verdict,
        findings=tuple(findings),
        available=available,
        parseable=parseable,
        flagged=flagged,
        passed=passed,
    )


def _case_passed(case: Case, *, flagged: bool, findings: list[Finding]) -> bool:
    if case.expect is Expect.CLEAN:
        return not flagged
    if not flagged:
        return False
    if case.min_severity is None:
        return True
    # Tolerant: any finding at/above min_severity counts — file/line drift in
    # reviewer output does not (wrong-line-number tolerance).
    required = _SEVERITY_RANK[case.min_severity]
    return any(_SEVERITY_RANK.get(f.severity, -1) >= required for f in findings)


def _build_report(results: tuple[CaseResult, ...]) -> CalibrationReport:
    total = len(results)
    flag_cases = [r for r in results if r.expect is Expect.FLAG]
    clean_cases = [r for r in results if r.expect is Expect.CLEAN]
    recall = sum(1 for r in flag_cases if r.passed) / len(flag_cases) if flag_cases else 1.0
    fp_rate = sum(1 for r in clean_cases if r.flagged) / len(clean_cases) if clean_cases else 0.0
    available = sum(1 for r in results if r.available)
    parseable = sum(1 for r in results if r.parseable)
    if available:
        format_compliance = parseable / available
    else:
        format_compliance = 1.0 if total == 0 else 0.0
    availability = parseable / total if total else 1.0
    # Verdict agreement with ground truth (beyond per-class recall/fp):
    # fraction of cases where the reviewer's flag/clean call was correct.
    agreed = sum(1 for r in results if r.passed)
    agreement = agreed / total if total else 1.0
    # A lopsided corpus makes a dimension vacuous (recall 1.0 with zero flag
    # cases proves nothing) — surface it loudly instead of letting the
    # missing dimension pass silently.
    warnings: list[str] = []
    if not flag_cases:
        warnings.append(
            "no expect=flag cases in corpus: recall dimension is vacuous (1.0 by default)"
        )
    if not clean_cases:
        warnings.append(
            "no expect=clean cases in corpus: false-positive dimension is vacuous (0.0 by default)"
        )
    return CalibrationReport(
        recall=recall,
        fp_rate=fp_rate,
        format_compliance=format_compliance,
        availability=availability,
        results=results,
        warnings=tuple(warnings),
        agreement=agreement,
    )


class CaptureMissing(PxxError):
    """No recorded response exists for a case (counts as unavailable)."""


@dataclass(frozen=True)
class RecordedReviewer:
    """Score an EXTERNAL reviewer from captured output, via the production path.

    CodeRabbit, Greptile, and Copilot review *pull requests*; they cannot be
    handed a diff and asked for a verdict, so they cannot implement
    :class:`~pxx.review.Reviewer` live. Capture what they said about each case
    once, then replay it here — ``run_calibration`` then scores them through the
    same ``parse_review`` path, the same ``_case_passed`` rules, and the same
    thresholds as a local model. That is the point: an external reviewer is held
    to *exactly* the bar the sovereign reviewer is held to, with no separate
    scoring code that could drift or flatter.

    Keyed by case ``id``, resolved from the diff, because the ``Reviewer``
    protocol passes only ``(diff, task)``.

    Fail-closed: a case with no capture raises, which ``run_calibration`` counts
    against availability and treats as flagged. A reviewer that stayed silent
    scores as unavailable rather than as a free pass — silence is not approval.
    """

    responses: dict[str, str]
    _by_diff: dict[str, str]

    @classmethod
    def from_cases(cls, cases: Iterable[Case], responses: dict[str, str]) -> RecordedReviewer:
        cases = tuple(cases)
        known = {c.id for c in cases}
        unknown = sorted(set(responses) - known)
        if unknown:
            # A capture for a case that is not in the corpus means the bench and
            # the corpus have diverged; scoring it would silently measure the
            # wrong thing.
            raise ConfigError(f"recorded responses for unknown case ids: {unknown}")
        return cls(responses=dict(responses), _by_diff={c.diff: c.id for c in cases})

    async def review(self, diff: str, task: str) -> str:
        case_id = self._by_diff.get(diff)
        if case_id is None:
            raise CaptureMissing("diff does not correspond to any loaded case")
        try:
            return self.responses[case_id]
        except KeyError:
            raise CaptureMissing(f"no recorded response for case {case_id!r}") from None


def breaches(report: CalibrationReport) -> list[str]:
    """Name every breached threshold; empty list means calibration passed."""
    out: list[str] = []
    if report.recall < MIN_RECALL:
        out.append(f"recall {report.recall:.3f} below MIN_RECALL {MIN_RECALL}")
    if report.fp_rate > MAX_FP_RATE:
        out.append(f"fp_rate {report.fp_rate:.3f} above MAX_FP_RATE {MAX_FP_RATE}")
    if report.format_compliance < MIN_FORMAT_COMPLIANCE:
        out.append(
            f"format_compliance {report.format_compliance:.3f} "
            f"below MIN_FORMAT_COMPLIANCE {MIN_FORMAT_COMPLIANCE}"
        )
    if report.availability < MIN_AVAILABILITY:
        out.append(
            f"availability {report.availability:.3f} below MIN_AVAILABILITY {MIN_AVAILABILITY}"
        )
    if report.agreement < MIN_AGREEMENT:
        out.append(f"agreement {report.agreement:.3f} below MIN_AGREEMENT {MIN_AGREEMENT}")
    return out
