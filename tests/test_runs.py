"""Tests for pxx.runs: run-store projections and metrics over tmp run dirs."""

from __future__ import annotations

import json
from pathlib import Path

from pxx.runs import (
    RunRecord,
    export_jsonl,
    group_by_agent,
    list_runs,
    memory_impact,
    metrics_failures,
    metrics_summary,
)


def _seed_run(
    state_dir: Path,
    run_id: str,
    *,
    agent: str = "agent-a",
    backend: str = "mock",
    model: str = "m1",
    outcome: dict | None = None,
    memory: bool = False,
) -> Path:
    run_dir = state_dir / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "agent_version_id": agent,
                "backend": backend,
                "provider": "ollama",
                "model": model,
            }
        )
    )
    (run_dir / "task.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "task": "t",
                "memory": memory,
                "memory_context_bytes": 100 if memory else 0,
                "ts": 1.0,
            }
        )
    )
    if outcome is not None:
        (run_dir / "outcome.json").write_text(json.dumps({"run_id": run_id, **outcome}))
    return run_dir


def _completed(**overrides: object) -> dict:
    base: dict = {
        "code": "COMPLETED",
        "summary": "ok",
        "rounds": 2,
        "tokens": 100,
        "diff_lines": 10,
        "cost_usd": 0.001,
        "ts": 2.0,
    }
    base.update(overrides)
    return base


# --- list_runs -----------------------------------------------------------------


def test_list_runs_most_recent_first_and_limit(tmp_path: Path) -> None:
    state = tmp_path / "state"
    for i in range(5):
        _seed_run(state, f"2026070{i}T000000Z-{i:08x}", outcome=_completed())
    runs = list_runs(state, limit=3)
    assert len(runs) == 3
    assert [r.run_id for r in runs] == sorted((r.run_id for r in runs), reverse=True)
    assert runs[0].run_id.startswith("20260704")


def test_list_runs_empty_state_dir(tmp_path: Path) -> None:
    assert list_runs(tmp_path / "nope") == []


def test_list_runs_tolerates_partial_and_junk_dirs(tmp_path: Path) -> None:
    state = tmp_path / "state"
    _seed_run(state, "20260701T000000Z-full", outcome=_completed())
    _seed_run(state, "20260702T000000Z-partial", outcome=None)  # manifest+task only
    junk = state / "runs" / "junk"
    junk.mkdir(parents=True)  # no json files at all
    runs = list_runs(state)
    assert [r.run_id for r in runs] == [
        "20260702T000000Z-partial",
        "20260701T000000Z-full",
    ]
    partial = runs[0]
    assert partial.code == ""
    assert not partial.ok
    assert partial.model == "m1"  # manifest still projected


def test_list_runs_projects_outcome_fields(tmp_path: Path) -> None:
    state = tmp_path / "state"
    _seed_run(state, "r1", agent="vid-1", outcome=_completed(), memory=True)
    (run,) = list_runs(state)
    assert run.agent_version_id == "vid-1"
    assert run.ok
    assert run.rounds == 2
    assert run.tokens == 100
    assert run.diff_lines == 10
    assert run.cost_usd == 0.001
    assert run.memory
    assert run.memory_context_bytes == 100


# --- group_by_agent -------------------------------------------------------------


def test_group_by_agent(tmp_path: Path) -> None:
    state = tmp_path / "state"
    _seed_run(state, "20260701T000000Z-1", agent="a1", outcome=_completed())
    _seed_run(state, "20260702T000000Z-2", agent="a1", outcome=_completed())
    _seed_run(state, "20260703T000000Z-3", agent="a2", outcome=_completed())
    groups = group_by_agent(state)
    assert sorted(groups) == ["a1", "a2"]
    assert len(groups["a1"]) == 2
    assert len(groups["a2"]) == 1


# --- metrics_summary ------------------------------------------------------------


def test_metrics_summary(tmp_path: Path) -> None:
    state = tmp_path / "state"
    _seed_run(state, "r1", outcome=_completed())
    _seed_run(
        state, "r2", outcome=_completed(code="BUDGET_EXCEEDED", rounds=5, tokens=500, cost_usd=None)
    )
    summary = metrics_summary(list_runs(state))
    assert summary.total == 2
    assert summary.completed == 1
    assert summary.failed == 1
    assert summary.success_rate == 0.5
    assert summary.total_rounds == 7
    assert summary.total_tokens == 600
    assert summary.by_code == {"BUDGET_EXCEEDED": 1, "COMPLETED": 1}
    assert summary.known_cost_usd == 0.001  # None cost is not fabricated


def test_metrics_summary_empty() -> None:
    summary = metrics_summary([])
    assert summary.total == 0
    assert summary.success_rate == 0.0
    assert summary.known_cost_usd is None


# --- metrics_failures -----------------------------------------------------------


def test_metrics_failures(tmp_path: Path) -> None:
    state = tmp_path / "state"
    _seed_run(state, "r1", outcome=_completed())
    _seed_run(state, "r2", outcome=_completed(code="OUT_OF_SCOPE"))
    _seed_run(state, "r3", outcome=_completed(code="OUT_OF_SCOPE"))
    _seed_run(state, "r4", outcome=_completed(code="ROUND_CAP"))
    report = metrics_failures(list_runs(state))
    assert report.total_failures == 3
    assert report.by_code == {"ROUND_CAP": 1, "OUT_OF_SCOPE": 2}
    assert all(not r.ok for r in report.runs)


