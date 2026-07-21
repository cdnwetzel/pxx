"""Typed event stream + hash-chained audit log.

Every model/tool/gate event in a session flows through :class:`EventBus`.
The :class:`AuditLog` subscriber persists events as hash-chained JSONL —
tamper-evident, append-only, and **metadata-only**: no prompt bodies, no file
contents, no diffs, no secrets. Audit is best-effort telemetry: failures are
swallowed (logged to stderr) and never gate a run.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("pxx.events")

#: Event kinds emitted by the runtime.
EVENT_KINDS = frozenset(
    {
        "run_created",
        "session_start",
        "model_request",
        "model_response",
        "prompt_rendered",
        "tool_call",
        "tool_result",
        "tool_action_proposed",
        "policy_decision",
        "file_changed",
        "gate_decision",
        "observation",
        "checkpoint_created",
        "run_paused",
        "resumed",
        "evaluation_completed",
        "budget",
        "error",
        "session_end",
    }
)

_CRED_RE = re.compile(r"(https?://)[^/@\s]+@")


def scrub(value: Any) -> Any:
    """Strip credentials from URLs in arbitrary JSON-ish data."""
    if isinstance(value, str):
        return _CRED_RE.sub(r"\1***@", value)
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value]
    return value


@dataclass(frozen=True)
class Event:
    kind: str
    data: dict[str, Any]
    session_id: str
    ts: float = field(default_factory=time.time)
    seq: int = 0

    def to_json(self) -> str:
        return json.dumps(
            {
                "kind": self.kind,
                "data": scrub(self.data),
                "session_id": self.session_id,
                "ts": self.ts,
                "seq": self.seq,
            },
            sort_keys=True,
            default=str,
        )


Subscriber = Callable[[Event], Awaitable[None]]


class EventBus:
    """Async pub/sub. Subscriber errors are logged, never raised."""

    def __init__(self) -> None:
        self._subs: list[Subscriber] = []
        self._seq = 0
        self.history: list[Event] = []  # in-memory ring for the session

    def subscribe(self, fn: Subscriber) -> None:
        self._subs.append(fn)

    async def emit(self, kind: str, data: dict[str, Any], session_id: str = "") -> Event:
        if kind not in EVENT_KINDS:
            raise ValueError(f"unknown event kind: {kind}")
        self._seq += 1
        event = Event(kind=kind, data=data, session_id=session_id, seq=self._seq)
        self.history.append(event)
        for fn in self._subs:
            try:
                await fn(event)
            except Exception:  # telemetry must never gate a run
                log.exception("event subscriber failed for %s", kind)
        return event


class AuditLog:
    """Hash-chained JSONL audit sink.

    Each line carries ``prev_hash`` and ``hash`` where
    ``hash = sha256(prev_hash + canonical_event_json)``. A ``.head`` sidecar
    anchors the chain length + tip hash after every record so that TRAILING
    TRUNCATION (dropping the newest records) is detectable — the chain alone
    only proves prefix consistency. Verify with :meth:`verify`
    (``pxx audit verify``); an unanchored log fails closed.
    """

    GENESIS = "0" * 64

    def __init__(self, state_dir: Path, session_id: str) -> None:
        self._dir = Path(state_dir) / "audit"
        self._session_id = session_id
        self._prev = self.GENESIS
        self._count = 0
        self._path: Path | None = None

    @property
    def path(self) -> Path:
        if self._path is None:
            day = time.strftime("%Y-%m-%d")
            self._dir.mkdir(parents=True, exist_ok=True)
            self._path = self._dir / f"{day}.jsonl"
            self._resume_chain()
        return self._path

    def _resume_chain(self) -> None:
        """Resume from the day's last record; rotate away a corrupt tail.

        A partial/corrupt final line must NOT silently reseed the chain to
        GENESIS mid-file (that corrupts the chain forever). Fail loud and
        rotate the damaged file aside; a fresh chain starts in its place.
        """
        assert self._path is not None
        try:
            lines = [ln for ln in self._path.read_text().splitlines() if ln.strip()]
        except OSError:
            return
        if not lines:
            return
        try:
            last = json.loads(lines[-1])
            self._prev = last["hash"]
            self._count = len(lines)
        except Exception:
            rotated = self._path.with_name(f"{self._path.stem}.corrupt-{int(time.time())}.jsonl")
            log.warning(
                "audit: %s has an unparseable tail; rotating it to %s and "
                "starting a fresh chain (inspect the rotated file)",
                self._path,
                rotated,
            )
            try:
                self._path.rename(rotated)
            except OSError:
                log.exception("audit: could not rotate corrupt log %s", self._path)
            self._prev = self.GENESIS
            self._count = 0

    def _write_head(self) -> None:
        """Anchor the chain tip: {count, hash} sidecar for truncation checks."""
        assert self._path is not None
        head = {"count": self._count, "hash": self._prev}
        self._path.with_suffix(".head").write_text(json.dumps(head, sort_keys=True) + "\n")

    async def record(self, event: Event) -> None:
        try:
            path = self.path  # force lazy open/chain resume BEFORE hashing
            payload = event.to_json()
            digest = hashlib.sha256((self._prev + payload).encode()).hexdigest()
            line = json.dumps(
                {"event": json.loads(payload), "prev_hash": self._prev, "hash": digest},
                sort_keys=True,
            )
            with path.open("a") as fh:
                fh.write(line + "\n")
            self._prev = digest
            self._count += 1
            self._write_head()
        except Exception:
            log.exception("audit record failed (best-effort, continuing)")

    def subscribe_to(self, bus: EventBus) -> None:
        bus.subscribe(self.record)

    @staticmethod
    def verify(path: Path) -> bool:
        """Check chain integrity of an audit file. Returns True when valid.

        Fails closed on: interior tamper, reordering, unparseable lines,
        TRAILING TRUNCATION (the ``.head`` anchor must match the final
        record's count + hash), and missing anchors (a log without a head
        sidecar cannot prove it wasn't truncated).
        """
        prev = AuditLog.GENESIS
        count = 0
        try:
            for raw in Path(path).read_text().splitlines():
                if not raw.strip():
                    continue
                rec = json.loads(raw)
                if rec.get("prev_hash") != prev:
                    return False
                payload = json.dumps(rec["event"], sort_keys=True)
                if hashlib.sha256((prev + payload).encode()).hexdigest() != rec.get("hash"):
                    return False
                prev = rec["hash"]
                count += 1
        except Exception:
            return False
        try:
            head = json.loads(Path(path).with_suffix(".head").read_text())
        except Exception:
            return False  # no anchor: truncation would be undetectable
        return head.get("count") == count and head.get("hash") == prev
