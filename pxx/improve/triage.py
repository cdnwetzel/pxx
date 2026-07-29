"""Human triage of the cycle's proposal inbox (`pxx improve triage`).

The cycle routes proposals it cannot auto-derive into
``inbox/human-review-required/``. These helpers record the human verdict
by moving the entry to ``qualified/`` or ``rejected/`` with a
``disposition`` block (who, when, verdict). A dispositioned signature is
durable: the cycle consults these records (:func:`pxx.improve.cycle.
human_disposition`) and never re-proposes it.

Qualifying a non-derivable proposal records approval to pursue it — the
candidate itself still needs human authoring (``pxx propose``); nothing
here creates candidates or promotes anything.
"""

from __future__ import annotations

import getpass
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .cycle import INBOX_HUMAN, INBOX_QUALIFIED, INBOX_REJECTED

_SLUG_RE = re.compile(r"[0-9a-f]{12}")  # sha256[:12] — the only inbox filename shape


def _box(state_dir: Path | str, box: str) -> Path:
    return Path(state_dir) / "inbox" / box


def pending(state_dir: Path | str) -> list[dict[str, Any]]:
    """Entries awaiting human review in slug order, each with its slug.

    An unreadable entry is listed with an ``error`` field, never silently
    dropped — a corrupt inbox record needs a human, and this listing is
    exactly where the human is looking.
    """
    out: list[dict[str, Any]] = []
    for path in sorted(_box(state_dir, INBOX_HUMAN).glob("*.json")):
        try:
            entry = json.loads(path.read_text())
            if not isinstance(entry, dict):
                raise ValueError("entry is not a JSON object")
        except (OSError, ValueError) as exc:
            entry = {"error": f"unreadable entry: {exc}"}
        entry["slug"] = path.stem
        out.append(entry)
    return out


def dispose(
    state_dir: Path | str,
    slug: str,
    *,
    qualify: bool,
    note: str = "",
    by: str | None = None,
) -> dict[str, Any]:
    """Record the human verdict for a pending proposal; returns the record.

    Rejections require a note — the rationale is the record's value (the
    next reviewer, or the next you, needs to know why). Raises KeyError
    for an unknown slug and ValueError for a noteless rejection or a
    corrupt pending entry.
    """
    if not _SLUG_RE.fullmatch(slug):
        # slug becomes a filename in read/write/delete paths — anything but
        # the 12-hex inbox shape (e.g. ../ traversal) is rejected outright
        raise ValueError(f"invalid slug {slug!r}: expected 12 hex chars (see: triage list)")
    note = note.strip()
    if not qualify and not note:
        raise ValueError("--note is required to reject: record why, for the next reviewer")
    src = _box(state_dir, INBOX_HUMAN) / f"{slug}.json"
    if not src.is_file():
        raise KeyError(f"no proposal awaiting review with slug {slug!r}")
    entry = json.loads(src.read_text())
    if not isinstance(entry, dict):
        raise ValueError(f"{src}: pending entry is not a JSON object")
    if note:
        entry["reason"] = note
    entry["disposition"] = {
        "decided": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "decided_by": by or getpass.getuser(),
        "moved_from": INBOX_HUMAN,
        "verdict": "qualified" if qualify else "rejected",
    }
    dest = _box(state_dir, INBOX_QUALIFIED if qualify else INBOX_REJECTED)
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"{slug}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(entry, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)
    src.unlink()
    return entry
