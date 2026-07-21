"""Phase 14: the ambiguity / clarification gate.

Deterministic ``ready_to_act`` check run BEFORE the first backend round: a
task that is empty, references a file that does not exist, or implies tests
without a configured test command stops with a question instead of burning
an autonomous run on a guess. Fail-safe by construction: the gate only
fires on POSITIVE ambiguity signals; anything it can't classify proceeds
(uncertain analysis never blocks a clear task).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

log = logging.getLogger("pxx.clarify")


class ReadyState(StrEnum):
    READY_TO_EXECUTE = "READY_TO_EXECUTE"
    QUESTION_REQUIRED = "QUESTION_REQUIRED"
    INSUFFICIENT_CONTEXT = "INSUFFICIENT_CONTEXT"


@dataclass(frozen=True)
class ReadyDecision:
    """The gate's verdict; ``question`` is the text to surface when gating."""

    state: ReadyState
    question: str = ""


#: Verbs that imply the referenced file already exists (vs. creating it).
_EXISTING_FILE_VERBS = re.compile(
    r"\b(fix|update|edit|modify|refactor|debug|review|explain|analy[sz]e|"
    r"read|change|patch|repair|improve|document|test)\b",
    re.IGNORECASE,
)

#: Repo-relative-looking file references.
_PATH_RE = re.compile(
    r"\b([\w][\w./-]*\.(?:py|md|toml|yaml|yml|json|js|ts|tsx|go|rs|c|h|cc|cpp|"
    r"sh|sql|txt|cfg|ini))\b"
)

#: Task phrasings that imply running a test suite.
_TEST_INTENT_RE = re.compile(
    r"\b(make (the )?tests? pass|fix (the )?(failing )?tests?|failing tests?|"
    r"tests? (are|is) failing|test suite (is )?(red|failing))\b",
    re.IGNORECASE,
)


def ready_to_act(task: str, *, cwd: Path, test_command: str | None) -> ReadyDecision:
    """Decide whether ``task`` is specified well enough to act on.

    Pure and deterministic. Gates only on positive ambiguity signals:
    empty task, test intent without a test command, or an edit-implying
    verb attached to a file that does not exist under ``cwd``.
    """
    text = (task or "").strip()
    if not text:
        return ReadyDecision(
            ReadyState.QUESTION_REQUIRED,
            "The task is empty — what would you like me to do?",
        )
    if _TEST_INTENT_RE.search(text) and not test_command:
        return ReadyDecision(
            ReadyState.QUESTION_REQUIRED,
            "This task involves tests, but no test command is configured "
            "(settings.test_command). Which command should verify the fix?",
        )
    if _EXISTING_FILE_VERBS.search(text):
        for match in _PATH_RE.finditer(text):
            rel = match.group(1)
            if rel.startswith(("/", "../")) or "://" in rel:
                continue
            try:
                exists = (cwd / rel).exists()
            except OSError:
                exists = True  # unreadable fs state is not ambiguity evidence
            if not exists:
                return ReadyDecision(
                    ReadyState.INSUFFICIENT_CONTEXT,
                    f"The task references '{rel}', which does not exist under "
                    f"{cwd}. Which file did you mean?",
                )
    return ReadyDecision(ReadyState.READY_TO_EXECUTE)


__all__ = ["ReadyDecision", "ReadyState", "ready_to_act"]
