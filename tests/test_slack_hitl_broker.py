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


# ---- P4: the pxx gate bridge (caller-supplied nonce) ----


def test_sanitize_nonce_accepts_a_gate_minted_nonce(broker):
    from secrets import token_hex

    minted = token_hex(8)  # exactly what hitl_gate.py mints
    assert broker.sanitize_nonce(minted) == minted


def test_sanitize_nonce_rejects_path_traversal(broker):
    """Negative control for the security property this endpoint adds.

    Before the bridge the broker minted every nonce, so it was trusted by construction.
    Now a caller supplies one and it becomes a filename — an unvalidated value is a
    path-traversal primitive that lets the caller choose where the broker writes.
    """
    for bad in (
        "../../etc/cron.d/pwn",
        "../" * 8 + "tmp/x",
        "/etc/passwd",
        "a/b",
        "a.b",
        "nul\x00byte",
        "with space",
        "semi;colon",
    ):
        assert broker.sanitize_nonce(bad) is None, bad


def test_sanitize_nonce_rejects_wrong_type_and_length(broker):
    assert broker.sanitize_nonce(None) is None
    assert broker.sanitize_nonce(12345678) is None
    assert broker.sanitize_nonce({"nonce": "abcdefgh"}) is None
    assert broker.sanitize_nonce("") is None
    assert broker.sanitize_nonce("a" * (broker.NONCE_MIN - 1)) is None
    assert broker.sanitize_nonce("a" * (broker.NONCE_MAX + 1)) is None
    # the bounds themselves are inclusive
    assert broker.sanitize_nonce("a" * broker.NONCE_MIN) is not None
    assert broker.sanitize_nonce("a" * broker.NONCE_MAX) is not None


def test_sanitize_nonce_rejects_non_ascii_alnum(broker):
    """`str.isalnum()` is True for non-ASCII digits and letters, which would let a
    homoglyph or an RTL-override character into a filename. ASCII-only closes that."""
    for bad in ("١٢٣٤٥٦٧٨", "ⅷⅷⅷⅷⅷⅷⅷⅷ", "abcdefg‮gnp"):
        assert broker.sanitize_nonce(bad) is None, bad


def test_decision_written_for_one_nonce_does_not_release_another(broker, tmp_path):
    """The nonce IS the binding between a gate and its approval. A decision for a
    different request must never satisfy the one being waited on."""
    broker.write_decision(tmp_path, "aaaaaaaaaaaaaaaa", "approve", "chris")
    assert (tmp_path / "aaaaaaaaaaaaaaaa.decision").exists()
    assert not (tmp_path / "bbbbbbbbbbbbbbbb.decision").exists()


def test_resolve_nonce_uses_the_callers_when_valid(broker):
    """The bridge: a gate-supplied nonce must be the one the card is posted under."""
    given = "a1b2c3d4e5f60718"
    nonce, err = broker.resolve_nonce({"nonce": given}, lambda: "MINTED00")
    assert (nonce, err) == (given, None)


def test_resolve_nonce_mints_when_absent(broker):
    """Back-compat: the n8n path (R-044/045) sends no nonce and must keep working."""
    nonce, err = broker.resolve_nonce({"summary": "x"}, lambda: "MINTED00")
    assert (nonce, err) == ("MINTED00", None)
    assert broker.resolve_nonce({}, lambda: "MINTED00") == ("MINTED00", None)


def test_resolve_nonce_rejects_garbage_instead_of_minting_a_replacement(broker):
    """Negative control on the failure MODE, not just the failure. Minting a substitute
    would return 200 while leaving the caller waiting on a nonce nobody will ever write,
    turning a misconfiguration into a deny that is indistinguishable from a human
    declining. It must be a loud rejection instead."""
    for bad in ("../../etc/passwd", "short", "has space", 42, ""):
        nonce, err = broker.resolve_nonce({"nonce": bad}, lambda: "MINTED00")
        assert nonce is None and err == "invalid nonce", bad
