"""Tests for pxx.entropy: golden principles, quality grades, GC (B5.4)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from pxx.entropy import quality_grades, run_gc, run_golden_principles
from pxx.memory.store import MemoryStore


def run(coro):
    return asyncio.run(coro)


def test_golden_principles_catches_violation(tmp_path):
    (tmp_path / "pxx").mkdir()
    (tmp_path / "pxx" / "bad.py").write_text("print('hello')\nx = 1\n")
    violations = run_golden_principles(tmp_path)
    assert any(v.principle == "no-print-outside-cli" for v in violations)


def test_golden_principles_respects_excludes(tmp_path):
    (tmp_path / "pxx").mkdir()
    (tmp_path / "pxx" / "cli.py").write_text("print('allowed here')\n")
    violations = run_golden_principles(tmp_path)
    assert not [v for v in violations if v.principle == "no-print-outside-cli"]


def test_golden_principles_clean_on_repo():
    repo = Path(__file__).resolve().parent.parent
    assert run_golden_principles(repo) == []


def test_quality_grades_reflect_utility_and_quarantine(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    oid = run(store.add("p", "note", "great lesson"))
    store.set_utility(oid, 0.95)
    low = run(store.add("p", "note", "bad lesson"))
    store.set_utility(low, 0.05)
    grades = quality_grades(store)
    assert set(grades) == {"policy", "repository", "skill", "playbook", "episodic"}
    assert grades["episodic"] in {"A", "B", "C", "D", "F"}
    assert grades["policy"] == "—"  # empty layer
    store.close()


def test_gc_prunes_low_utility_and_is_deterministic(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")

    async def seed():
        low = await store.add("p", "note", "junk")
        await store.add("p", "note", "junk")  # seen_count = 2
        good = await store.add("p", "note", "keeper")
        return low, good

    low, good = run(seed())
    store.set_utility(low, 0.1)
    store.set_utility(good, 0.9)

    report = run_gc(store)
    assert report.pruned_low_utility == 1
    assert report.pruned_ids == (low,)
    remaining = {o.id for o in store.list("p")}
    assert good in remaining and low not in remaining

    # deterministic: a second pass over the pruned store reports nothing more
    second = run_gc(store)
    assert second.pruned_low_utility == 0
    assert second.archived_expired == 0
    store.close()


def test_gc_archives_expired(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    run(store.add("p", "note", "short lived", ttl_days=-1))  # already expired
    report = run_gc(store)
    assert report.archived_expired == 1
    store.close()
