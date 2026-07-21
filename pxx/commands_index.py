"""Discover and describe the slash-command prompt fragments shipped with pxx.

Filesystem-driven: any `pxx/commands/*.md` file except the authoring template
is picked up automatically by :func:`list_commands`. The first markdown H1
heading in each file is used to derive a one-line description. The canonical
heading format is::

    # /<name> — <description>

with an em-dash, double-dash (``--``), or single-dash (``-``) separator. If
the heading is bare (``# /<name>``) or absent, the description defaults to
``(no description)``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

COMMANDS_DIR = Path(__file__).parent / "commands"
"""Default location of slash-command files — `pxx/commands/`."""

NO_DESCRIPTION = "(no description)"
EXCLUDED_FILENAMES = frozenset({"SKILL_TEMPLATE.md"})

# Match the first H1 heading anywhere in the document.
# `^# ` requires a single `#` followed by a space at the start of a line,
# so `##` and deeper headings do not match.
_H1_RE = re.compile(r"^# (.+?)\s*$", re.MULTILINE)

# Strip a canonical "/<name> <separator> " prefix from a heading, leaving
# just the description text. Separator is em-dash, double-dash, or
# single-dash, surrounded by whitespace.
_CONVENTION_RE = re.compile(r"^/\S+\s+[—-]+\s+(\S.*)$")

# Match a bare slash-name heading with no description (e.g. "/audit").
_BARE_SLASH_RE = re.compile(r"^/\S+\s*$")


@dataclass(frozen=True)
class CommandInfo:
    """One discovered slash command."""

    name: str
    path: Path
    description: str


def _extract_description(text: str) -> str:
    """Return a one-line description derived from the first H1 heading.

    See the module docstring for the canonical heading format. Returns
    :data:`NO_DESCRIPTION` if no usable description can be derived.
    """
    m = _H1_RE.search(text)
    if not m:
        return NO_DESCRIPTION
    heading = m.group(1).strip()

    convention = _CONVENTION_RE.match(heading)
    if convention:
        return convention.group(1).strip()

    if _BARE_SLASH_RE.match(heading):
        return NO_DESCRIPTION

    return heading


def list_commands(commands_dir: Path = COMMANDS_DIR) -> list[CommandInfo]:
    """Discover all `*.md` files in ``commands_dir`` and return their metadata.

    Results are sorted by ``name``. Returns an empty list if ``commands_dir``
    does not exist. Unreadable files are skipped silently rather than raising.
    """
    if not commands_dir.exists():
        return []

    out: list[CommandInfo] = []
    for path in sorted(commands_dir.glob("*.md")):
        if path.name in EXCLUDED_FILENAMES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        out.append(
            CommandInfo(
                name=path.stem,
                path=path.resolve(),
                description=_extract_description(text),
            )
        )
    return out
