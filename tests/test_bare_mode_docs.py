"""Template-validation tests for docs/bare-mode/.

Every shipped bare-mode prompt template is a contract artifact: it must carry
a complete provenance header (so a reader knows what was measured, against
what, and what is merely plausible), and every ```json fence in it must parse
(a template whose own example schema is malformed teaches the wrong shape).
No network, no model calls — these tests read the docs tree only.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs" / "bare-mode"
TEMPLATES = DOCS / "templates"

HEADER_FIELDS = ("purpose", "output contract", "validated against", "measured result", "status")
STATUS_LABELS = ("UNVALIDATED", "VALIDATED-AT-SOURCE", "VALIDATED")
JSON_FENCE = re.compile(r"```json\n(.*?)```", re.DOTALL)


def _templates() -> list[Path]:
    return sorted(TEMPLATES.glob("*.md"))


def _header(text: str, field: str) -> str | None:
    m = re.search(rf"^- \*\*{re.escape(field)}:\*\*\s*(.+)$", text, re.MULTILINE)
    return m.group(1).strip() if m else None


def test_templates_exist() -> None:
    assert _templates(), "docs/bare-mode/templates/ must ship at least one template"


def test_every_template_has_a_complete_header() -> None:
    for tpl in _templates():
        text = tpl.read_text()
        for field in HEADER_FIELDS:
            assert _header(text, field), f"{tpl.name}: missing header field '{field}'"


def test_status_uses_a_known_label() -> None:
    for tpl in _templates():
        status = _header(tpl.read_text(), "status") or ""
        assert any(label in status for label in STATUS_LABELS), (
            f"{tpl.name}: status must carry one of {STATUS_LABELS}, got: {status!r}"
        )


def test_json_fences_parse() -> None:
    for tpl in _templates():
        text = tpl.read_text()
        fences = JSON_FENCE.findall(text)
        assert fences, f"{tpl.name}: template must show its output contract as a json fence"
        for body in fences:
            json.loads(body)  # raises with position info on malformed JSON


def test_readme_links_every_template() -> None:
    readme = (DOCS / "README.md").read_text()
    for tpl in _templates():
        assert f"templates/{tpl.name}" in readme, (
            f"docs/bare-mode/README.md does not reference templates/{tpl.name}"
        )
