"""Post-session observation capture.

Rolls the session's event history (tool results, file changes, explicit
observations) and an optional git diff up into deduped :class:`NewObservation`
records, and writes them into the memory store. This is telemetry:
:func:`record_observations` is best-effort and never raises.

Phase 20.5 (contamination discipline): a COMPLETED run is **never**
auto-converted into knowledge — a success may be right for the wrong
reason. Only explicit `remember` calls, or graduated/validated lessons,
enter the durable layers. FAILED runs record *episodic* observations with
``failed_run_inference`` provenance (EVIDENCE_RANK 0.2) and
``contamination_risk=0.5``, so failure lessons are visible but visibly
low-trust. Frequency != correctness.

Opt-in amendment (``memory_capture_successes``): when the operator flips
that setting on, a COMPLETED run writes EXACTLY ONE compact
``session_outcome`` exemplar (see :func:`record_observations`) — one line,
gate-verified provenance, lower contamination than a failure inference —
so the graduation ladder can also learn from verified successes. The
default stays off; Phase 20.5 semantics are preserved unless the operator
opts in.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..gitenv import git_env
from ..outcome import TerminalCode
from .store import EVIDENCE_RANK, KnowledgeLayer

if TYPE_CHECKING:
    from ..events import Event
    from .store import MemoryStore

log = logging.getLogger("pxx.memory.capture")

#: Observations are capped so one noisy tool result cannot flood memory.
MAX_CONTENT_CHARS = 2000

#: Contamination applied to failed-run inferences (Phase 20).
FAILED_CONTAMINATION_RISK = 0.5

#: Contamination applied to opt-in success exemplars: below a failure
#: inference (0.5) because the session's own gates verified the outcome,
#: but nonzero — a success may still be right for the wrong reason. Well
#: below ``MemoryStore.auto_quarantine``'s 0.7 default threshold, so an
#: exemplar is never quarantined by construction.
SUCCESS_CONTAMINATION_RISK = 0.3

#: Confidence for opt-in success exemplars: above the failed-run inference
#: evidence rank (0.2 — the gates verified this outcome, nothing about a
#: failure is inferred), below explicit `remember` observations (0.8).
SUCCESS_CONFIDENCE = 0.6

#: Event kinds that never become observations (chatter, not learnings).
_SKIP_KINDS = frozenset(
    {
        "session_start",
        "session_end",
        "model_request",
        "model_response",
        "tool_call",
        "gate_decision",
        "budget",
        "error",
    }
)


@dataclass(frozen=True)
class NewObservation:
    """A not-yet-stored observation produced by capture."""

    kind: str
    content: str
    tags: tuple[str, ...] = field(default_factory=tuple)
    source: str = ""
    confidence: float = 0.7


def _cap(text: str) -> str:
    text = text.strip()
    return text[:MAX_CONTENT_CHARS]


def _data(event: Any) -> dict:
    data = getattr(event, "data", None)
    return data if isinstance(data, dict) else {}


def _terminal_code(events: list[Event]) -> str:
    """Terminal code from the last ``session_end`` event ('' when absent)."""
    code = ""
    for event in events:
        if getattr(event, "kind", "") == "session_end":
            found = _data(event).get("code")
            if found:
                code = str(found)
    return code


def observations_from_events(events: list[Event]) -> list[NewObservation]:
    """Roll up tool_result / file_changed / observation events, deduped."""
    out: list[NewObservation] = []
    seen: set[str] = set()

    def push(obs: NewObservation) -> None:
        if obs.content and obs.content not in seen:
            seen.add(obs.content)
            out.append(obs)

    for event in events:
        kind = getattr(event, "kind", "")
        if kind in _SKIP_KINDS:
            continue
        data = _data(event)
        if kind == "tool_result":
            tool = str(data.get("tool") or data.get("name") or "tool")
            result = data.get("result_preview", data.get("result", data.get("output", "")))
            result_text = str(result).strip()
            if not result_text:
                continue
            push(
                NewObservation(
                    kind="tool_result",
                    content=_cap(f"{tool}: {result_text}"),
                    source="tool_result",
                    confidence=(0.3 if data.get("error") else 0.6),
                )
            )
        elif kind == "file_changed":
            path = data.get("path")
            if not path:
                continue
            detail = str(data.get("summary") or data.get("diff_stat") or "").strip()
            content = f"changed file: {path}" + (f" ({detail})" if detail else "")
            push(
                NewObservation(
                    kind="file_changed",
                    content=_cap(content),
                    tags=("files",),
                    source="file_changed",
                    confidence=0.7,
                )
            )
        elif kind == "observation":
            content = str(data.get("content") or data.get("text") or "").strip()
            if not content:
                continue
            tags = data.get("tags") or ()
            push(
                NewObservation(
                    kind=str(data.get("kind") or "observation"),
                    content=_cap(content),
                    tags=tuple(str(t) for t in tags),
                    source="observation",
                    confidence=float(data.get("confidence", 0.8)),
                )
            )
    return out


async def _git(root: Path, *args: str) -> str | None:
    """Run a git command; return stdout or None on any failure (no repo, no git)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=str(root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=git_env(),
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15.0)
    except (OSError, TimeoutError):
        return None
    if proc.returncode != 0:
        return None
    return stdout.decode(errors="replace")


