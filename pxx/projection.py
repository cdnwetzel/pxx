"""Phase 10.8: project RunOutcome FROM the typed event stream.

The audit stream is the single source of truth: the recorded outcome of a
run is a pure projection of its events, so the record can never disagree
with what actually happened. Sessions still RETURN a constructed outcome
for control flow; the persisted record (outcome.json) is written from this
projection.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from .events import Event
from .outcome import RunOutcome, TerminalCode

log = logging.getLogger("pxx.projection")


def project_outcome(events: Iterable[Event | dict[str, Any]], session_id: str = "") -> RunOutcome:
    """Project a RunOutcome from an event stream (or events.jsonl dicts).

    Accepts Event objects or plain ``{"kind", "data", "session_id"}``
    mappings. Unknown/missing pieces degrade to neutral values (never a
    crash — projection is a read).
    """
    code = TerminalCode.MODEL_UNAVAILABLE
    summary = ""
    rounds = 0
    tokens = 0
    files_changed = 0
    unparseable = 0
    baseline_failures = 0
    introduced_failures = 0
    terminal_failures = 0
    contributing: list[str] = []
    findings_by_severity: dict[str, int] = {}
    findings: list[dict] = []
    sid = ""
    test_seconds = review_seconds = 0.0

    for event in events:
        if isinstance(event, dict):
            kind = event.get("kind", "")
            data = event.get("data") or {}
            event_sid = event.get("session_id", "")
        else:
            kind = event.kind
            data = event.data
            event_sid = event.session_id
        if session_id and event_sid and event_sid != session_id:
            continue
        if event_sid:
            sid = event_sid

        if kind == "session_end":
            try:
                code = TerminalCode(str(data.get("code", "")))
            except ValueError:
                code = TerminalCode.MODEL_UNAVAILABLE
            summary = str(data.get("summary", ""))
            rounds = int(data.get("rounds") or 0)
            tokens = int(data.get("tokens") or 0)
            test_seconds = float(data.get("test_seconds") or 0.0)
            review_seconds = float(data.get("review_seconds") or 0.0)
        elif kind == "file_changed":
            files_changed += 1
        elif kind == "gate_decision":
            gate = data.get("gate")
            if gate == "tests":
                failing = int(data.get("failing") or 0)
                terminal_failures = failing
                introduced = len(data.get("new_failures") or ())
                introduced_failures = max(introduced_failures, introduced)
                if baseline_failures == 0:
                    baseline_failures = failing
            elif gate == "review" and data.get("verdict") == "NO_REVIEW":
                unparseable += 1
                if "REVIEW_UNPARSEABLE" not in contributing:
                    contributing.append("REVIEW_UNPARSEABLE")
        elif kind == "observation" and data.get("findings_by_severity"):
            findings_by_severity = dict(data["findings_by_severity"])

    return RunOutcome(
        code=code,
        summary=summary,
        rounds=rounds,
        tokens=tokens,
        files_changed=files_changed,
        baseline_failures=baseline_failures,
        introduced_failures=introduced_failures,
        terminal_failures=terminal_failures,
        unparseable_review_count=unparseable,
        findings_by_severity=findings_by_severity,
        findings=tuple(findings),
        contributing_codes=tuple(contributing),
        test_seconds=test_seconds,
        review_seconds=review_seconds,
        session_id=sid,
    )


__all__ = ["project_outcome"]
