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


def test_resolve_nonce_rejects_an_explicit_null(broker):
    """`{"nonce": null}` must NOT mint. `.get()` collapses "absent" and "present but
    null", and a caller's JSON serializer emitting null for an unset field would then
    silently get a fresh nonce -- the permanent-deny failure this bridge exists to
    prevent, reintroduced through the back door. (Found by review on PR #80.)"""
    nonce, err = broker.resolve_nonce({"nonce": None}, lambda: "MINTED00")
    assert nonce is None and err == "invalid nonce"
    # ...while a genuinely absent key still mints, so the n8n path is unaffected
    assert broker.resolve_nonce({}, lambda: "MINTED00") == ("MINTED00", None)


def test_wait_for_decision_returns_the_recorded_decision(broker, tmp_path):
    broker.write_decision(tmp_path, "abcdefgh", "approve", "chris")
    assert broker.wait_for_decision(tmp_path, "abcdefgh", 2.0, poll=0.01) == "approve"


def test_wait_for_decision_times_out_fail_closed(broker, tmp_path):
    import time as _time

    started = _time.time()
    assert broker.wait_for_decision(tmp_path, "neverever", 0.3, poll=0.01) == "timeout"
    assert _time.time() - started >= 0.3  # it actually waited


def test_wait_for_decision_reports_a_corrupt_spool_as_unreadable(broker, tmp_path):
    (tmp_path / "corrupt1.decision").write_text("{not json")
    assert broker.wait_for_decision(tmp_path, "corrupt1", 2.0, poll=0.01) == "unreadable"


def test_only_the_blocking_endpoint_waits(broker):
    """Guards the P4 property against regression IN THE SHIPPED CODE.

    The end-to-end timing test measures the loopback stub, so it would still pass if
    `/post-approval` became blocking (review caught this on PR #80). `post_approval` is
    defined inside `main()` and cannot be imported, so assert on the shipped source: the
    wait lives in exactly one endpoint.
    """
    import inspect
    import re

    src = inspect.getsource(broker.main)

    def body_of(endpoint: str) -> str:
        start = src.index(f'@app.post("{endpoint}")')
        rest = src[start + 1 :]
        nxt = re.search(r"\n    @app\.post\(|\n    # A HITL_DIR", rest)
        return rest[: nxt.start()] if nxt else rest

    assert "wait_for_decision" in body_of("/request-approval")
    assert "wait_for_decision" not in body_of("/post-approval")


# ---- atomic publication (PR #80 review) ----------------------------------------------


def test_final_path_never_appears_before_the_content_is_complete(broker, tmp_path, monkeypatch):
    """The atomicity property, asserted DETERMINISTICALLY.

    Regression test for a real race: `write_decision` used to create the final path with
    O_EXCL and then write into it, leaving a window where the file EXISTED but was empty
    or half-written. The gate polls `exists()` and immediately `json.loads`, so a read
    landing in that window parsed as unreadable and DENIED an approval the human gave.
    Measured at ~1/400 against the pre-fix code with a reader spinning on the path.

    Timing-based reproduction is the obvious test and the wrong one: at 1-in-400 it would
    pass against broken code almost every run, which is a gate that cannot fail. So assert
    the property instead -- stall the write and check what an observer can see. With the
    fix the content goes to a scratch file and is `os.link`ed into place, so the final
    path does not exist until it is complete; without it, the final path is already there
    and empty.
    """
    real_dump = json.dump
    observed = {}

    def stalled_dump(obj, fp, **kw):
        observed["final_exists_midwrite"] = (tmp_path / "slowwrite.decision").exists()
        return real_dump(obj, fp, **kw)

    monkeypatch.setattr(broker.json, "dump", stalled_dump)
    assert broker.write_decision(tmp_path, "slowwrite", "approve", "chris") is True

    assert observed["final_exists_midwrite"] is False, (
        "the final decision path was visible while its content was still being written -- "
        "a reader in that window sees partial JSON and denies a valid approval"
    )
    # and the completed file is intact
    assert json.loads((tmp_path / "slowwrite.decision").read_text())["decision"] == "approve"


def test_atomic_publish_still_leaves_no_scratch_files(broker, tmp_path):
    """The temp file used for atomic publication must not litter the spool -- the gate
    globs nothing, but an operator reading HITL_DIR should see decisions only."""
    broker.write_decision(tmp_path, "cleanup1", "approve", "chris")
    broker.write_decision(tmp_path, "cleanup1", "abort", "attacker")  # refused
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["cleanup1.decision"]


def test_atomic_publish_preserves_single_use_under_contention(broker, tmp_path):
    """Single-use is what O_EXCL gave us; `os.link` must keep it. Race several writers
    on one nonce and assert exactly one wins and the record is that winner's."""
    import threading

    WRITERS = 8
    results: list[bool] = []
    errors: list[BaseException] = []
    lock = threading.Lock()
    barrier = threading.Barrier(WRITERS)

    def contend(n):
        barrier.wait()
        try:
            ok = broker.write_decision(tmp_path, "contend1", "approve", f"writer{n}")
        except BaseException as exc:  # a raise must not masquerade as "did not win"
            with lock:
                errors.append(exc)
            return
        with lock:
            results.append(ok)

    threads = [threading.Thread(target=contend, args=(n,)) for n in range(WRITERS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    # Assert every writer actually FINISHED before judging the winner count. Without this
    # the test passes when one writer succeeds and the other seven die early: results is
    # [True], sum == 1, green. That is a vacuous pass -- the exact failure mode this
    # suite's negative controls exist to prevent. (Caught by review on PR #80.)
    assert not errors, f"writers raised: {errors!r}"
    assert not any(t.is_alive() for t in threads), "a writer thread never finished"
    assert len(results) == WRITERS, f"only {len(results)}/{WRITERS} writers reported"

    assert sum(results) == 1, f"expected exactly one winner, got {sum(results)}"
    rec = json.loads((tmp_path / "contend1.decision").read_text())
    assert rec["decision"] == "approve" and rec["by"].startswith("writer")
