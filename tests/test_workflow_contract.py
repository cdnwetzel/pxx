"""WORKFLOW.md is a CONTRACT — every machine-readable field is asserted
against the code it describes (roadmap 10.5: instruction validation).
A mismatch here means the contract drifted; fix whichever side is wrong,
never delete the assertion."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _contract() -> dict:
    text = (REPO / "WORKFLOW.md").read_text(encoding="utf-8")
    match = re.search(r"```toml\n(.*?)```", text, re.DOTALL)
    assert match, "WORKFLOW.md must contain a ```toml contract block"
    return tomllib.loads(match.group(1))


class TestWorkflowContract:
    def test_budgets_match_loop_constants(self):
        from pxx import loop

        budgets = _contract()["budgets"]
        assert budgets["max_rounds"] == loop.DEFAULT_MAX_ROUNDS
        assert budgets["max_seconds"] == loop.DEFAULT_MAX_SECONDS
        assert budgets["max_diff_lines"] == loop.DEFAULT_DIFF_BUDGET_LINES

    def test_states_match_workflow_module(self):
        from pxx import workflow

        states = _contract()["states"]
        # The phase vocabulary lives in WorkflowState's docstring comment;
        # transition() accepts these and resume_state() branches on them.
        source = Path(workflow.__file__).read_text(encoding="utf-8")
        for phase in states["phases"]:
            assert phase in source, f"phase {phase!r} not present in workflow.py"
        assert states["initial"] == workflow.WorkflowState().phase

    def test_commands_exist_and_match_gates(self):
        from pxx import loop

        commands = _contract()["commands"]
        loop_src = Path(loop.__file__).read_text(encoding="utf-8")
        # The loop's test gate runs exactly the contract's test command head.
        assert '"uv", "run", "pytest"' in loop_src
        assert commands["test"][:3] == ["uv", "run", "pytest"]
        assert commands["lint"][:3] == ["uv", "run", "ruff"]

    def test_protected_paths_exist_and_cover_trust_boundary(self):
        contract_paths = set(_contract()["permissions"]["protected_paths"])
        for p in contract_paths:
            assert (REPO / p).exists(), f"protected path missing from repo: {p}"
        boundary = (REPO / "docs" / "TRUST_BOUNDARY.md").read_text(encoding="utf-8")
        for p in (
            "pxx/safety.py",
            "pxx/scope.py",
            "pxx/governance.py",
            "pxx/review_gate.py",
            ".github/workflows/",
        ):
            assert p in contract_paths, f"{p} absent from WORKFLOW.md contract"
            assert p.rstrip("/").split("/")[-1] in boundary or p in boundary

    def test_terminal_codes_reference_is_accurate(self):
        from pxx import outcomes

        assert len(outcomes.FAILURE_CODES) == 19  # the count WORKFLOW.md cites

    def test_agents_md_links_resolve(self):
        text = (REPO / "AGENTS.md").read_text(encoding="utf-8")
        for target in re.findall(r"\]\(([^)#]+)\)", text):
            assert (REPO / target).exists(), f"AGENTS.md dead link: {target}"
