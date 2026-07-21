"""Phase 19 tests: propose-only cycle (stages, triage inbox, anti-spam,
idempotent resume, fcntl lock serialization)."""

from __future__ import annotations

import fcntl
import json
from pathlib import Path

import pytest

from pxx.errors import ConfigError, PxxError
from pxx.improve.cycle import run_cycle


def _write_run(
    state_dir: Path,
    run_id: str,
    *,
    code: str,
    memory: bool = False,
    backend: str = "native",
    model: str = "m1",
    rounds: int = 5,
) -> None:
    run_dir = state_dir / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "backend": backend,
                "model": model,
                "agent_version_id": "agent-v1",
            }
        )
    )
    (run_dir / "task.json").write_text(json.dumps({"run_id": run_id, "memory": memory}))
    (run_dir / "outcome.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "code": code,
                "rounds": rounds,
                "agent_version_id": "agent-v1",
            }
        )
    )


def _failing_runs(
    state_dir: Path, n: int, *, code: str = "BUDGET_EXCEEDED", prefix: str = "run"
) -> None:
    for i in range(n):
        _write_run(state_dir, f"2026-07-{10 + i:02d}T00-00-00-{prefix}{i}", code=code)


def _read_state(state_dir: Path) -> dict:
    return json.loads((state_dir / "cycle-state.json").read_text())


def _candidate_dirs(state_dir: Path) -> list[Path]:
    root = state_dir / "candidates"
    return sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []


# --- happy path: stages, candidates, stops before promotion ----------------------


def test_cycle_persists_candidate_and_stops_before_promotion(tmp_path):
    state_dir = tmp_path / ".pxx"
    _failing_runs(state_dir, 4)  # BUDGET_EXCEEDED, no memory -> 2 proposals

    report = run_cycle(state_dir)

    assert report.mode == "propose-only"
    assert report.runs_collected == 4
    assert report.stopped_before_promotion is True
    # memory proposal is derivable -> exactly one candidate persisted
    assert len(report.candidates) == 1
    cand_dir = _candidate_dirs(state_dir)
    assert [d.name for d in cand_dir] == list(report.candidates)
    data = json.loads((cand_dir[0] / "candidate.json").read_text())
    assert data["target"] == "memory_retrieval_limit"
    assert data["rationale"]
    assert len(data["evidence"]) == 4
    # budgets proposal is not derivable -> human review
    assert report.human_review == ("budgets:tighten_budget",)
    # triage inbox populated
    assert (state_dir / "inbox" / "qualified").is_dir()
    assert (state_dir / "inbox" / "human-review-required").is_dir()
    # report persisted; promotion NEVER happens (no promotions, no channels)
    persisted = json.loads((state_dir / "cycle-report.json").read_text())
    assert persisted["stopped_before_promotion"] is True
    assert persisted["candidates"] == list(report.candidates)
    assert not (state_dir / "promotions").exists()
    assert not (state_dir / "channels.json").exists()


def test_cycle_with_no_runs_is_a_no_op(tmp_path):
    state_dir = tmp_path / ".pxx"
    report = run_cycle(state_dir)
    assert report.runs_collected == 0
    assert report.candidates == ()
    assert report.proposals == 0
    assert (state_dir / "cycle-state.json").exists()


def test_completed_runs_yield_no_proposals(tmp_path):
    state_dir = tmp_path / ".pxx"
    _failing_runs(state_dir, 5, code="COMPLETED")
    report = run_cycle(state_dir)
    assert report.candidates == ()
    assert report.proposals == 0


def test_mode_other_than_propose_only_refused(tmp_path):
    with pytest.raises(ConfigError, match="propose-only"):
        run_cycle(tmp_path / ".pxx", mode="auto-promote")


# --- idempotency / resume after interruption --------------------------------------


def test_rerun_is_idempotent_no_duplicate_work(tmp_path):
    state_dir = tmp_path / ".pxx"
    _failing_runs(state_dir, 4)

    first = run_cycle(state_dir)
    dirs_after_first = sorted(d.name for d in _candidate_dirs(state_dir))
    cand_file = state_dir / "candidates" / first.candidates[0] / "candidate.json"
    bytes_after_first = cand_file.read_bytes()

    second = run_cycle(state_dir)

    assert second.candidates == first.candidates
    assert sorted(d.name for d in _candidate_dirs(state_dir)) == dirs_after_first
    assert cand_file.read_bytes() == bytes_after_first  # never rewritten
    assert second.runs_collected == first.runs_collected


def test_resume_after_interrupted_cycle(tmp_path):
    state_dir = tmp_path / ".pxx"
    _failing_runs(state_dir, 4)
    first = run_cycle(state_dir)

    # simulate interruption: candidates persisted, durable state lost
    (state_dir / "cycle-state.json").unlink()

    resumed = run_cycle(state_dir)  # must not raise on existing candidates
    assert resumed.candidates == first.candidates
    assert len(_candidate_dirs(state_dir)) == 1


# --- anti-spam rules -----------------------------------------------------------------


def test_thin_evidence_cluster_skipped(tmp_path):
    state_dir = tmp_path / ".pxx"
    _failing_runs(state_dir, 2)  # < 3 runs in cluster
    report = run_cycle(state_dir)
    assert report.candidates == ()
    assert report.proposals == 0
    assert any("thin evidence" in s["reason"] for s in report.skipped)


def test_cluster_with_active_candidate_skipped(tmp_path):
    state_dir = tmp_path / ".pxx"
    _failing_runs(state_dir, 4)
    first = run_cycle(state_dir)
    assert len(first.candidates) == 1

    # simulate a DIFFERENT candidate already active for this cluster
    state = _read_state(state_dir)
    state["active_candidates"] = {k: "cand-other" for k in state["active_candidates"]}
    (state_dir / "cycle-state.json").write_text(json.dumps(state))

    second = run_cycle(state_dir)
    assert second.candidates == ()
    assert any("already has an active candidate" in s["reason"] for s in second.skipped)
    rejected = state_dir / "inbox" / "rejected"
    assert rejected.is_dir() and any(rejected.iterdir())


def test_prior_identical_failed_candidate_skipped(tmp_path):
    state_dir = tmp_path / ".pxx"
    _failing_runs(state_dir, 4)
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "cycle-state.json").write_text(
        json.dumps(
            {
                "processed_run_ids": [],
                "active_candidates": {},
                "failed_signatures": ["memory_retrieval_limit:adjust_memory"],
                "cycles": [],
            }
        )
    )

    report = run_cycle(state_dir)
    assert report.candidates == ()
    assert _candidate_dirs(state_dir) == []
    assert any("prior identical candidate failed" in s["reason"] for s in report.skipped)


# --- lock serialization ---------------------------------------------------------------


def test_concurrent_cycle_refused_by_lock(tmp_path):
    state_dir = tmp_path / ".pxx"
    state_dir.mkdir(parents=True)
    lock_path = state_dir / "cycle.lock"
    with lock_path.open("w") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(PxxError, match="already running"):
            run_cycle(state_dir)
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    # after release, a cycle runs fine
    assert run_cycle(state_dir).mode == "propose-only"
