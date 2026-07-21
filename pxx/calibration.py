"""Reviewer calibration — roadmap Phase 14.3, minimum slice.

The eval lab measures the editor; this measures the REVIEWER. A fixed,
optimizer-protected set of diffs with known ground truth is fed through the
exact production review path (same instructions, same endpoint config, same
parser), and the reviewer's answers are scored against that truth:

- critical-defect recall   — of the diffs that contain a real defect, how
                             many did the reviewer flag at all?
- false-positive rate      — of the diffs known to be clean, how many drew
                             findings anyway? (r5's live failure class:
                             a false-positive REVISE against a correct fix
                             sent the loop into a healing spin.)
- format compliance        — did the output honor the F-NNN contract or the
                             exact no-findings line?
- availability             — did the call succeed at all?

Thresholds are explicit (14.3 exit criterion): breaching them makes
``pxx --calibrate`` exit non-zero. Same-model review is lower-confidence
evidence — the report records which reviewer it measured.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pxx import review_gate

CALIBRATION_DIR = Path(__file__).resolve().parent.parent / "evals" / "calibration"
SCHEMA_VERSION = 1

# Initial thresholds — engineering bars, revisited as data accumulates.
MIN_RECALL = 0.75
MAX_FALSE_POSITIVE_RATE = 0.25

_NO_FINDINGS_LINE = "# Review pass: no findings."


@dataclass(frozen=True)
class CalibrationCase:
    id: str
    kind: str  # "clean" | "defect"
    description: str
    diff: str
    task: str | None  # the request the diff claims to satisfy (mirrors production)
    expected_severity: str | None  # defect cases: minimum severity expected
    path: Path


@dataclass(frozen=True)
class CaseVerdict:
    case_id: str
    kind: str
    available: bool
    findings: int
    format_compliant: bool
    correct: bool  # clean→no findings | defect→at least one finding


@dataclass(frozen=True)
class CalibrationReport:
    reviewer_backend: str
    reviewer_model: str
    date: str
    verdicts: tuple[CaseVerdict, ...]
    recall: float
    false_positive_rate: float
    format_compliance: float
    availability: float

    @property
    def within_thresholds(self) -> bool:
        return (
            self.recall >= MIN_RECALL
            and self.false_positive_rate <= MAX_FALSE_POSITIVE_RATE
        )


def load_calibration_cases(directory: Path | None = None) -> list[CalibrationCase]:
    directory = directory or CALIBRATION_DIR
    cases = []
    for path in sorted(directory.glob("*.toml")):
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        if data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"{path.name}: schema_version must be {SCHEMA_VERSION}")
        kind = data["kind"]
        if kind not in ("clean", "defect"):
            raise ValueError(f"{path.name}: kind must be clean|defect")
        cases.append(
            CalibrationCase(
                id=data["id"],
                kind=kind,
                description=data.get("description", ""),
                diff=data["diff"],
                task=data.get("task"),
                expected_severity=data.get("expected_severity"),
                path=path,
            )
        )
    return cases


def judge_response(case: CalibrationCase, content: str) -> CaseVerdict:
    """Score one reviewer response against the case's ground truth.

    Pure — the seam unit tests exercise without a model."""
    findings = review_gate.parse_findings(content)
    stripped = content.strip()
    compliant = bool(findings) or stripped == _NO_FINDINGS_LINE
    correct = (not findings) if case.kind == "clean" else bool(findings)
    return CaseVerdict(
        case_id=case.id,
        kind=case.kind,
        available=True,
        findings=len(findings),
        format_compliant=compliant,
        correct=correct,
    )


def run_calibration(
    cases: list[CalibrationCase] | None = None, timeout: float = 120.0
) -> CalibrationReport:
    """Feed every case through the PRODUCTION review path and score it."""
    cases = cases if cases is not None else load_calibration_cases()
    url = review_gate._review_url()
    model = review_gate._review_model()
    verdicts: list[CaseVerdict] = []
    for case in cases:
        try:
            content = review_gate._post_chat(
                url,
                model,
                review_gate.build_review_prompt(case.diff, case.task),
                timeout,
            )
        except Exception:
            verdicts.append(CaseVerdict(case.id, case.kind, False, 0, False, False))
            continue
        verdicts.append(judge_response(case, content))

    defect = [v for v in verdicts if v.kind == "defect"]
    clean = [v for v in verdicts if v.kind == "clean"]
    answered = [v for v in verdicts if v.available]
    return CalibrationReport(
        reviewer_backend=review_gate._review_backend(),
        reviewer_model=model,
        date=datetime.now().date().isoformat(),
        verdicts=tuple(verdicts),
        recall=(sum(1 for v in defect if v.correct) / len(defect) if defect else 0.0),
        false_positive_rate=(
            sum(1 for v in clean if not v.correct) / len(clean) if clean else 0.0
        ),
        format_compliance=(
            sum(1 for v in answered if v.format_compliant) / len(answered)
            if answered
            else 0.0
        ),
        availability=len(answered) / len(verdicts) if verdicts else 0.0,
    )


def save_report(report: CalibrationReport, directory: Path | None = None) -> Path:
    directory = directory or (
        Path(__file__).resolve().parent.parent / "evals" / "baselines"
    )
    directory.mkdir(parents=True, exist_ok=True)
    safe_model = report.reviewer_model.replace("/", "-").replace(":", "-")
    out = directory / f"reviewer-{safe_model}.json"
    out.write_text(
        json.dumps(
            {
                "reviewer_backend": report.reviewer_backend,
                "reviewer_model": report.reviewer_model,
                "date": report.date,
                "recall": report.recall,
                "false_positive_rate": report.false_positive_rate,
                "format_compliance": report.format_compliance,
                "availability": report.availability,
                "within_thresholds": report.within_thresholds,
                "thresholds": {
                    "min_recall": MIN_RECALL,
                    "max_false_positive_rate": MAX_FALSE_POSITIVE_RATE,
                },
                "verdicts": [
                    {
                        "case": v.case_id,
                        "kind": v.kind,
                        "available": v.available,
                        "findings": v.findings,
                        "format_compliant": v.format_compliant,
                        "correct": v.correct,
                    }
                    for v in report.verdicts
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return out
