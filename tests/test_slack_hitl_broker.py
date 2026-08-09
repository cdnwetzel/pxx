"""Unit tests for the Slack HITL broker reference impl (docs/examples/hitl/).

The example is a reference implementation, not part of the pxx package, so we load
it by path. Its slack_sdk / fastapi imports are lazy (inside main()), so these pure
helpers import and test WITHOUT those dependencies installed.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_EXAMPLE = (
    Path(__file__).resolve().parent.parent / "docs" / "examples" / "hitl" / "slack_hitl_broker.py"
)


@pytest.fixture(scope="module")
def broker():
    spec = importlib.util.spec_from_file_location("slack_hitl_broker", _EXAMPLE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # must not require slack_sdk/fastapi
    return mod


def test_decision_for_action_maps_only_decision_buttons(broker):
    assert broker.decision_for_action("pxx_approve") == "approve"
    assert broker.decision_for_action("pxx_abort") == "abort"
    # modify is not a terminal decision here: it opens a modal, not writes a decision
    assert broker.decision_for_action("pxx_modify") is None
    assert broker.decision_for_action("nonsense") is None


def test_write_decision_is_single_use(broker, tmp_path):
    assert broker.write_decision(tmp_path, "abc123", "approve", "chris") is True
    # a second decision on the same nonce must be refused (single-use / O_EXCL)
    assert broker.write_decision(tmp_path, "abc123", "abort", "attacker") is False
    rec = json.loads((tmp_path / "abc123.decision").read_text())
    assert rec["decision"] == "approve" and rec["by"] == "chris"


def test_write_decision_rejects_bad_input(broker, tmp_path):
    assert broker.write_decision(tmp_path, "not alnum!", "approve", "x") is False
    assert broker.write_decision(tmp_path, "n1", "sideways", "x") is False
    assert not list(tmp_path.glob("*.decision"))  # nothing written


def test_approval_blocks_carry_nonce_and_actions(broker):
    blocks = broker.approval_blocks("nonce9", "do a thing")
    actions = next(b for b in blocks if b["type"] == "actions")["elements"]
    by_id = {e["action_id"]: e for e in actions}
    assert by_id["pxx_approve"]["value"] == "nonce9"
    assert by_id["pxx_abort"]["value"] == "nonce9"
    assert by_id["pxx_modify"]["value"] == "nonce9"  # modify button present (P2)
    assert by_id["pxx_approve"]["style"] == "primary"
    assert by_id["pxx_abort"]["style"] == "danger"
    # no origin given -> no origin line, first block is the summary (backward compatible)
    assert blocks[0]["type"] == "section"


def test_approval_blocks_origin_label(broker):
    blocks = broker.approval_blocks("n1", "do a thing", origin="openclaw")
    # origin renders as a distinct top context line carrying the source label
    assert blocks[0]["type"] == "context"
    assert "openclaw" in blocks[0]["elements"][0]["text"]
    # the buttons still carry the nonce regardless of origin
    actions = next(b for b in blocks if b["type"] == "actions")["elements"]
    assert {e["action_id"] for e in actions} == {"pxx_approve", "pxx_abort", "pxx_modify"}


def test_outcome_blocks_render_each_terminal(broker):
    for decision in ("approve", "abort", "modify", "timeout"):
        blocks = broker.outcome_blocks(decision, "U123")
        assert blocks and blocks[0]["type"] == "section"


# ---- P2: Modify modal ----


def test_write_decision_modify_carries_fields_single_use(broker, tmp_path):
    ok = broker.write_decision(
        tmp_path,
        "abc",
        "modify",
        "chris",
        extra={"scope": "mathlib.py", "note": "only add multiply"},
    )
    assert ok is True
    # still single-use even for modify
    assert broker.write_decision(tmp_path, "abc", "approve", "attacker") is False
    rec = json.loads((tmp_path / "abc.decision").read_text())
    assert rec["decision"] == "modify"
    assert rec["scope"] == "mathlib.py" and rec["note"] == "only add multiply"


def test_modify_modal_carries_nonce_and_fields(broker):
    view = broker.modify_modal("nonceX")
    assert view["callback_id"] == broker.MODIFY_CALLBACK
    assert view["private_metadata"] == "nonceX"
    block_ids = {b["block_id"] for b in view["blocks"]}
    assert block_ids == {"scope", "note"}


def test_read_modify_submission_extracts_fields(broker):
    view = {
        "private_metadata": "nonceX",
        "state": {
            "values": {
                "scope": {"scope_input": {"value": "mathlib.py"}},
                "note": {"note_input": {"value": "only add multiply"}},
            }
        },
    }
    assert broker.read_modify_submission(view) == {
        "nonce": "nonceX",
        "scope": "mathlib.py",
        "note": "only add multiply",
    }
    # missing/empty view degrades gracefully, never raises
    assert broker.read_modify_submission({}) == {"nonce": "", "scope": "", "note": ""}
