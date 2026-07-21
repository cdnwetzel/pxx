"""Tests for pxx.memory.store + pxx.memory.embeddings.

No network, no Ollama, no numpy: HashEmbedder and tmp_path everywhere.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from array import array

import pytest

from pxx.memory.embeddings import HashEmbedder, OllamaEmbedder, pick_embedder
from pxx.memory.store import MemoryStore, Observation


def run(coro):
    return asyncio.run(coro)


def make_store(tmp_path, *, embedder=None) -> MemoryStore:
    store = MemoryStore(tmp_path / "memory.db")
    if embedder is not None:
        store.set_embedder(embedder)
    return store


# ---------------------------------------------------------------- embeddings


def test_hash_embedder_deterministic_and_normalized():
    emb = HashEmbedder(dim=64)
    a, b = run(emb.embed(["sqlite wal mode is durable", "sqlite wal mode is durable"]))
    assert a == b  # deterministic
    vec = array("f")
    vec.frombytes(a)
    assert len(vec) == 64
    norm = math.sqrt(sum(v * v for v in vec))
    assert norm == pytest.approx(1.0)


def test_hash_embedder_shared_tokens_give_positive_cosine():
    emb = HashEmbedder()
    (a,) = run(emb.embed(["the router probes ollama endpoints"]))
    (b,) = run(emb.embed(["ollama endpoints are probed by the router"]))
    (c,) = run(emb.embed(["banana bread needs ripe bananas"]))
    va, vb, vc = (array("f") for _ in range(3))
    va.frombytes(a)
    vb.frombytes(b)
    vc.frombytes(c)
    sim_ab = sum(x * y for x, y in zip(va, vb, strict=True))
    sim_ac = sum(x * y for x, y in zip(va, vc, strict=True))
    assert sim_ab > sim_ac


def test_hash_embedder_empty_text_is_zero_blob():
    (blob,) = run(HashEmbedder(dim=16).embed(["!!!"]))
    assert blob == bytes(16 * 4)


def test_pick_embedder_none_is_hash():
    emb = run(pick_embedder(None))
    assert isinstance(emb, HashEmbedder)


def test_pick_embedder_unreachable_falls_back():
    # Port 9 (discard) on loopback refuses fast; no external network needed.
    emb = run(pick_embedder("http://127.0.0.1:9", probe_timeout=0.3))
    assert isinstance(emb, HashEmbedder)


def test_ollama_embedder_config():
    emb = OllamaEmbedder("http://localhost:11434/")
    assert emb.base_url == "http://localhost:11434"
    assert emb.model == "nomic-embed-text"


# --------------------------------------------------------------------- store


def test_add_and_dedupe(tmp_path):
    store = make_store(tmp_path)

    async def go():
        first = await store.add("proj", "note", "sqlite uses WAL mode")
        dupe = await store.add("proj", "note", "sqlite uses WAL mode")
        other = await store.add("proj", "note", "pytest runs the tests")
        return first, dupe, other

    first, dupe, other = run(go())
    assert first == dupe
    assert other != first
    assert store.stats().total == 2
    store.close()


def test_dedupe_is_per_project(tmp_path):
    store = make_store(tmp_path)

    async def go():
        a = await store.add("proj-a", "note", "same content")
        b = await store.add("proj-b", "note", "same content")
        return a, b

    a, b = run(go())
    assert a != b
    store.close()


def test_search_keyword_only_without_embedder(tmp_path):
    store = make_store(tmp_path)

    async def go():
        await store.add("proj", "note", "sqlite WAL mode improves write concurrency")
        await store.add("proj", "note", "banana bread needs ripe bananas")
        return await store.search("proj", "sqlite concurrency")

    hits = run(go())
    assert hits
    assert isinstance(hits[0], Observation)
    assert "sqlite" in hits[0].content
    assert hits[0].score > 0
    store.close()


def test_search_hybrid_with_hash_embedder(tmp_path):
    store = make_store(tmp_path, embedder=HashEmbedder())

    async def go():
        await store.add("proj", "note", "the router probes ollama endpoints at startup")
        await store.add("proj", "note", "banana bread needs ripe bananas and flour")
        return await store.search("proj", "endpoint probing router")

    hits = run(go())
    assert hits
    assert "router" in hits[0].content
    assert store.stats().with_embeddings == 2
    store.close()


def test_search_scores_and_k_limit(tmp_path):
    store = make_store(tmp_path, embedder=HashEmbedder())

    async def go():
        for i in range(5):
            await store.add("proj", "note", f"sqlite note number {i} about databases")
        return await store.search("proj", "sqlite databases", k=2)

    hits = run(go())
    assert len(hits) == 2
    assert hits[0].score >= hits[1].score
    store.close()


def test_embedder_failure_tolerated(tmp_path):
    class BrokenEmbedder:
        async def embed(self, texts):
            raise RuntimeError("endpoint down")

    store = make_store(tmp_path, embedder=BrokenEmbedder())

    async def go():
        oid = await store.add("proj", "note", "durable despite broken embedder")
        hits = await store.search("proj", "durable embedder")
        return oid, hits

    oid, hits = run(go())
    assert oid > 0
    assert hits and hits[0].id == oid  # keyword search still works
    assert store.stats().with_embeddings == 0  # NULL embedding stored
    store.close()


def test_forget(tmp_path):
    store = make_store(tmp_path)

    async def go():
        oid = await store.add("proj", "note", "forget me please")
        hits_before = await store.search("proj", "forget")
        removed = store.forget(oid)
        hits_after = await store.search("proj", "forget")
        return hits_before, removed, hits_after

    hits_before, removed, hits_after = run(go())
    assert hits_before
    assert removed is True
    assert hits_after == []
    assert store.forget(999999) is False
    store.close()


def test_archive_expired(tmp_path):
    store = make_store(tmp_path)

    async def go():
        expired = await store.add("proj", "note", "short lived fact", ttl_days=-1.0)
        fresh = await store.add("proj", "note", "long lived fact", ttl_days=30.0)
        return expired, fresh

    expired, fresh = run(go())
    archived = store.archive_expired()
    assert archived == 1

    month = time.strftime("%Y-%m")
    archive_file = tmp_path / "memory-archive" / f"{month}.jsonl"
    lines = archive_file.read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["id"] == expired
    assert record["content"] == "short lived fact"
    assert "embedding" not in record  # blobs stay out of the JSONL archive

    remaining = [o.id for o in store.list("proj")]
    assert remaining == [fresh]
    stats = store.stats()
    assert stats.archived == 1
    assert stats.active == 1

    # second run archives nothing new
    assert store.archive_expired() == 0
    store.close()


def test_archived_rows_excluded_from_search(tmp_path):
    store = make_store(tmp_path)

    async def go():
        await store.add("proj", "note", "ephemeral sqlite trivia", ttl_days=-1.0)
        store.archive_expired()
        return await store.search("proj", "sqlite trivia")

    assert run(go()) == []
    store.close()


def test_list_order_and_limit(tmp_path):
    store = make_store(tmp_path)

    async def go():
        for i in range(3):
            await store.add("proj", "note", f"fact {i}")
            await store.add("other", "note", f"other fact {i}")

    run(go())
    listed = store.list("proj")
    assert [o.content for o in listed] == ["fact 2", "fact 1", "fact 0"]
    assert len(store.list("proj", limit=1)) == 1
    assert store.list("nothing") == []
    store.close()


def test_stats_projects(tmp_path):
    store = make_store(tmp_path)

    async def go():
        await store.add("a", "note", "one")
        await store.add("a", "note", "two")
        await store.add("b", "note", "three")

    run(go())
    stats = store.stats()
    assert stats.total == 3
    assert stats.active == 3
    assert stats.archived == 0
    assert stats.projects == {"a": 2, "b": 1}
    store.close()


def test_persistence_across_open(tmp_path):
    store = make_store(tmp_path)
    run(store.add("proj", "note", "survives a reopen"))
    store.close()

    reopened = make_store(tmp_path)
    assert [o.content for o in reopened.list("proj")] == ["survives a reopen"]
    mode = reopened._db.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
    reopened.close()


# --- B5.1/B5.2: layers, provenance, seen_count, utility, graduation, migration -----


def test_layer_routing_and_default_ttl(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")

    async def scenario():
        policy_id = await store.add("p", "policy", "never do X")
        fact_id = await store.add("p", "fact", "the repo uses uv")
        skill_id = await store.add("p", "skill", "how to rebase")
        note_id = await store.add("p", "note", "something happened")
        return policy_id, fact_id, skill_id, note_id

    policy_id, fact_id, skill_id, note_id = run(scenario())
    rows = {o.id: o for o in store.list("p")}
    assert rows[policy_id].layer == "policy"
    assert rows[fact_id].layer == "repository"
    assert rows[skill_id].layer == "skill"
    assert rows[note_id].layer == "episodic"
    # per-layer TTL: policy/repository never expire, skill 90d, episodic 30d
    assert rows[policy_id].expires_at is None
    assert rows[fact_id].expires_at is None
    assert rows[skill_id].expires_at is not None
    assert rows[note_id].expires_at is not None
    assert rows[note_id].expires_at < rows[skill_id].expires_at
    store.close()


def test_explicit_layer_wins_over_kind(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    oid = run(store.add("p", "note", "explicit", layer="skill"))
    row = next(o for o in store.list("p") if o.id == oid)
    assert row.layer == "skill"
    store.close()


def test_seen_count_increments_on_recurrence(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    for _ in range(3):
        run(store.add("p", "note", "recurring lesson"))
    row = store.list("p")[0]
    assert row.seen_count == 3
    store.close()


def test_set_utility_and_graduation_ladder(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")

    async def seed():
        oid = await store.add("p", "note", "useful lesson")
        for _ in range(2):  # seen 3 times total
            await store.add("p", "note", "useful lesson")
        return oid

    oid = run(seed())
    assert store.maybe_graduate("p") == []  # utility still default 0.5
    assert store.set_utility(oid, 0.75)
    graduated = store.maybe_graduate("p")
    assert graduated == [oid]
    row = next(o for o in store.list("p") if o.id == oid)
    assert row.layer == "skill"
    # next rung needs seen >= 5 and utility >= 0.8
    for _ in range(2):
        run(store.add("p", "note", "useful lesson"))
    store.set_utility(oid, 0.85)
    assert store.maybe_graduate("p") == [oid]
    row = next(o for o in store.list("p") if o.id == oid)
    assert row.layer == "playbook"
    assert store.maybe_graduate("p") == []  # no further rungs
    store.close()


def test_utility_changes_search_ranking(tmp_path):
    from pxx.memory.embeddings import HashEmbedder

    store = MemoryStore(tmp_path / "memory.db")
    store.set_embedder(HashEmbedder())

    async def seed():
        a = await store.add("p", "note", "retry logic for flaky endpoints")
        b = await store.add("p", "note", "retry logic for flaky endpoints!")
        return a, b

    a, b = run(seed())
    first = run(store.search("p", "retry logic flaky endpoints", k=2))
    assert len(first) == 2
    store.set_utility(a, 0.95)
    store.set_utility(b, 0.05)
    ranked = run(store.search("p", "retry logic flaky endpoints", k=2))
    assert ranked[0].id == a  # measured-useful observation now outranks
    store.close()


def test_contamination_downweights_search(tmp_path):
    from pxx.memory.embeddings import HashEmbedder

    store = MemoryStore(tmp_path / "memory.db")
    store.set_embedder(HashEmbedder())

    async def seed():
        clean = await store.add("p", "note", "deterministic gate behavior")
        dirty = await store.add(
            "p",
            "note",
            "deterministic gate behavior!",
            contamination_risk=0.9,
        )
        return clean, dirty

    clean, _dirty = run(seed())
    ranked = run(store.search("p", "deterministic gate behavior", k=2))
    assert ranked[0].id == clean
    store.close()


def test_v21_migration_preserves_data(tmp_path):
    """An old v2-schema db gains the v2.1 columns without losing rows."""
    import sqlite3 as _sql

    db_path = tmp_path / "memory.db"
    conn = _sql.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE observations (
            id INTEGER PRIMARY KEY, project TEXT NOT NULL, kind TEXT NOT NULL,
            content TEXT NOT NULL, tags TEXT NOT NULL DEFAULT '[]',
            source TEXT NOT NULL DEFAULT '', session_id TEXT NOT NULL DEFAULT '',
            confidence REAL NOT NULL DEFAULT 1.0, created_at REAL NOT NULL,
            expires_at REAL, hash TEXT NOT NULL UNIQUE, embedding BLOB,
            archived INTEGER NOT NULL DEFAULT 0,
            evidence_confidence REAL NOT NULL DEFAULT 0.5,
            observed_utility REAL NOT NULL DEFAULT 0.5,
            contamination_risk REAL NOT NULL DEFAULT 0.0,
            outcome TEXT NOT NULL DEFAULT '', quarantined INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    conn.execute(
        "INSERT INTO observations (project, kind, content, hash, created_at)"
        " VALUES ('p', 'note', 'old row', 'h1', 1.0)"
    )
    conn.commit()
    conn.close()

    store = MemoryStore(db_path)  # migration runs here
    cols = {row[1] for row in store._db.execute("PRAGMA table_info(observations)")}
    assert {"layer", "provenance", "validation", "agent_version_id", "seen_count"} <= cols
    rows = store.list("p")
    assert len(rows) == 1 and rows[0].content == "old row"
    assert rows[0].layer == "episodic"  # defaulted, not lost
    store.close()
