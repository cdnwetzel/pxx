"""Persistent observation memory: SQLite + FTS5 + pure-python vector search.

One database file per memory dir, WAL mode. Observations are deduped by
``sha256(project + content)`` (UNIQUE), full-text indexed via an FTS5
external-content table, and optionally carry a float32 embedding blob
attached lazily on :meth:`MemoryStore.add`.

``search`` is hybrid: FTS5 bm25 rank (weight 0.4) combined with cosine
similarity over stored embedding blobs (weight 0.6). Cosine is pure python
(``array``/``math``) — no numpy. Without an embedder (or when embedding
fails) search degrades to keyword-only and never raises.

Retention: rows with ``expires_at`` in the past are moved to
``memory-archive/YYYY-MM.jsonl`` next to the db by :meth:`archive_expired`
and flagged ``archived=1`` (excluded from list/search).

Phase 20 (outcome-aware memory, schema v2): observations carry
``evidence_confidence`` (provenance rank — frequency != correctness),
``observed_utility``, ``contamination_risk``, ``outcome`` (source run's
terminal code) and ``quarantined``. Existing 2.0 databases gain the columns
via an idempotent ``ALTER TABLE`` migration (:meth:`MemoryStore._migrate`).
Search/list exclude quarantined rows and search multiplies the final score
by ``0.5 + 0.5 * evidence_confidence`` so failed-run inferences rank lower.

Note: ``add``/``search`` are ``async`` because embedding a document/query
may hit a local embedding endpoint (async core convention); all other
methods are sync.
"""

from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
import time
from array import array
from dataclasses import dataclass, replace
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .embeddings import Embedder

log = logging.getLogger("pxx.memory.store")

#: Hybrid ranking weights (DESIGN.md: bm25 0.4 + cosine 0.6).
W_FTS = 0.4
W_VEC = 0.6

#: Evidence confidence by provenance label (Phase 20). Deterministic evidence
#: outranks human judgement, which outranks model claims; observations
#: inferred from failed runs rank lowest. Frequency != correctness.
EVIDENCE_RANK: dict[str, float] = {
    "deterministic_test": 1.0,
    "human_decision": 0.9,
    "reviewer_agreement": 0.7,
    "model_claim": 0.5,
    "failed_run_inference": 0.2,
}


class KnowledgeLayer(StrEnum):
    """The five knowledge layers (Phase 20 amend), each with its own
    retention and injection lifecycle."""

    POLICY = "policy"
    REPOSITORY = "repository"
    SKILL = "skill"
    PLAYBOOK = "playbook"
    EPISODIC = "episodic"


#: Default TTL in days per layer (None = never expires). Applied at insert
#: time when the caller gives no explicit ttl_days.
LAYER_TTL_DAYS: dict[str, float | None] = {
    "policy": None,
    "repository": None,
    "skill": 90.0,
    "playbook": 60.0,
    "episodic": 30.0,
}

