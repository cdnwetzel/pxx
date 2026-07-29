"""pxx improve triage: durable human dispositions over the proposal inbox."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pxx.improve.cycle import human_disposition, proposal_slug, run_cycle
from pxx.improve.triage import dispose, pending


def _write_run(state_dir: Path, run_id: str, *, code: str) -> None:
    run_dir = state_dir / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps({"backend": "native", "model": "m1", "agent_version_id": "agent-v1"})
    )
    (run_dir / "task.json").write_text(json.dumps({"run_id": run_id, "memory": False}))
    (run_dir / "outcome.json").write_text(
        json.dumps({"run_id": run_id, "code": code, "rounds": 5, "agent_version_id": "agent-v1"})
    )


def _seed_pending(state_dir: Path) -> str:
    """Run a cycle -> one human-review proposal (budgets:tighten_budget).

    BUDGET_EXCEEDED runs yield two proposals: the derivable memory one
    (auto-qualified) and the budgets one, which needs human judgment.
    Returns the pending entry's slug.
    """
    for i in range(4):
        _write_run(state_dir, f"2026-07-{20 + i:02d}T00-00-00-be{i}", code="BUDGET_EXCEEDED")
    run_cycle(state_dir)
    entries = pending(state_dir)
    assert len(entries) == 1, entries
    return entries[0]["slug"]


def test_pending_lists_slug_and_proposal_fields(tmp_path):
    state_dir = tmp_path / ".pxx"
    slug = _seed_pending(state_dir)
    (entry,) = pending(state_dir)
    assert entry["slug"] == slug
    assert entry["operation"] and entry["target"]
    assert "disposition" not in entry


def test_pending_surfaces_unreadable_entries(tmp_path):
    state_dir = tmp_path / ".pxx"
    box = state_dir / "inbox" / "human-review-required"
    box.mkdir(parents=True)
    (box / "deadbeef0000.json").write_text("[not an object]")
    (entry,) = pending(state_dir)
    assert entry["slug"] == "deadbeef0000" and "error" in entry


def test_reject_requires_note(tmp_path):
    state_dir = tmp_path / ".pxx"
    slug = _seed_pending(state_dir)
    with pytest.raises(ValueError, match="note is required"):
        dispose(state_dir, slug, qualify=False, note="   ")
    assert pending(state_dir)  # nothing moved


def test_reject_moves_entry_with_disposition(tmp_path):
    state_dir = tmp_path / ".pxx"
    slug = _seed_pending(state_dir)
    record = dispose(state_dir, slug, qualify=False, note="temporal confound", by="cwe")
    assert record["reason"] == "temporal confound"
    d = record["disposition"]
    assert d["verdict"] == "rejected" and d["decided_by"] == "cwe" and d["decided"]
    assert not pending(state_dir)
    on_disk = json.loads((state_dir / "inbox" / "rejected" / f"{slug}.json").read_text())
    assert on_disk["disposition"]["verdict"] == "rejected"
    assert human_disposition(state_dir, slug) == "rejected"


def test_qualify_moves_to_qualified(tmp_path):
    state_dir = tmp_path / ".pxx"
    slug = _seed_pending(state_dir)
    record = dispose(state_dir, slug, qualify=True, by="cwe")
    assert record["disposition"]["verdict"] == "qualified"
    assert human_disposition(state_dir, slug) == "qualified"


def test_unknown_slug_raises_keyerror(tmp_path):
    state_dir = tmp_path / ".pxx"
    (state_dir / "inbox" / "human-review-required").mkdir(parents=True)
    with pytest.raises(KeyError, match="no proposal awaiting review"):
        dispose(state_dir, "cafecafecafe", qualify=True)


def test_cycle_respects_human_disposition(tmp_path):
    # The 2026-07-29 finding: every tick re-mined the same runs and
    # re-surfaced the identical proposal a human had already rejected.
    state_dir = tmp_path / ".pxx"
    slug = _seed_pending(state_dir)
    dispose(state_dir, slug, qualify=False, note="transient outage, not a model signal")

    report = run_cycle(state_dir)  # re-mines the same runs

    assert not pending(state_dir)  # the human verdict stuck
    assert any("human-dispositioned" in s["reason"] for s in report.skipped)
    # and the rejection record was not clobbered by the re-run
    on_disk = json.loads((state_dir / "inbox" / "rejected" / f"{slug}.json").read_text())
    assert on_disk["reason"] == "transient outage, not a model signal"


def test_cycle_auto_rejections_carry_no_disposition_and_stay_reroutable(tmp_path):
    # Only HUMAN verdicts are durable; the cycle's own gate rejections may
    # legitimately change between versions.
    state_dir = tmp_path / ".pxx"
    slug = _seed_pending(state_dir)
    sig_dir = state_dir / "inbox" / "rejected"
    if sig_dir.is_dir():
        for path in sig_dir.glob("*.json"):
            assert "disposition" not in json.loads(path.read_text())
    assert human_disposition(state_dir, slug) is None  # pending, not dispositioned


def test_proposal_slug_matches_inbox_filenames(tmp_path):
    state_dir = tmp_path / ".pxx"
    slug = _seed_pending(state_dir)
    (entry,) = pending(state_dir)
    assert proposal_slug(f"{entry['target']}:{entry['operation']}") == slug


def test_dispose_rejects_path_traversal_slug(tmp_path):
    state_dir = tmp_path / ".pxx"
    (state_dir / "inbox" / "human-review-required").mkdir(parents=True)
    for evil in ("../../../../tmp/evil", "a/b", "DEADBEEF0000", "deadbeef", ""):
        with pytest.raises(ValueError, match="invalid slug"):
            dispose(state_dir, evil, qualify=False, note="x")
