"""Phase 18: deployment channels, shadow runs, circuit breakers.

Channels (``stable`` / ``candidate`` / ``shadow`` / ``retired``) are persisted
in ``<state_dir>/channels.json``. ``activate`` assigns an agent version to a
channel; ``rollback`` restores the EXACT previous stable version (a stack of
prior stable assignments is kept); ``history`` returns the audit trail of
channel transitions.

``shadow_run`` lets a candidate replay a task in an ISOLATED worktree (a real
git worktree when the tree is a git repo, otherwise a disposable copy): the
stable backend does the real task in the main worktree, the candidate's output
is scored and NEVER merged, and the stable channel assignment is never touched
during candidate runs.

Circuit breakers are evaluated per candidate run
(:func:`evaluate_candidate_run`): scope violation, critical evaluator failure,
budget overrun, or unexpected files retire the candidate IMMEDIATELY, with a
best-effort audit event (telemetry never crashes the plane).
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

log = logging.getLogger("pxx.improve.channels")


class Channel(StrEnum):
    STABLE = "stable"
    CANDIDATE = "candidate"
    SHADOW = "shadow"
    CANARY = "canary"
    RETIRED = "retired"


#: Active (assignable) channels; RETIRED is a sink, not assignable.
_ACTIVE_CHANNELS = (
    Channel.STABLE,
    Channel.CANDIDATE,
    Channel.SHADOW,
    Channel.CANARY,
)

CHANNELS_FILENAME = "channels.json"
CANARY_FILENAME = "canary.json"

#: Canary routing: ~1-in-20 real runs go to the canary channel
#: (deterministic by run_id hash, no RNG).
CANARY_RATE = 0.05

#: Runs a canary must stay green over before it may advance to stable.
CANARY_ADVANCE_RUNS = 20


@dataclass(frozen=True)
class ChannelEvent:
    """One persisted channel transition (metadata-only)."""

    ts: str  # ISO 8601
    action: str  # "activate" | "rollback" | "retire"
    channel: str
    agent_version_id: str
    detail: str = ""


#: Audit sink: (event_kind, metadata-only data) -> None. Best-effort.
AuditSink = Callable[[str, dict[str, Any]], None]


@dataclass(frozen=True)
class CanaryStatus:
    """Green-run accounting for the active canary (Phase 18.3)."""

    agent_version_id: str | None
    runs: int
    green: int
    failures: int
    eligible_to_advance: bool = False


def select_canary_run(run_id: str, *, rate: float = CANARY_RATE) -> bool:
    """Deterministic canary selection: ~1-in-20 runs route to the canary
    channel, keyed on the run id hash (reproducible, no RNG)."""
    digest = hashlib.sha256(run_id.encode()).hexdigest()[:8]
    return int(digest, 16) / 0xFFFFFFFF < rate


def _empty_state() -> dict[str, Any]:
    return {
        "channels": {str(c): None for c in _ACTIVE_CHANNELS},
        "stable_stack": [],  # prior stable ids, most recent last
        "retired": [],
        "history": [],
        "canary_outcomes": [],  # per-run canary evidence (B7.1)
    }


class ChannelManager:
    """Persistent channel assignments + rollback stack + history."""

    def __init__(
        self,
        state_dir: Path | str,
        *,
        audit: AuditSink | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._dir = Path(state_dir)
        self._audit = audit
        self._clock = clock or (lambda: datetime.now(UTC))
        self._state = self._load()

    @property
    def state_dir(self) -> Path:
        """The state directory this manager persists into."""
        return self._dir

    # -- persistence ---------------------------------------------------------

    @property
    def _path(self) -> Path:
        return self._dir / CHANNELS_FILENAME

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self._path.read_text())
        except Exception:
            return _empty_state()
        state = _empty_state()
        if isinstance(data, dict):
            for key, default in state.items():
                value = data.get(key)
                if isinstance(value, type(default)):
                    state[key] = value
        return state

    def _save(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._state, indent=2, sort_keys=True) + "\n")
        tmp.replace(self._path)

    def _emit_audit(self, kind: str, data: dict[str, Any]) -> None:
        """Best-effort telemetry: never raises, metadata-only."""
        if self._audit is None:
            return
        try:
            self._audit(kind, data)
        except Exception:
            log.exception("channel audit sink failed (best-effort, continuing)")

    def _record(self, action: str, channel: str, agent_version_id: str, detail: str = "") -> None:
        event = ChannelEvent(
            ts=self._clock().isoformat(),
            action=action,
            channel=channel,
            agent_version_id=agent_version_id,
            detail=detail,
        )
        self._state["history"].append(
            {
                "ts": event.ts,
                "action": event.action,
                "channel": event.channel,
                "agent_version_id": event.agent_version_id,
                "detail": event.detail,
            }
        )

    # -- queries ---------------------------------------------------------------

    def current(self, channel: Channel | str) -> str | None:
        """The agent version currently assigned to ``channel`` (or None)."""
        return self._state["channels"].get(str(channel))

    def retired(self) -> tuple[str, ...]:
        return tuple(self._state["retired"])

    def history(self) -> list[ChannelEvent]:
        return [
            ChannelEvent(
                ts=str(e.get("ts", "")),
                action=str(e.get("action", "")),
                channel=str(e.get("channel", "")),
                agent_version_id=str(e.get("agent_version_id", "")),
                detail=str(e.get("detail", "")),
            )
            for e in self._state["history"]
        ]

    # -- transitions -----------------------------------------------------------

    def activate(self, channel: Channel | str, agent_version_id: str) -> None:
        """Assign ``agent_version_id`` to ``channel``.

        Activating STABLE pushes the previous stable id onto the rollback
        stack, so :meth:`rollback` always restores the exact previous version.
        RETIRED is a sink: use :meth:`retire_candidate` instead.
        """
        ch = Channel(str(channel))
        if ch is Channel.RETIRED:
            raise ValueError("retired is a sink, not an assignable channel")
        previous = self._state["channels"][str(ch)]
        if ch is Channel.STABLE and previous and previous != agent_version_id:
            self._state["stable_stack"].append(previous)
        self._state["channels"][str(ch)] = agent_version_id
        self._record("activate", str(ch), agent_version_id)
        self._save()

    def rollback(self) -> str | None:
        """Restore the EXACT previous stable version. Returns it, or None
        when there is nothing to roll back to."""
        stack = self._state["stable_stack"]
        if not stack:
            return None
        current = self._state["channels"][str(Channel.STABLE)]
        previous = stack.pop()
        self._state["channels"][str(Channel.STABLE)] = previous
        self._record("rollback", str(Channel.STABLE), previous, detail=f"from {current}")
        self._save()
        return previous

    def retire_candidate(self, reason: str) -> str | None:
        """Move the current candidate to ``retired`` immediately.

        Emits a best-effort audit event. Returns the retired id (None when no
        candidate was active).
        """
        return self.retire_channel(Channel.CANDIDATE, reason)

    def retire_channel(self, channel: Channel | str, reason: str) -> str | None:
        """Move the current assignment of ``channel`` to ``retired``
        immediately, recording why. Returns the retired id (None when the
        channel was empty)."""
        ch = Channel(str(channel))
        if ch in (Channel.STABLE, Channel.RETIRED):
            raise ValueError(f"cannot retire the {ch} channel")
        current = self._state["channels"][str(ch)]
        if current is None:
            return None
        self._state["channels"][str(ch)] = None
        if current not in self._state["retired"]:
            self._state["retired"].append(current)
        self._record("retire", str(ch), current, detail=reason)
        self._save()
        self._emit_audit(
            "candidate_retired",
            {"agent_version_id": current, "channel": str(ch), "reason": reason},
        )
        return current

    # -- canary (Phase 18.3) ----------------------------------------------------

    def record_canary_outcome(self, run_id: str, code: str, detail: str = "") -> None:
        """Accrue one canary run's outcome as DISTINCT promotion evidence
        (separate from dev/held-out eval scorecards — B8 consumes this)."""
        agent = self._state["channels"][str(Channel.CANARY)]
        self._state["canary_outcomes"].append(
            {
                "run_id": run_id,
                "agent_version_id": agent,
                "code": code,
                "detail": detail[:200],
                "ts": self._clock().isoformat(),
            }
        )
        self._save()

    def canary_status(self) -> CanaryStatus:
        """Green-run accounting for the active canary: how many runs, how
        green, and whether it may advance to stable."""
        outcomes = self._state["canary_outcomes"]
        agent = self._state["channels"][str(Channel.CANARY)]
        mine = [o for o in outcomes if o.get("agent_version_id") == agent]
        green = sum(1 for o in mine if o.get("code") == "COMPLETED")
        failures = len(mine) - green
        return CanaryStatus(
            agent_version_id=agent,
            runs=len(mine),
            green=green,
            failures=failures,
            eligible_to_advance=bool(agent and len(mine) >= CANARY_ADVANCE_RUNS and failures == 0),
        )


# -- shadow runs -----------------------------------------------------------------


@dataclass(frozen=True)
class ShadowReport:
    """Result of one shadow run. ``merged`` is ALWAYS False: candidate output
    is scored, never merged into the main worktree."""

    task: str
    stable_summary: str
    candidate_summary: str
    candidate_score: float
    candidate_worktree: str
    merged: bool = False


#: A shadow backend is any callable (task, cwd) -> result, or an object with
#: a sync ``run(task, cwd)`` method. Results are coerced to a short metadata
#: summary string.
ShadowBackend = Callable[[str, Path], Any]


def _run_backend(backend: Any, task: str, cwd: Path) -> Any:
    fn = getattr(backend, "run", backend)
    return fn(task, cwd)


def _summarize(result: Any, limit: int = 200) -> str:
    text = getattr(result, "summary", result)
    return str(text)[:limit]


def _isolate_worktree(worktree: Path, sandbox: Path) -> Path:
    """Create an isolated candidate worktree inside ``sandbox``.

    A real ``git worktree add`` when the tree is a git repo and git is
    available; otherwise a plain copy (``.git`` excluded). Either way the
    main worktree is never written by the candidate.
    """
    dest = sandbox / "candidate-worktree"
    git_dir = worktree / ".git"
    if git_dir.exists() and shutil.which("git"):
        try:
            subprocess.run(
                ["git", "-C", str(worktree), "worktree", "add", "--detach", str(dest)],
                check=True,
                capture_output=True,
                text=True,
            )
            return dest
        except (OSError, subprocess.CalledProcessError):
            log.warning("git worktree add failed; falling back to a copy", exc_info=True)
    shutil.copytree(worktree, dest, ignore=shutil.ignore_patterns(".git"))
    return dest


def shadow_run(
    task: str,
    stable_backend: Any,
    candidate_backend: Any,
    worktree: Path | str,
    *,
    scorer: Callable[[Any], float] | None = None,
) -> ShadowReport:
    """Run ``task`` with the stable backend for real; replay it with the
    candidate in an isolated worktree; score the candidate output; merge
    NOTHING.

    The stable channel assignment and the stable backend's configuration are
    never touched: this function performs no channel I/O at all. Candidate
    backend errors degrade to score 0.0 (metadata-only summary); stable
    backend errors propagate — stable is production.
    """
    worktree = Path(worktree)
    stable_result = _run_backend(stable_backend, task, worktree)

    sandbox = Path(tempfile.mkdtemp(prefix="pxx-shadow-"))
    candidate_dir = _isolate_worktree(worktree, sandbox)
    try:
        candidate_result = _run_backend(candidate_backend, task, candidate_dir)
        candidate_summary = _summarize(candidate_result)
        score = float(scorer(candidate_result)) if scorer is not None else 1.0
    except Exception as exc:  # candidate failure must never gate production
        log.exception("candidate shadow run failed (score 0.0)")
        candidate_summary = f"{type(exc).__name__}"
        score = 0.0

    return ShadowReport(
        task=task,
        stable_summary=_summarize(stable_result),
        candidate_summary=candidate_summary,
        candidate_score=score,
        candidate_worktree=str(candidate_dir),
        merged=False,
    )


# -- circuit breakers --------------------------------------------------------------


class Breaker(StrEnum):
    SCOPE_VIOLATION = "scope_violation"
    EVALUATOR_CRITICAL_FAILURE = "evaluator_critical_failure"
    BUDGET_OVERRUN = "budget_overrun"
    UNEXPECTED_FILES = "unexpected_files"
    APPROVAL_RATE_DROP = "approval_rate_drop"
    HUMAN_CORRECTION_SPIKE = "human_correction_spike"
    REVIEWER_AVAILABILITY_DROP = "reviewer_availability_drop"


#: Thresholds for the signal-derived breakers (Phase 18.4).
APPROVAL_DROP_MAX_DELTA = 0.2  # approval rate fell > 0.2 below baseline
CORRECTION_SPIKE_THRESHOLD = 3  # human overrides/reverts in one candidate run
MIN_REVIEWER_AVAILABILITY = 0.5  # review layer degraded below this


@dataclass(frozen=True)
class CandidateRunSignals:
    """Per-candidate-run observations fed to the circuit breakers."""

    scope_violation: bool = False
    evaluator_critical_failure: bool = False
    budget_overrun: bool = False
    unexpected_files: tuple[str, ...] = ()
    approval_rate: float | None = None  # reviewer/human approval rate seen
    baseline_approval_rate: float | None = None
    human_corrections: int = 0  # human overrides/reverts this run
    reviewer_availability: float | None = None


def tripped_breakers(signals: CandidateRunSignals) -> tuple[Breaker, ...]:
    """Pure: which breakers fire for these signals (deterministic order)."""
    tripped: list[Breaker] = []
    if signals.scope_violation:
        tripped.append(Breaker.SCOPE_VIOLATION)
    if signals.evaluator_critical_failure:
        tripped.append(Breaker.EVALUATOR_CRITICAL_FAILURE)
    if signals.budget_overrun:
        tripped.append(Breaker.BUDGET_OVERRUN)
    if signals.unexpected_files:
        tripped.append(Breaker.UNEXPECTED_FILES)
    if (
        signals.approval_rate is not None
        and signals.baseline_approval_rate is not None
        and signals.approval_rate < signals.baseline_approval_rate - APPROVAL_DROP_MAX_DELTA
    ):
        tripped.append(Breaker.APPROVAL_RATE_DROP)
    if signals.human_corrections >= CORRECTION_SPIKE_THRESHOLD:
        tripped.append(Breaker.HUMAN_CORRECTION_SPIKE)
    if (
        signals.reviewer_availability is not None
        and signals.reviewer_availability < MIN_REVIEWER_AVAILABILITY
    ):
        tripped.append(Breaker.REVIEWER_AVAILABILITY_DROP)
    return tuple(tripped)


def evaluate_candidate_run(
    manager: ChannelManager,
    signals: CandidateRunSignals,
    *,
    channel: Channel | str = Channel.CANDIDATE,
) -> tuple[Breaker, ...]:
    """Evaluate circuit breakers for one candidate (or canary) run.

    Any tripped breaker disables the channel's assignment to ``retired``
    IMMEDIATELY (with a best-effort audit event). Returns the tripped
    breakers."""
    tripped = tripped_breakers(signals)
    if tripped:
        reason = "circuit breaker: " + ", ".join(str(b) for b in tripped)
        manager.retire_channel(channel, reason)
    return tripped