#: Graduation ladder: (from_layer, min seen_count, min utility) -> to_layer.
GRADUATION_LADDER: tuple[tuple[str, int, float, str], ...] = (
    ("episodic", 3, 0.7, "skill"),
    ("skill", 5, 0.8, "playbook"),
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
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
    archived INTEGER NOT NULL DEFAULT 0,
    evidence_confidence REAL NOT NULL DEFAULT 0.5,
    observed_utility REAL NOT NULL DEFAULT 0.5,
    contamination_risk REAL NOT NULL DEFAULT 0.0,
    outcome TEXT NOT NULL DEFAULT '',
    quarantined INTEGER NOT NULL DEFAULT 0,
    layer TEXT NOT NULL DEFAULT 'episodic',
    provenance TEXT NOT NULL DEFAULT '',
    validation TEXT NOT NULL DEFAULT '',
    agent_version_id TEXT NOT NULL DEFAULT '',
    seen_count INTEGER NOT NULL DEFAULT 1
);
CREATE VIRTUAL TABLE IF NOT EXISTS observations_fts USING fts5(
    content, tags, content='observations', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS observations_ai AFTER INSERT ON observations BEGIN
    INSERT INTO observations_fts(rowid, content, tags)
    VALUES (new.id, new.content, new.tags);
END;
CREATE TRIGGER IF NOT EXISTS observations_ad AFTER DELETE ON observations BEGIN
    INSERT INTO observations_fts(observations_fts, rowid, content, tags)
    VALUES ('delete', old.id, old.content, old.tags);
END;
"""

_FTS_TOKEN_RE = re.compile(r"\w+")
_MAX_FTS_TOKENS = 20

#: Columns added by the Phase-20 schema (v2); ``_migrate`` adds any that are
#: missing to existing 2.0 databases via ALTER TABLE.
_V2_COLUMNS: tuple[tuple[str, str], ...] = (
    ("evidence_confidence", "REAL NOT NULL DEFAULT 0.5"),
    ("observed_utility", "REAL NOT NULL DEFAULT 0.5"),
    ("contamination_risk", "REAL NOT NULL DEFAULT 0.0"),
    ("outcome", "TEXT NOT NULL DEFAULT ''"),
    ("quarantined", "INTEGER NOT NULL DEFAULT 0"),
)

#: Columns added by the five-layer schema (v2.1); same idempotent migration.
_V21_COLUMNS: tuple[tuple[str, str], ...] = (
    ("layer", "TEXT NOT NULL DEFAULT 'episodic'"),
    ("provenance", "TEXT NOT NULL DEFAULT ''"),
    ("validation", "TEXT NOT NULL DEFAULT ''"),
    ("agent_version_id", "TEXT NOT NULL DEFAULT ''"),
    ("seen_count", "INTEGER NOT NULL DEFAULT 1"),
)


@dataclass(frozen=True)
class Observation:
    """One stored memory observation. ``score`` is set on search results."""

    id: int
    project: str
    kind: str
    content: str
    tags: tuple[str, ...] = ()
    source: str = ""
    session_id: str = ""
    confidence: float = 1.0
    created_at: float = 0.0
    expires_at: float | None = None
    archived: bool = False
    evidence_confidence: float = 0.5
    observed_utility: float = 0.5
    contamination_risk: float = 0.0
    outcome: str = ""
    quarantined: bool = False
    score: float = 0.0
    layer: str = "episodic"
    provenance: str = ""
    validation: str = ""
    agent_version_id: str = ""
    seen_count: int = 1


@dataclass(frozen=True)
class MemoryStats:
    total: int
    active: int
    archived: int
    with_embeddings: int
    projects: dict[str, int]
    quarantined: int = 0


def _blob_to_vec(blob: bytes) -> array:
    vec = array("f")
    vec.frombytes(blob)
    return vec


def _cosine(a: array, b: array) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _fts_query(query: str) -> str:
    """Build a safe FTS5 MATCH expression: quoted tokens joined by OR."""
    tokens = _FTS_TOKEN_RE.findall(query.lower())[:_MAX_FTS_TOKENS]
    return " OR ".join(f'"{t}"' for t in tokens)


def _route_layer(kind: str) -> str:
    """Infer the knowledge layer from an observation kind."""
    mapping = {
        "policy": str(KnowledgeLayer.POLICY),
        "fact": str(KnowledgeLayer.REPOSITORY),
        "repo": str(KnowledgeLayer.REPOSITORY),
        "skill": str(KnowledgeLayer.SKILL),
        "playbook": str(KnowledgeLayer.PLAYBOOK),
    }
    return mapping.get(kind, str(KnowledgeLayer.EPISODIC))


class MemoryStore:
    """SQLite-backed observation store. Failures to embed never gate writes."""

    #: Columns pxx 2.0 requires on the observations table.
    _REQUIRED_COLUMNS = frozenset({"id", "project", "kind", "content", "tags", "hash", "archived"})

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._set_aside_legacy_db()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)
        self._migrate()
        self._embedder: Embedder | None = None

    def _migrate(self) -> None:
        """Add Phase-20 (v2) and five-layer (v2.1) columns to existing dbs.
        Idempotent."""
        cols = {row[1] for row in self._db.execute("PRAGMA table_info(observations)")}
        for name, ddl in (*_V2_COLUMNS, *_V21_COLUMNS):
            if name not in cols:
                self._db.execute(f"ALTER TABLE observations ADD COLUMN {name} {ddl}")
        self._db.commit()

    def _set_aside_legacy_db(self) -> None:
        """Move a pre-2.0 (pxx 1.x) database aside instead of crashing.

        1.x schemas lack required columns (``kind``, ``archived``, ``hash``...).
        The old file is preserved as ``<name>.v1-backup`` for manual salvage.
        """
        if not self.path.is_file():
            return
        try:
            probe = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
            try:
                cols = {row[1] for row in probe.execute("PRAGMA table_info(observations)")}
            finally:
                probe.close()
        except sqlite3.Error:
            return  # unreadable/empty file: let connect() handle it
        if cols and not self._REQUIRED_COLUMNS <= cols:
            backup = self.path.with_suffix(self.path.suffix + ".v1-backup")
            if not backup.exists():
                self.path.rename(backup)
                # WAL sidecars belong to the old file; leaving them would let
                # SQLite replay stale pages into the fresh 2.0 database.
                for suffix in ("-wal", "-shm"):
                    sidecar = self.path.with_name(self.path.name + suffix)
                    if sidecar.exists():
                        sidecar.rename(backup.with_name(backup.name + suffix))
                log.warning("legacy pxx 1.x memory db moved aside to %s", backup)
            else:
                self.path.unlink()
                log.warning("legacy pxx 1.x memory db removed (backup exists)")

    def set_embedder(self, embedder: Embedder | None) -> None:
        """Attach an embedder used to lazily embed on add and query on search."""
        self._embedder = embedder

    async def add(
        self,
        project: str,
        kind: str,
        content: str,
        *,
        tags: tuple[str, ...] | list[str] = (),
        source: str = "",
        session_id: str = "",
        confidence: float = 1.0,
        ttl_days: float | None = None,
        evidence_confidence: float = 0.5,
        contamination_risk: float = 0.0,
        outcome: str = "",
        layer: str = "",
        provenance: str = "",
        validation: str = "",
        agent_version_id: str = "",
    ) -> int:
        """Insert an observation (deduped); returns the observation id.

        Layer routing: explicit ``layer`` wins; otherwise inferred from
        ``kind`` (policy/repository/skill/playbook, else episodic). Per-layer
        default TTL applies when no explicit ``ttl_days`` is given. A repeat
        of an existing observation increments its ``seen_count`` (the
        graduation ladder's recurrence signal).
        """
        resolved_layer = layer if layer else _route_layer(kind)
        if resolved_layer not in LAYER_TTL_DAYS:
            resolved_layer = str(KnowledgeLayer.EPISODIC)
        digest = sha256(f"{project}\n{content}".encode()).hexdigest()
        now = time.time()
        if ttl_days is None:
            ttl_days = LAYER_TTL_DAYS[resolved_layer]
        expires_at = now + ttl_days * 86400.0 if ttl_days is not None else None
        embedding = await self._embed_safe(content)
        cur = self._db.execute(
            "INSERT OR IGNORE INTO observations"
            " (project, kind, content, tags, source, session_id, confidence,"
            "  created_at, expires_at, hash, embedding,"
            "  evidence_confidence, contamination_risk, outcome,"
            "  layer, provenance, validation, agent_version_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                project,
                kind,
                content,
                json.dumps(list(tags)),
                source,
                session_id,
                confidence,
                now,
                expires_at,
                digest,
                embedding,
                evidence_confidence,
                contamination_risk,
                outcome,
                resolved_layer,
                provenance,
                validation,
                agent_version_id,
            ),
        )
        if cur.rowcount:
            self._db.commit()
            return int(cur.lastrowid)
        # recurrence: increment seen_count and keep the strongest provenance
        self._db.execute(
            "UPDATE observations SET seen_count = seen_count + 1,"
            " evidence_confidence = MAX(evidence_confidence, ?) WHERE hash = ?",
            (evidence_confidence, digest),
        )
        self._db.commit()
        row = self._db.execute("SELECT id FROM observations WHERE hash = ?", (digest,)).fetchone()
        return int(row["id"])

    async def search(self, project: str, query: str, *, k: int = 8) -> list[Observation]:
        """Hybrid search: FTS5 bm25 (0.4) + embedding cosine (0.6).

        Quarantined rows are excluded. The final score is weighed by
        provenance (``evidence_confidence``), MEASURED usefulness
        (``observed_utility``), and contamination: frequency != correctness,
        popular-but-wrong observations sink.
        """
        scores: dict[int, float] = {}
        evidence: dict[int, float] = {}
        utility: dict[int, float] = {}
        contam: dict[int, float] = {}

        match = _fts_query(query)
        if match:
            try:
                rows = self._db.execute(
                    "SELECT o.id AS id, bm25(observations_fts) AS rank,"
                    " o.evidence_confidence AS ec, o.observed_utility AS ou,"
                    " o.contamination_risk AS cr"
                    " FROM observations_fts"
                    " JOIN observations o ON o.id = observations_fts.rowid"
                    " WHERE observations_fts MATCH ? AND o.project = ?"
                    " AND o.archived = 0 AND o.quarantined = 0",
                    (match, project),
                ).fetchall()
                for row in rows:
                    rank = row["rank"] or 0.0  # bm25: more negative = better
                    scores[row["id"]] = scores.get(row["id"], 0.0) + W_FTS * (
                        1.0 / (1.0 + max(0.0, -rank))
                    )
                    evidence[row["id"]] = float(row["ec"] or 0.5)
                    utility[row["id"]] = float(row["ou"] or 0.5)
                    contam[row["id"]] = float(row["cr"] or 0.0)
            except sqlite3.Error:
                log.exception("fts search failed (keyword component skipped)")

        qblob = await self._embed_safe(query)
        if qblob:
            qvec = _blob_to_vec(qblob)
            rows = self._db.execute(
                "SELECT id, embedding, evidence_confidence AS ec,"
                " observed_utility AS ou, contamination_risk AS cr FROM observations"
                " WHERE project = ? AND archived = 0 AND quarantined = 0"
                " AND embedding IS NOT NULL",
                (project,),
            ).fetchall()
            for row in rows:
                sim = _cosine(qvec, _blob_to_vec(row["embedding"]))
                scores[row["id"]] = scores.get(row["id"], 0.0) + W_VEC * sim
                evidence[row["id"]] = float(row["ec"] or 0.5)
                utility[row["id"]] = float(row["ou"] or 0.5)
                contam[row["id"]] = float(row["cr"] or 0.0)

        if not scores:
            return []

        def final_score(obs_id: int) -> float:
            factor = 0.4 + 0.3 * evidence.get(obs_id, 0.5) + 0.3 * utility.get(obs_id, 0.5)
            return scores[obs_id] * factor * (1.0 - 0.5 * contam.get(obs_id, 0.0))

        top = sorted(scores, key=final_score, reverse=True)[:k]
        out: list[Observation] = []
        for oid in top:
            obs = self._get(oid)
            if obs is not None:
                out.append(replace(obs, score=final_score(oid)))
        return out

    def set_utility(self, observation_id: int, utility: float) -> bool:
        """Write a MEASURED observed_utility (from ablation, never guessed)."""
        cur = self._db.execute(
            "UPDATE observations SET observed_utility = ? WHERE id = ?",
            (max(0.0, min(1.0, utility)), observation_id),
        )
        self._db.commit()
        return cur.rowcount > 0

    def maybe_graduate(self, project: str) -> list[int]:
        """Promote recurring, high-utility lessons up the ladder (episodic ->
        skill -> playbook). Returns the ids that graduated. Policy and
        repository layers never auto-graduate (human-only)."""
        graduated: list[int] = []
        for from_layer, min_seen, min_utility, to_layer in GRADUATION_LADDER:
            rows = self._db.execute(
                "SELECT id FROM observations WHERE project = ? AND layer = ?"
                " AND seen_count >= ? AND observed_utility >= ?"
                " AND archived = 0 AND quarantined = 0",
                (project, from_layer, min_seen, min_utility),
            ).fetchall()
            for row in rows:
                self._db.execute(
                    "UPDATE observations SET layer = ? WHERE id = ?",
                    (to_layer, row["id"]),
                )
                graduated.append(int(row["id"]))
        if graduated:
            self._db.commit()
        return graduated

    def forget(self, observation_id: int) -> bool:
        """Delete an observation entirely. Returns True when a row was removed."""
        cur = self._db.execute("DELETE FROM observations WHERE id = ?", (observation_id,))
        self._db.commit()
        return cur.rowcount > 0

    def quarantine(self, observation_id: int) -> bool:
        """Exclude an observation from list/search. Returns True when a row changed."""
        cur = self._db.execute(
            "UPDATE observations SET quarantined = 1 WHERE id = ? AND quarantined = 0",
            (observation_id,),
        )
        self._db.commit()
        return cur.rowcount > 0

    def unquarantine(self, observation_id: int) -> bool:
        """Restore a quarantined observation. Returns True when a row changed."""
        cur = self._db.execute(
            "UPDATE observations SET quarantined = 0 WHERE id = ? AND quarantined = 1",
            (observation_id,),
        )
        self._db.commit()
        return cur.rowcount > 0

    def auto_quarantine(self, contamination_threshold: float = 0.7) -> int:
        """Quarantine active rows at/above ``contamination_threshold``.

        Returns the number of newly quarantined rows.
        """
        cur = self._db.execute(
            "UPDATE observations SET quarantined = 1"
            " WHERE quarantined = 0 AND contamination_risk >= ?",
            (contamination_threshold,),
        )
        self._db.commit()
        return cur.rowcount

    def archive_expired(self, *, now: float | None = None) -> int:
        """Append expired rows to ``memory-archive/YYYY-MM.jsonl``; mark archived."""
        now = time.time() if now is None else now
        rows = self._db.execute(
            "SELECT * FROM observations"
            " WHERE archived = 0 AND expires_at IS NOT NULL AND expires_at < ?",
            (now,),
        ).fetchall()
        if not rows:
            return 0
        archive_dir = self.path.parent / "memory-archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        month = time.strftime("%Y-%m", time.localtime(now))
        with (archive_dir / f"{month}.jsonl").open("a") as fh:
            for row in rows:
                fh.write(json.dumps(self._row_dict(row), sort_keys=True) + "\n")
        self._db.execute(
            "UPDATE observations SET archived = 1"
            " WHERE archived = 0 AND expires_at IS NOT NULL AND expires_at < ?",
            (now,),
        )
        self._db.commit()
        return len(rows)

    def list(self, project: str, *, limit: int = 200, layer: str = "") -> list[Observation]:
        """Active, non-quarantined observations for a project, newest first.
        ``layer`` optionally restricts to one knowledge layer."""
        if layer:
            rows = self._db.execute(
                "SELECT * FROM observations"
                " WHERE project = ? AND layer = ? AND archived = 0 AND quarantined = 0"
                " ORDER BY created_at DESC, id DESC LIMIT ?",
                (project, layer, limit),
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT * FROM observations"
                " WHERE project = ? AND archived = 0 AND quarantined = 0"
                " ORDER BY created_at DESC, id DESC LIMIT ?",
                (project, limit),
            ).fetchall()
        return [self._row_to_obs(row) for row in rows]

    def stats(self) -> MemoryStats:
        row = self._db.execute(
            "SELECT COUNT(*) AS total,"
            " COALESCE(SUM(archived), 0) AS archived,"
            " COALESCE(SUM(quarantined), 0) AS quarantined,"
            " COALESCE(SUM(CASE WHEN embedding IS NOT NULL THEN 1 ELSE 0 END), 0) AS emb"
            " FROM observations"
        ).fetchone()
        projects = {
            r["project"]: r["n"]
            for r in self._db.execute(
                "SELECT project, COUNT(*) AS n FROM observations"
                " WHERE archived = 0 GROUP BY project"
            ).fetchall()
        }
        total = int(row["total"])
        archived = int(row["archived"])
        return MemoryStats(
            total=total,
            active=total - archived,
            archived=archived,
            with_embeddings=int(row["emb"]),
            projects=projects,
            quarantined=int(row["quarantined"]),
        )

    def close(self) -> None:
        self._db.close()

    async def _embed_safe(self, text: str) -> bytes | None:
        if self._embedder is None:
            return None
        try:
            blobs = await self._embedder.embed([text])
            return blobs[0] if blobs else None
        except Exception:
            log.exception("embedding failed; storing/searching without vector")
            return None

    def _get(self, observation_id: int) -> Observation | None:
        row = self._db.execute(
            "SELECT * FROM observations WHERE id = ?", (observation_id,)
        ).fetchone()
        return self._row_to_obs(row) if row else None

    @staticmethod
    def _row_dict(row: sqlite3.Row) -> dict:
        data = dict(row)
        data.pop("embedding", None)  # blobs are not JSON-serializable/useful in archive
        return data

    @staticmethod
    def _row_to_obs(row: sqlite3.Row) -> Observation:
        try:
            tags = tuple(str(t) for t in json.loads(row["tags"] or "[]"))
        except (TypeError, ValueError):
            tags = ()
        return Observation(
            id=int(row["id"]),
            project=row["project"],
            kind=row["kind"],
            content=row["content"],
            tags=tags,
            source=row["source"],
            session_id=row["session_id"],
            confidence=float(row["confidence"]),
            created_at=float(row["created_at"]),
            expires_at=row["expires_at"],
            archived=bool(row["archived"]),
            evidence_confidence=float(row["evidence_confidence"]),
            observed_utility=float(row["observed_utility"]),
            contamination_risk=float(row["contamination_risk"]),
            outcome=row["outcome"],
            quarantined=bool(row["quarantined"]),
            layer=row["layer"],
            provenance=row["provenance"],
            validation=row["validation"],
            agent_version_id=row["agent_version_id"],
            seen_count=int(row["seen_count"]),
        )
