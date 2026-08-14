"""Artifact store — full logs on disk, a bounded + secret-scrubbed summary to the model.

Test/terminal output can run to megabytes and can contain credentials. The model never sees
the full log: it gets a bounded, secret-scrubbed **summary** plus a **reference ID** it can
INSPECT. The full artifact stays host-side on disk. Scrubbing happens before the summary is
ever handed to the capsule, so credentials never reach the model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .pages import page_hash

# Credential-ish patterns scrubbed from any summary before the model sees it. Deliberately
# broad (fail toward redaction): URL creds, bearer/authorization headers, common token shapes,
# and key=value assignments for secret-named keys.
_SCRUBBERS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(https?://)[^/@\s]+@"), r"\1***@"),
    (re.compile(r"(?i)\b(authorization|bearer)\b\s*:?\s*\S+"), r"\1 ***"),
    (re.compile(r"(?i)\b(api[_-]?key|secret|token|password|passwd|pwd)\b\s*[=:]\s*\S+"), r"\1=***"),
    (re.compile(r"\b(sk|xoxb|ghp|gho|ghs|AKIA)[-_A-Za-z0-9]{12,}\b"), "***"),
]

_SUMMARY_HEAD = 1200
_SUMMARY_TAIL = 1200


def scrub_secrets(text: str) -> str:
    for pat, repl in _SCRUBBERS:
        text = pat.sub(repl, text)
    return text


@dataclass(frozen=True)
class ArtifactRef:
    """What the model gets: a stable id, a bounded scrubbed summary, and the byte length."""

    ref_id: str
    summary: str
    total_bytes: int


class ArtifactStore:
    """Persists full logs on disk; hands out bounded, scrubbed summaries by reference."""

    def __init__(self, state_dir: Path) -> None:
        self.dir = Path(state_dir) / "artifacts"
        self.dir.mkdir(parents=True, exist_ok=True)

    def put(self, kind: str, content: str) -> ArtifactRef:
        """Store the full (scrubbed) log; return a bounded scrubbed summary + a ref id.

        The stored artifact is ALSO scrubbed — the on-disk copy is host-side but the sandbox is
        not a place to leave plaintext credentials either.
        """
        scrubbed = scrub_secrets(content)
        ref_id = f"{kind}-{page_hash(scrubbed.encode())[:12]}"
        (self.dir / f"{ref_id}.log").write_text(scrubbed)
        return ArtifactRef(
            ref_id=ref_id, summary=self._summarize(scrubbed), total_bytes=len(content)
        )

    def get(self, ref_id: str) -> str | None:
        path = self.dir / f"{ref_id}.log"
        return path.read_text() if path.is_file() else None

    @staticmethod
    def _summarize(scrubbed: str) -> str:
        if len(scrubbed) <= _SUMMARY_HEAD + _SUMMARY_TAIL:
            return scrubbed
        head = scrubbed[:_SUMMARY_HEAD]
        tail = scrubbed[-_SUMMARY_TAIL:]
        elided = len(scrubbed) - _SUMMARY_HEAD - _SUMMARY_TAIL
        return f"{head}\n... [{elided} bytes elided — INSPECT the ref for more] ...\n{tail}"
