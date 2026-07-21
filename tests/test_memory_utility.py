"""Tests for pxx.memory.utility: measured observed_utility via ablation (B5.1)."""

from __future__ import annotations

import asyncio

import pytest

from pxx.memory.store import MemoryStore
from pxx.memory.utility import compute_utility, measure_utilities


def run(coro):
    return asyncio.run(coro)


def test_compute_utility_useful_vs_useless_vs_harmful():
    useful = compute_utility(
        [{"ok": True, "rounds": 1}, {"ok": True, "rounds": 2}],
        [{"ok": False, "rounds": 3}, {"ok": False, "rounds": 4}],
    )
    assert useful > 0.9  # clearly helpful

    useless = compute_utility([{"ok": True, "rounds": 2}], [{"ok": True, "rounds": 2}])
    assert useless == pytest.approx(0.5)  # no measurable effect

    harmful = compute_utility([{"ok": False, "rounds": 5}], [{"ok": True, "rounds": 1}])
    assert harmful < 0.1  # demonstrably harmful

    with pytest.raises(ValueError):
        compute_utility([], [{"ok": True}])  # never fabricate


def test_measure_utilities_writes_measured_values(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    good_id = run(store.add("proj", "note", "useful hint"))
    bad_id = run(store.add("proj", "note", "misleading hint"))
    runs = [
        # task A: two with-injection runs succeed, two without fail
        {"task_id": "t-a", "ok": True, "rounds": 1, "injected_observation_ids": [str(good_id)]},
        {"task_id": "t-a", "ok": True, "rounds": 2, "injected_observation_ids": [str(good_id)]},
        {"task_id": "t-a", "ok": False, "rounds": 4, "injected_observation_ids": []},
        {"task_id": "t-a", "ok": False, "rounds": 5, "injected_observation_ids": []},
        # task B: with-injection fails, without succeeds (bad hint)
        {"task_id": "t-b", "ok": False, "rounds": 5, "injected_observation_ids": [str(bad_id)]},
        {"task_id": "t-b", "ok": True, "rounds": 1, "injected_observation_ids": []},
    ]
    measurements = measure_utilities(store, "proj", runs)
    by_id = {m.observation_id: m for m in measurements}
    assert set(by_id) == {str(good_id), str(bad_id)}
    assert by_id[str(good_id)].utility > 0.9
    assert by_id[str(bad_id)].utility < 0.1
    assert by_id[str(good_id)].pairs == 1
    # the store now carries MEASURED values (not the 0.5 default)
    rows = {o.id: o for o in store.list("proj")}
    assert rows[good_id].observed_utility > 0.9
    assert rows[bad_id].observed_utility < 0.1
    store.close()


def test_measure_utilities_skips_unmatched_observations(tmp_path):
    """An observation with no matched with/without pair keeps its value —
    no fabricated measurement."""
    store = MemoryStore(tmp_path / "memory.db")
    oid = run(store.add("proj", "note", "unpaired hint"))
    runs = [
        {"task_id": "t-a", "ok": True, "rounds": 1, "injected_observation_ids": [str(oid)]},
        {"task_id": "t-b", "ok": True, "rounds": 1, "injected_observation_ids": []},
    ]
    assert measure_utilities(store, "proj", runs) == []
    row = store.list("proj")[0]
    assert row.observed_utility == 0.5  # untouched default
    store.close()
