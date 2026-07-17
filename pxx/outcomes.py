"""Normalized run outcomes and failure taxonomy — roadmap Phase 12, minimum.

The loop writes a machine-readable ``loop-terminal`` audit record naming WHY
it stopped; this module projects those records (plus the per-round stream)
into a typed ``RunOutcome``. No terminal condition is ever derived by parsing
free-text messages — that is the whole point of the taxonomy.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from pathlib import Path

from pxx import audit

# Canonical terminal codes (roadmap 12.2 + the INTERRUPTED lesson). One run
# has exactly one terminal code; contributing codes come later if Phase 13
# demonstrates the need.
FAILURE_CODES: frozenset[str] = frozenset(
    {
        "APPROVED",
        "EDIT_FAILED",
        "EDIT_TIMEOUT",
        "TEST_RUN_FAILED",
        "TEST_REGRESSION",
        "NO_TEST_PROGRESS",
        "LINT_BLOCKED",
        "REVIEW_REJECTED",
        "REVIEW_UNAVAILABLE",
        "REVIEW_EMPTY",
        "REVIEW_UNPARSEABLE",
        "OUT_OF_SCOPE",
        "DIFF_BUDGET_EXCEEDED",
        "ROUND_CAP_EXCEEDED",
        "TIME_BUDGET_EXCEEDED",
        "HOOKS_MISSING",
        "MODEL_UNAVAILABLE",
        "CONFIGURATION_INVALID",
        "INTERRUPTED",
    }
)


@dataclass(frozen=True)
class RunOutcome:
    """A loop run, normalized from its audit records (a projection — the
    JSONL stream stays the source of truth)."""

    run_id: str
    agent_version_id: str | None
    terminal_code: str
    accepted: bool
    rounds: int
    edit_seconds: float
    test_seconds: float
    review_seconds: float
    diff_lines: int
    baseline_failing: int
    introduced_failing: int
    findings_p0: int
    findings_p1: int
    findings_p2: int
    findings_unparseable: int
    verdicts: tuple[str, ...]
    start_sha: str | None
    end_sha: str | None


@dataclass(frozen=True)
class VerificationPacket:
    """Evidence that a change was verified, not merely claimed (roadmap 12,
    minimum shape — reproduction fields land with Phase 13's harness)."""

    run_id: str
    terminal_code: str
    accepted: bool
    baseline_commit: str | None
    result_commit: str | None
    verification_commands: tuple[str, ...]
    verification_results: tuple[str, ...]
    unresolved_risks: tuple[str, ...]


def outcome_from_records(records: list[dict]) -> RunOutcome | None:
    """Project one run's audit records into a RunOutcome.

    Requires a ``loop-terminal`` record (runs predating the taxonomy — or
    killed hard enough to skip it — yield None; absence of evidence is not
    an outcome)."""
    terminal = None
    rounds: list[dict] = []
    for r in records:
        cls = r.get("session_class")
        if cls == "loop-terminal":
            terminal = r
        elif cls == "loop-round":
            rounds.append(r)
    if terminal is None:
        return None
    rounds.sort(key=lambda r: r.get("round", 0))
    sev = {"P0": 0, "P1": 0, "P2": 0, "UNPARSEABLE": 0}
    for r in rounds:
        for k, v in (r.get("findings_by_severity") or {}).items():
            if k in sev:
                sev[k] += int(v)
    last = rounds[-1] if rounds else {}
    code = terminal.get("terminal_code", "CONFIGURATION_INVALID")
    return RunOutcome(
        run_id=terminal.get("run_id", ""),
        agent_version_id=terminal.get("agent_version_id"),
        terminal_code=code,
        accepted=code == "APPROVED",
        rounds=int(terminal.get("rounds", len(rounds))),
        edit_seconds=float(sum(r.get("edit_s", 0) for r in rounds)),
        test_seconds=float(sum(r.get("test_s", 0) for r in rounds)),
        review_seconds=float(sum(r.get("review_s", 0) for r in rounds)),
        diff_lines=int(last.get("diff_lines", 0)),
        baseline_failing=int(last.get("baseline_failing", 0)),
        introduced_failing=int(last.get("introduced_failing", 0)),
        findings_p0=sev["P0"],
        findings_p1=sev["P1"],
        findings_p2=sev["P2"],
        findings_unparseable=sev["UNPARSEABLE"],
        verdicts=tuple(r.get("verdict", "") for r in rounds),
        start_sha=terminal.get("start_sha"),
        end_sha=terminal.get("end_sha"),
    )


def verification_packet(outcome: RunOutcome) -> VerificationPacket:
    """The evidence summary a reviewer (human or promotion logic) consumes.

    Commands reflect what the loop actually ran deterministically each round;
    results come from the projected outcome, never from model claims."""
    results = (
        f"terminal={outcome.terminal_code}",
        f"rounds={outcome.rounds}",
        f"baseline_failing={outcome.baseline_failing}",
        f"introduced_failing={outcome.introduced_failing}",
        f"diff_lines={outcome.diff_lines}",
    )
    risks: tuple[str, ...] = ()
    if outcome.introduced_failing:
        risks += (f"{outcome.introduced_failing} test(s) newly failing",)
    if outcome.findings_unparseable:
        risks += (f"{outcome.findings_unparseable} unparseable review finding(s)",)
    return VerificationPacket(
        run_id=outcome.run_id,
        terminal_code=outcome.terminal_code,
        accepted=outcome.accepted,
        baseline_commit=outcome.start_sha,
        result_commit=outcome.end_sha,
        verification_commands=("uv run pytest -q", "ruff check (scoped)"),
        verification_results=results,
        unresolved_risks=risks,
    )


def _iter_audit_records(directory: Path | None = None):
    directory = directory or audit.log_dir()
    if not directory.exists():
        return
    for path in sorted(directory.iterdir()):
        try:
            if path.suffix == ".gz":
                text = gzip.decompress(path.read_bytes()).decode("utf-8")
            elif path.suffix == ".jsonl":
                text = path.read_text(encoding="utf-8")
            else:
                continue
        except OSError:
            continue
        for line in text.splitlines():
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def recent_outcomes(limit: int = 10, directory: Path | None = None) -> list[RunOutcome]:
    """Latest loop runs, newest first (run_ids are time-prefixed)."""
    by_run: dict[str, list[dict]] = {}
    for record in _iter_audit_records(directory):
        run_id = record.get("run_id")
        if run_id:
            by_run.setdefault(run_id, []).append(record)
    outcomes = []
    for run_id in sorted(by_run, reverse=True):
        outcome = outcome_from_records(by_run[run_id])
        if outcome is not None:
            outcomes.append(outcome)
        if len(outcomes) >= limit:
            break
    return outcomes


def outcome_for_run(run_id: str, directory: Path | None = None) -> RunOutcome | None:
    """Project a single run's outcome by id — the seam behind `pxx --verify`."""
    records = [r for r in _iter_audit_records(directory) if r.get("run_id") == run_id]
    return outcome_from_records(records) if records else None


def format_packet(packet: VerificationPacket) -> str:
    """Human-readable VerificationPacket — the evidence a reviewer reads
    instead of trusting the agent's claim of completion."""
    lines = [
        f"verification packet — run {packet.run_id}",
        f"  outcome:   {packet.terminal_code} (accepted={packet.accepted})",
        f"  baseline:  {packet.baseline_commit}",
        f"  result:    {packet.result_commit}",
        "  ran:       " + " ; ".join(packet.verification_commands),
        "  evidence:  " + " ".join(packet.verification_results),
    ]
    if packet.unresolved_risks:
        lines.append("  risks:     " + "; ".join(packet.unresolved_risks))
    else:
        lines.append("  risks:     none")
    return "\n".join(lines)