# --- memory_impact --------------------------------------------------------------


def test_memory_impact(tmp_path: Path) -> None:
    state = tmp_path / "state"
    _seed_run(state, "r1", outcome=_completed(), memory=True)
    _seed_run(state, "r2", outcome=_completed(), memory=True)
    _seed_run(state, "r3", outcome=_completed(code="MODEL_UNAVAILABLE"), memory=False)
    _seed_run(state, "r4", outcome=_completed(code="ROUND_CAP"), memory=False)
    impact = memory_impact(list_runs(state))
    assert impact.with_memory.runs == 2
    assert impact.with_memory.success_rate == 1.0
    assert impact.without_memory.runs == 2
    assert impact.without_memory.success_rate == 0.0
    assert impact.delta_success_rate == 1.0


def test_memory_impact_one_sided_cohort_has_zero_delta() -> None:
    runs = [RunRecord(run_id="r1", code="COMPLETED", memory=True)]
    impact = memory_impact(runs)
    assert impact.with_memory.runs == 1
    assert impact.without_memory.runs == 0
    assert impact.delta_success_rate == 0.0


# --- export_jsonl ----------------------------------------------------------------


def test_export_jsonl(tmp_path: Path) -> None:
    state = tmp_path / "state"
    _seed_run(state, "r1", outcome=_completed())
    _seed_run(state, "r2", outcome=_completed(code="ROUND_CAP"))
    out = tmp_path / "export.jsonl"
    written = export_jsonl(list_runs(state), out)
    assert written == 2
    lines = out.read_text().splitlines()
    assert len(lines) == 2
    records = [json.loads(line) for line in lines]
    assert {r["code"] for r in records} == {"COMPLETED", "ROUND_CAP"}
    assert all(r["run_id"] for r in records)


# --- M0 regression: C1 (malformed numeric fields degrade to neutral) ------------


def test_load_run_malformed_numeric_fields_degrade_neutral(tmp_path):
    """C1: one bad outcome.json must not crash every projection."""
    run_dir = tmp_path / "runs" / "run-x"
    run_dir.mkdir(parents=True)
    (run_dir / "outcome.json").write_text(
        json.dumps(
            {
                "run_id": "run-x",
                "code": "COMPLETED",
                "rounds": "oops",
                "tokens": [1, 2],
                "diff_lines": "3",
                "ts": "not-a-float",
            }
        )
    )
    runs = list_runs(tmp_path)
    assert len(runs) == 1
    run = runs[0]
    assert run.rounds == 0
    assert run.tokens == 0
    assert run.ts == 0.0
    assert run.diff_lines == 3  # numeric strings still coerce


# --- B2.2: metrics_compare + fat round-trip --------------------------------------


def test_metrics_compare_per_metric_delta() -> None:
    from pxx.runs import metrics_compare

    a = [
        RunRecord(run_id="a1", code="COMPLETED", rounds=2, tokens=100),
        RunRecord(run_id="a2", code="MODEL_UNAVAILABLE", rounds=4, tokens=300),
    ]
    b = [
        RunRecord(run_id="b1", code="COMPLETED", rounds=1, tokens=50),
        RunRecord(run_id="b2", code="COMPLETED", rounds=3, tokens=150),
    ]
    cmp = metrics_compare(a, b)
    assert cmp.delta_success_rate == 0.5  # 0.5 -> 1.0
    assert cmp.delta_avg_rounds == -1.0  # 3.0 -> 2.0
    assert cmp.delta_avg_tokens == -100.0
    assert cmp.delta_known_cost_usd is None  # unpriced on both sides


def test_run_record_round_trips_fat_fields(tmp_path: Path) -> None:
    """The full 12.1 outcome field set survives outcome.json -> RunRecord."""
    _seed_run(
        tmp_path,
        "run-fat",
        outcome={
            "code": "COMPLETED",
            "edit_seconds": 1.5,
            "test_seconds": 0.5,
            "review_seconds": 0.25,
            "files_changed": 3,
            "introduced_failures": 1,
            "contributing_codes": ["REVIEW_REJECTED"],
            "audit_sampled": True,
        },
    )
    (tmp_path / "runs" / "run-fat" / "task.json").write_text(
        json.dumps(
            {
                "task_id": "t" * 16,
                "repository_fingerprint": "f" * 16,
                "starting_commit": "c" * 40,
                "memory": True,
            }
        )
    )
    run = list_runs(tmp_path)[0]
    assert run.edit_seconds == 1.5
    assert run.test_seconds == 0.5
    assert run.review_seconds == 0.25
    assert run.files_changed == 3
    assert run.introduced_failures == 1
    assert run.contributing_codes == ("REVIEW_REJECTED",)
    assert run.audit_sampled is True
    assert run.task_id == "t" * 16
    assert run.repository_fingerprint == "f" * 16
    assert run.starting_commit == "c" * 40
