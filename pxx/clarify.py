"""Phase 14: the ambiguity / clarification gate.

Deterministic ``ready_to_act`` check run BEFORE the first backend round: a
task that is empty, references a file that does not exist, or implies tests
without a configured test command stops with a question instead of burning
an autonomous run on a guess. Fail-safe by construction: the gate only
fires on POSITIVE ambiguity signals; anything it can't classify proceeds
(uncertain analysis never blocks a clear task).

The missing-file signal is *governed*, not global: it fires only when an edit
verb is the nearest cue to a specific path within its clause. A path introduced
by a creation/description cue ("emits ``out.json``", "such as ``build/x.json``",
"a new ``foo.py``") is not treated as an edit target, so a task that merely
*describes* a generated/runtime artifact no longer false-blocks (F-2 / R-014).
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

#: Cues that a nearby path is NOT an edit target — creation intent, or a
#: described/generated/example artifact. When one of these governs a path more
#: closely than an edit verb, a missing file is not ambiguity: the task is
#: making it, or merely naming an output. This closes the F-2 false positive
#: (R-014) where an edit-verb task that only *describes* a generated artifact
#: (e.g. "improve the detector so it emits ``prose-tool-call.json``") was gated
#: on that never-meant-to-be-edited path.
#: Kept deliberately tight: creation/generation VERBS (incl. past participles
#: like "generated"/"written" that describe an emitted artifact) plus explicit
#: exemplifiers. Generic nouns/adjectives that also sit near genuine edit targets
#: ("runtime crash", "the file called foo.py", "the bug named …") are excluded —
#: they would false-SUPPRESS a real missing-file ambiguity.
_NON_EDIT_TARGET_CUE = re.compile(
    r"(?:\b(?:"
    r"creat(?:e|es|ing|ed)|add(?:s|ing|ed)?|generat(?:e|es|ing|ed)|"
    r"produc(?:e|es|ing|ed)|scaffold(?:s|ing|ed)?|introduc(?:e|es|ing|ed)|"
    r"implement(?:s|ing|ed)?|emit(?:s|ting|ted)?|output(?:s|ting|ted)?|"
    r"writ(?:e|es|ing|ten)|new|such as|for example"
    r")\b)|(?:e\.g\.)",
    re.IGNORECASE,
)

#: Sentence/clause terminators that stop a governing cue from binding across
#: them — a cue in a prior sentence does not govern this path.
_CLAUSE_BREAK = re.compile(r"[.!?;:\n]")

#: Repo-relative-looking file references.
_PATH_RE = re.compile(
    r"\b([\w][\w./-]*\.(?:py|md|toml|yaml|yml|json|js|ts|tsx|go|rs|c|h|cc|cpp|"
    r"sh|sql|txt|cfg|ini))\b"
)


def _last_start(rx: re.Pattern[str], s: str) -> int | None:
    """Start index of the LAST (nearest-to-end) match of ``rx`` in ``s``, or None."""
    last: int | None = None
    for m in rx.finditer(s):
        last = m.start()
    return last


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
    for match in _PATH_RE.finditer(text):
        rel = match.group(1)
        # Untrusted task text: skip absolutes, URLs, and any path with a ".."
        # segment (e.g. "a/../../outside.py") so a probe can't escape cwd.
        if rel.startswith("/") or "://" in rel or ".." in rel.split("/"):
            continue
        # Governance: only gate when an edit verb is the NEAREST cue governing
        # this specific path within its own clause. A creation/description cue
        # sitting nearer the path (or the absence of any edit verb) means the
        # path is not an edit target — a missing file is then not ambiguity.
        before = text[: match.start()]
        brk = _last_start(_CLAUSE_BREAK, before)
        window = before[brk + 1 :] if brk is not None else before
        edit_at = _last_start(_EXISTING_FILE_VERBS, window)
        if edit_at is None:
            continue  # no edit verb governs this path
        suppress_at = _last_start(_NON_EDIT_TARGET_CUE, window)
        if suppress_at is not None and suppress_at > edit_at:
            continue  # a creation/description cue governs the path more closely
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