async def observations_from_git(pre_sha: str, root: str | Path) -> list[NewObservation]:
    """Summarize ``git diff pre_sha`` (stat + name-only). ``[]`` outside a repo."""
    if not pre_sha:
        return []
    root = Path(root)
    stat, names = await asyncio.gather(
        _git(root, "diff", "--stat", "--no-renames", pre_sha),
        _git(root, "diff", "--name-only", "--no-renames", pre_sha),
    )
    if stat is None or names is None:
        return []
    files = [line.strip() for line in names.splitlines() if line.strip()]
    if not files:
        return []
    summary = stat.strip().splitlines()[-1].strip() if stat.strip() else ""
    content = f"git diff since {pre_sha[:12]}: {len(files)} file(s): {', '.join(files)}"
    if summary:
        content += f"\n{summary}"
    return [
        NewObservation(
            kind="git_diff",
            content=_cap(content),
            tags=("files",),
            source="git",
            confidence=0.7,
        )
    ]


def _agent_version_id(events: list[Event]) -> str:
    for event in events:
        if getattr(event, "kind", "") == "session_start":
            return str(_data(event).get("agent_version_id") or "")
    return ""


def _failure_provenance(events: list[Event], terminal: str) -> tuple[str, str]:
    """Map a terminal run to the 5-level EVIDENCE_RANK ladder (20.1).

    Returns (provenance, validation). Only failed runs auto-capture, so the
    ladder entry is almost always ``failed_run_inference``; a failed run that
    nonetheless carried deterministic test evidence ranks one notch up.
    """
    if terminal and terminal != str(TerminalCode.COMPLETED):
        tests_ran = any(
            getattr(e, "kind", "") == "gate_decision" and _data(e).get("gate") == "tests"
            for e in events
        )
        if tests_ran:
            return "failed_run_inference", "tests"
        return "failed_run_inference", "none"
    review_approved = any(
        getattr(e, "kind", "") == "gate_decision"
        and _data(e).get("gate") == "review"
        and _data(e).get("verdict") == "APPROVE"
        for e in events
    )
    if review_approved:
        return "reviewer_agreement", "review"
    return "model_claim", "none"


