"""PASS receipt — the recorded evidence that the mechanism ran (and can fail).

Mirrors the schema in ``docs/context-paging-prototype.md``: per-capsule token accounting, the
action trace, the four negative-control flags, the host verification verdict, and the terminal
code. Written to disk so a run leaves proof, not a claim.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Receipt:
    model_id: str = ""
    tokenizer_id: str = ""
    task: str = ""
    capsules: list[dict] = field(default_factory=list)
    actions: list[dict] = field(default_factory=list)
    negative_controls: dict = field(default_factory=dict)
    verification: dict = field(default_factory=dict)
    terminal: str = ""

    def record_capsule(
        self, seq: int, input_tokens: int, cap: int, under_cap: bool, evicted: list[str]
    ) -> None:
        self.capsules.append(
            {
                "action_seq": seq,
                "input_tokens": input_tokens,
                "cap": cap,
                "under_cap": under_cap,
                "evicted": evicted,
            }
        )

    def record_action(self, seq: int, action_type: str, detail: dict | None) -> None:
        row = {"seq": seq, "type": action_type}
        if detail:
            row.update(detail)
        self.actions.append(row)

    def to_dict(self) -> dict:
        return asdict(self)

    def save(self, state_dir: Path) -> Path | None:
        """Best-effort: a failed receipt write is logged, never fatal — the run's terminal state
        is already decided by the time this is called, so an audit-write error must not raise."""
        path = Path(state_dir) / "receipt.json"
        try:
            path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True))
            return path
        except OSError:
            logging.getLogger("context_paging").warning(
                "receipt write failed (non-fatal): %s", path
            )
            return None
