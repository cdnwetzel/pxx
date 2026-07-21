"""Tests for pxx.improve.evidence: the REAL evidence producer (B8.2/B8.4)."""

from __future__ import annotations

import json
from pathlib import Path

from pxx.improve.channels import CANARY_ADVANCE_RUNS, Channel, ChannelManager
from pxx.improve.evidence import (
    check_preconditions,
    compute_evidence,
    preconditions_met,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

_ADV_CASE = """\
id = "adv-held"
tier = "adversarial"
family = "safety"
task = "Fix it."
partition = "held-out"
honest_patch = "h"
cheat_patch = "c"
"""


def _corpus(root: Path) -> Path:
    tier = root / "adversarial"
    tier.mkdir(parents=True)
    (tier / "case.toml").write_text(_ADV_CASE)
    return root


def _write_evaluation(state: Path, cid: str, **overrides) -> None:
    record = {
        "candidate_id": cid,
        "partition": "held-out",
        "promoted": True,
        "candidate_verdicts": {"adv-held": True},
        "gates": {"scope_violation": True},
        "eval_ids": ["eval-1"],
    }
    record.update(overrides)
    cdir = state / "candidates" / cid
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "evaluation.json").write_text(json.dumps(record))


def test_missing_records_make_every_bar_false(tmp_path):
    state = tmp_path / "state"
    evidence = compute_evidence(
        "ghost",
        state,
        corpus_root=_corpus(tmp_path / "evals"),
        channels=ChannelManager(state),
    )
    assert not evidence.full_pass
    assert not evidence.held_out_pass
    assert not evidence.adversarial_pass
    assert not evidence.canary_pass
    assert "missing evidence" in evidence.details["full_pass"]
    assert "missing evidence" in evidence.details["canary_pass"]


def test_fully_evidenced_candidate_is_green(tmp_path):
    state = tmp_path / "state"
    _write_evaluation(state, "c1")
    channels = ChannelManager(state)
    channels.activate(Channel.CANARY, "c1")
    for i in range(CANARY_ADVANCE_RUNS):
        channels.record_canary_outcome(f"run-{i}", "COMPLETED")
    evidence = compute_evidence(
        "c1", state, corpus_root=_corpus(tmp_path / "evals"), channels=channels
    )
    assert evidence.full_pass
    assert evidence.held_out_pass
    assert evidence.adversarial_pass
    assert evidence.canary_pass
    assert evidence.gates == {"scope_violation": True}


def test_canary_bar_requires_a_full_green_window(tmp_path):
    state = tmp_path / "state"
    _write_evaluation(state, "c1")
    channels = ChannelManager(state)
    channels.activate(Channel.CANARY, "c1")
    for i in range(CANARY_ADVANCE_RUNS - 1):  # one short
        channels.record_canary_outcome(f"run-{i}", "COMPLETED")
    evidence = compute_evidence(
        "c1", state, corpus_root=_corpus(tmp_path / "evals"), channels=channels
    )
    assert not evidence.canary_pass  # 19 < 20: missing evidence, not a green bar


def test_canary_bar_false_on_any_failure(tmp_path):
    state = tmp_path / "state"
    _write_evaluation(state, "c1")
    channels = ChannelManager(state)
    channels.activate(Channel.CANARY, "c1")
    for i in range(CANARY_ADVANCE_RUNS):
        channels.record_canary_outcome(f"run-{i}", "COMPLETED")
    channels.record_canary_outcome("run-bad", "TEST_REGRESSION")
    evidence = compute_evidence(
        "c1", state, corpus_root=_corpus(tmp_path / "evals"), channels=channels
    )
    assert not evidence.canary_pass


def test_adversarial_bar_false_when_safety_case_failed(tmp_path):
    state = tmp_path / "state"
    _write_evaluation(state, "c1", candidate_verdicts={"adv-held": False})
    evidence = compute_evidence(
        "c1",
        state,
        corpus_root=_corpus(tmp_path / "evals"),
        channels=ChannelManager(state),
    )
    assert not evidence.adversarial_pass
    assert not evidence.full_pass


def test_preconditions_all_present_on_repo():
    state = REPO_ROOT / ".pxx-test-preconditions"
    preconditions = check_preconditions(REPO_ROOT, state)
    assert len(preconditions) == 10
    missing = [p.name for p in preconditions if not p.ok]
    assert missing == []
    assert preconditions_met(preconditions)


def test_preconditions_missing_on_bare_tree(tmp_path):
    preconditions = check_preconditions(tmp_path, tmp_path / "state")
    assert not preconditions_met(preconditions)
    missing = {p.name for p in preconditions if not p.ok}
    assert "action_broker" in missing
    assert "held_out_corpus" in missing
    assert "workflow_contract" in missing
