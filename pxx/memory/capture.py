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
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

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
            result = data.get("result", data.get("output", ""))
            result_text = str(result).strip()
            if not result_text:
                continue
            push(
                NewObservation(
                    kind="tool_result",
                    content=_cap(f"{tool}: {result_text}"),
                    source="tool_result",
                    confidence=0.6,
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


async def record_observations(
    store: MemoryStore,
    project: str,
    session_id: str,
    events: list[Event],
    *,
    pre_sha: str = "",
    root: str | Path | None = None,
) -> int:
    """Best-effort writer used by ``pxx.session``. Returns rows written; never raises.

    Phase 20.5: COMPLETED sessions write NOTHING automatically (no silent
    success-to-knowledge conversion); only FAILED sessions capture episodic
    observations, marked low-trust (failed_run_inference + contamination).
    """
    written = 0
    try:
        terminal = _terminal_code(events)
        if terminal == str(TerminalCode.COMPLETED):
            log.debug(
                "memory capture: skipping auto-write for completed session "
                "(successes are not auto-converted to knowledge)"
            )
            return 0
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
