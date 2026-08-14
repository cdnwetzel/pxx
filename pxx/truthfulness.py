"""Content-truthfulness checks — a deterministic axis SEPARATE from permission.

Scope/permission (R-014) governs what the agent may *touch*; it does not catch a model
that stays fully in-scope and still reports something *false* about the code (quoting a
comment that isn't there, claiming code it doesn't have). The objective gates
(lint/tests/diff-cap) catch broken edits, not confident-but-wrong claims.

First increment: **quote grounding**. Every non-trivial code span the model quotes in its
narration must appear in content it actually read or wrote (read tool-results plus the diff).
An ungrounded quote is a fabrication. Deterministic, no model, so it is trivially
unit-testable with a negative control (a fabricated quote MUST be flagged; a real one MUST
pass — a check that cannot go red is not a check).

This module is the pure checker; wiring it into the loop as an advisory gate (fp-rate
measured before it can block) is a separate step.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Fenced ```...``` blocks (any/no info string, incl. spaces like ```python hl_lines=1; CRLF
# tolerated) and inline `...` spans.
_FENCE_RE = re.compile(r"```[^\n]*\r?\n(.*?)```", re.DOTALL)
_INLINE_RE = re.compile(r"`([^`\n]+)`")
_WS_RE = re.compile(r"\s+")

#: Spans shorter than this are ignored (filenames, flags, a lone identifier): too small to
#: be a meaningful "quote", and short spans coincidentally match too often to be evidence.
_MIN_QUOTE_LEN = 12
#: An inline span is only checked if it looks like *code* (has code punctuation/keywords),
#: so prose backticks like `foo.py` or `--flag` are not treated as quotes.
_CODE_SIGNAL_RE = re.compile(
    r"[(){}\[\];=]|->|=>|::|\bdef\b|\bclass\b|\breturn\b|\bimport\b|\bfunction\b"
)
#: Fenced blocks are checked LINE-BY-LINE, not as one whole-block substring: a model that
#: quotes a function but elides its docstring is being terse, not fabricating. So a fenced
#: quote is grounded when each of its substantive lines appears in the sources; a single
#: fabricated line (a comment that isn't there, invented code) is what gets flagged. Lines
#: that are blank or pure structure (``}``, ``):``) are skipped: too common to be evidence.
_MIN_LINE_LEN = 4
_PUNCT_ONLY_RE = re.compile(r"^[\s(){}\[\];:,.]*$")


@dataclass(frozen=True)
class TruthfulnessFinding:
    """A code span the model quoted that is grounded in neither read nor written content."""

    quote: str  # the ungrounded span (trimmed for display)
    kind: str  # "fenced" | "inline"


def _normalize(text: str) -> str:
    """Whitespace-insensitive form: collapse all whitespace runs to single spaces.

    So a quote that differs from the source only in indentation/reflow still matches
    (avoids false positives from cosmetic whitespace)."""
    return _WS_RE.sub(" ", text).strip()


def _looks_like_code(span: str) -> bool:
    s = span.strip()
    if len(s) < _MIN_QUOTE_LEN:
        return False
    if s.startswith("-"):  # a CLI flag / option, not a code quote
        return False
    return bool(_CODE_SIGNAL_RE.search(s))


def _substantive_lines(block: str) -> list[tuple[str, str]]:
    """(display, normalized) for each fenced line worth grounding — skip blank / pure-structure
    lines (too common to be evidence of anything)."""
    out: list[tuple[str, str]] = []
    for line in block.splitlines():
        norm = _normalize(line)
        if len(norm) >= _MIN_LINE_LEN and not _PUNCT_ONLY_RE.match(norm):
            out.append((line.strip(), norm))
    return out


def check_quote_grounding(text: str, sources: list[str]) -> list[TruthfulnessFinding]:
    """Flag code the model quotes in ``text`` that appears in none of ``sources``.

    ``sources`` = the content the agent actually read (read/search tool results) plus what it
    wrote (the diff / post-edit content) — everything a truthful quote could legitimately come
    from. A quote grounded in none of them is presented as real code that exists nowhere the
    agent saw or produced: a content-truthfulness violation, independent of permission/scope.

    Fenced blocks are checked line-by-line (one finding per block, keyed to its first
    ungrounded line) so elision/reflow don't false-positive; inline code spans are checked
    whole. Pure and deterministic (whitespace-normalized, no model). Empty list == all grounded.
    """
    if not text:
        return []
    # Normalize each source SEPARATELY (not one joined blob): a quote is grounded only if some
    # single source contains it whole. Joining would let a quote whose first half ends source A
    # and whose second half starts source B pass, though no source ever held the quoted code.
    grounded_sources = [g for g in (_normalize(s) for s in sources if s) if g]

    def _is_grounded(norm: str) -> bool:
        return any(norm in g for g in grounded_sources)

    findings: list[TruthfulnessFinding] = []
    seen: set[str] = set()
    for m in _FENCE_RE.finditer(text):
        for display, norm in _substantive_lines(m.group(1)):
            if not _is_grounded(norm):
                if norm not in seen:
                    seen.add(norm)
                    findings.append(TruthfulnessFinding(quote=display[:200], kind="fenced"))
                break  # one finding per block, anchored to its first ungrounded line
    for m in _INLINE_RE.finditer(text):
        span = m.group(1)
        if _looks_like_code(span):
            norm = _normalize(span)
            if norm and not _is_grounded(norm) and norm not in seen:
                seen.add(norm)
                findings.append(TruthfulnessFinding(quote=span.strip()[:200], kind="inline"))
    return findings
