"""Legacy (pxx 1.x) memory.db migration tests."""

from __future__ import annotations

import asyncio
import sqlite3

from pxx.memory.store import MemoryStore


def _make_v1_db(path):
    """Create a minimal pxx 1.x-shaped observations table (no kind/archived/hash)."""
    db = sqlite3.connect(str(path))
    db.execute(
        "CREATE TABLE observations (id INTEGER PRIMARY KEY, project TEXT,"
        " content TEXT, created_at REAL, expires_at REAL, embedding BLOB)"
    )
    db.execute("INSERT INTO observations (project, content, created_at) VALUES ('p', 'old', 1.0)")
    db.commit()
    db.close()


def test_legacy_db_is_set_aside_and_fresh_db_works(tmp_path):
    db_path = tmp_path / "memory.db"
    _make_v1_db(db_path)

    store = MemoryStore(db_path)
    try:
        backup = tmp_path / "memory.db.v1-backup"
        assert backup.is_file()  # old data preserved in the backup
        probe = sqlite3.connect(str(backup))
        assert probe.execute("SELECT content FROM observations").fetchone()[0] == "old"
        probe.close()
        # fresh 2.0 schema works
        stats = store.stats()
        assert stats.total == 0
    finally:
        store.close()


def test_current_db_is_not_touched(tmp_path):
    db_path = tmp_path / "memory.db"
    store = MemoryStore(db_path)
    store.close()
    mtime = db_path.stat().st_mtime_ns

    store2 = MemoryStore(db_path)
    try:
        assert not (tmp_path / "memory.db.v1-backup").exists()
        assert db_path.stat().st_mtime_ns >= mtime
    finally:
        store2.close()


# ------------------------------------------------------- schema v2 (Phase 20)

_V1_SCHEMA_2_0 = """
CREATE TABLE observations (
    id INTEGER PRIMARY KEY,
    project TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    source TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 1.0,
    created_at REAL NOT NULL,
    expires_at REAL,
    hash TEXT NOT NULL UNIQUE,
    embedding BLOB,
    archived INTEGER NOT NULL DEFAULT 0
);
CREATE VIRTUAL TABLE observations_fts USING fts5(
    content, tags, content='observations', content_rowid='id'
);
CREATE TRIGGER observations_ai AFTER INSERT ON observations BEGIN
    INSERT INTO observations_fts(rowid, content, tags)
    VALUES (new.id, new.content, new.tags);
END;
CREATE TRIGGER observations_ad AFTER DELETE ON observations BEGIN
    INSERT INTO observations_fts(observations_fts, rowid, content, tags)
    VALUES ('delete', old.id, old.content, old.tags);
END;
"""

_V2_COLUMNS = {
    "evidence_confidence",
    "observed_utility",
    "contamination_risk",
    "outcome",
    "quarantined",
}


def _make_pre_phase20_db(path):
    """Create a 2.0 (pre-Phase-20) db: full v1 schema, no v2 columns, one row."""
    db = sqlite3.connect(str(path))
    db.executescript(_V1_SCHEMA_2_0)
    db.execute(
        "INSERT INTO observations (project, kind, content, hash, created_at)"
        " VALUES ('p', 'note', 'pre phase-20 fact', 'h1', 1.0)"
    )
    db.commit()
    db.close()


def test_schema_v2_migration_on_existing_2_0_db(tmp_path):
    db_path = tmp_path / "memory.db"
    _make_pre_phase20_db(db_path)

    store = MemoryStore(db_path)
    try:
        # no legacy set-aside: the v1-schema 2.0 db is migrated in place
        assert not (tmp_path / "memory.db.v1-backup").exists()
        cols = {row[1] for row in store._db.execute("PRAGMA table_info(observations)")}
        assert _V2_COLUMNS <= cols

        # pre-existing rows get the schema defaults
        obs = store.list("p")
        assert len(obs) == 1
        assert obs[0].content == "pre phase-20 fact"
        assert obs[0].evidence_confidence == 0.5
        assert obs[0].observed_utility == 0.5
        assert obs[0].contamination_risk == 0.0
        assert obs[0].outcome == ""
        assert obs[0].quarantined is False

        # store stays fully functional after migration
        asyncio.run(store.add("p", "note", "post migration fact", outcome="COMPLETED"))
        assert store.stats().total == 2
    finally:
        store.close()


def test_schema_v2_migration_is_idempotent(tmp_path):
    db_path = tmp_path / "memory.db"
    _make_pre_phase20_db(db_path)

    MemoryStore(db_path).close()
    store = MemoryStore(db_path)  # second open: ALTERs must not repeat/fail
    try:
        assert store.stats().total == 1
    finally:
        store.close()


def test_fresh_db_has_v2_columns(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    try:
        cols = {row[1] for row in store._db.execute("PRAGMA table_info(observations)")}
        assert _V2_COLUMNS <= cols
    finally:
        store.close()