def _success_exemplar(events: list[Event]) -> NewObservation:
    """The ONE compact observation an opt-in COMPLETED session writes.

    Bounded, non-sensitive SHAPE metadata only — files-changed count
    (``file_changed`` events) and tool-call count. The raw task preview is
    deliberately NOT stored: it is free-form user-prompt text (truncation does
    not remove secrets), and this row is DURABLE memory that later becomes
    prompt context, so persisting it would be a data-exposure vector
    (CodeRabbit). The session id is likewise not part of the content: the store
    dedupes on ``sha256(project + content)`` and a repeat increments
    ``seen_count`` (store.py recurrence), so identical verified-success shapes
    across sessions collapse into one row whose recurrence grows — the signal
    the episodic→skill graduation ladder consumes. The session id is still
    recorded in its own column.
    """
    files = 0
    calls = 0
    for event in events:
        kind = getattr(event, "kind", "")
        if kind == "file_changed":
            files += 1
        elif kind == "tool_call":
            calls += 1
    content = _cap(f"completed run: {files} file(s) changed, {calls} tool call(s)")
    return NewObservation(
        kind="session_outcome",
        content=content,
        tags=("outcome",),
        source="completed_run",
        confidence=SUCCESS_CONFIDENCE,
    )


async def record_observations(
    store: MemoryStore,
    project: str,
    session_id: str,
    events: list[Event],
    *,
    pre_sha: str = "",
    root: str | Path | None = None,
    capture_successes: bool = False,
) -> int:
    """Best-effort writer used by ``pxx.session``. Returns rows written; never raises.

    Phase 20.5: COMPLETED sessions write NOTHING automatically (no silent
    success-to-knowledge conversion); only FAILED sessions capture episodic
    observations, marked low-trust (failed_run_inference + contamination).

    Opt-in: with ``capture_successes`` a COMPLETED session writes EXACTLY
    ONE gate-verified ``session_outcome`` exemplar (see
    :func:`_success_exemplar`); provenance comes from the same ladder a
    completed run maps to (``reviewer_agreement`` when the review gate
    approved, else ``model_claim``). The default preserves Phase 20.5.
    """
    written = 0
    try:
        terminal = _terminal_code(events)
        if terminal == str(TerminalCode.COMPLETED):
            if not capture_successes:
                log.debug(
                    "memory capture: skipping auto-write for completed session "
                    "(successes are not auto-converted to knowledge)"
                )
                return 0
            obs = _success_exemplar(events)
            provenance, validation = _failure_provenance(events, terminal)
            try:
                await store.add(
                    project,
                    obs.kind,
                    obs.content,
                    tags=obs.tags,
                    source=obs.source,
                    session_id=session_id,
                    confidence=obs.confidence,
                    evidence_confidence=EVIDENCE_RANK[provenance],
                    contamination_risk=SUCCESS_CONTAMINATION_RISK,
                    outcome=terminal,
                    layer=str(KnowledgeLayer.EPISODIC),
                    provenance=provenance,
                    validation=validation,
                    agent_version_id=_agent_version_id(events),
                )
                written += 1
            except Exception:
                log.exception("observation write failed (best-effort, continuing)")
            return written
        failed = bool(terminal)
        if not failed:
            return 0
        observations = observations_from_events(events)
        if pre_sha and root is not None:
            observations.extend(await observations_from_git(pre_sha, root))
        provenance, validation = _failure_provenance(events, terminal)
        evidence = EVIDENCE_RANK[provenance]
        agent_version_id = _agent_version_id(events)
        for obs in observations:
            try:
                await store.add(
                    project,
                    obs.kind,
                    obs.content,
                    tags=obs.tags,
                    source=obs.source or "session_failed",
                    session_id=session_id,
                    confidence=obs.confidence,
                    evidence_confidence=evidence,
                    contamination_risk=FAILED_CONTAMINATION_RISK,
                    outcome=terminal,
                    layer=str(KnowledgeLayer.EPISODIC),
                    provenance=provenance,
                    validation=validation,
                    agent_version_id=agent_version_id,
                )
                written += 1
            except Exception:
                log.exception("observation write failed (best-effort, continuing)")
    except Exception:
        log.exception("memory capture failed (best-effort, continuing)")
    return written
