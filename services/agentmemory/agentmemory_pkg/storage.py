import sqlite3
import os
import json
import hashlib
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass


@dataclass
class Observation:
    """Stored observation."""

    id: str
    project: str
    content: str
    created_at: str
    last_accessed: str
    access_count: int
    score: float = 0.0
    embedding: list[float] | None = None
    expires_at: str | None = None
    metadata: dict | None = None


class ObservationStore:
    """SQLite-based observation storage with TTL support."""

    def __init__(self, db_path: str = None, default_ttl_days: int = 90):
        if db_path is None:
            db_path = os.path.expanduser("~/.pxx/memory.db")

        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.default_ttl_days = default_ttl_days
        self.project_ttls: dict[str, int] = {}  # Per-project TTL overrides
        self._init_db()

    def _init_db(self):
        """Initialize database schema."""
        with sqlite3.connect(self.db_path) as conn:
            # Create main observations table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS observations (
                    id TEXT PRIMARY KEY,
                    project TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_accessed TEXT NOT NULL,
                    access_count INTEGER DEFAULT 0,
                    embedding TEXT,
                    expires_at TEXT,
                    metadata TEXT,  -- JSON string
                    UNIQUE(project, content)
                )
            """)

            # Add columns if they don't exist (backward compatibility)
            try:
                conn.execute("ALTER TABLE observations ADD COLUMN embedding TEXT")
            except sqlite3.OperationalError:
                pass  # Column already exists
            try:
                conn.execute("ALTER TABLE observations ADD COLUMN expires_at TEXT")
            except sqlite3.OperationalError:
                pass  # Column already exists

            # Create indexes (safe to run multiple times)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_project ON observations(project)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_created ON observations(created_at)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_expires ON observations(expires_at)
            """)

            # BM25 index tables
            conn.execute("""
                CREATE TABLE IF NOT EXISTS bm25_index (
                    term TEXT PRIMARY KEY,
                    doc_freq INTEGER NOT NULL,
                    idf REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS bm25_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            conn.commit()

    def store(
        self, project: str, content: str, metadata: dict | None = None,
        ttl_days: int | None = None
    ) -> Observation:
        """Store a new observation with embedding and TTL."""
        from . import embeddings as emb_module
        from datetime import timedelta

        obs_id = f"obs-{hashlib.md5(f'{project}{content}'.encode()).hexdigest()[:12]}"
        now = datetime.utcnow().isoformat()

        # Calculate expiration time
        expires_at = None
        if ttl_days is None:
            ttl_days = self._get_project_ttl(project)
        if ttl_days > 0:
            expires_at = (datetime.utcnow() + timedelta(days=ttl_days)).isoformat()

        # Generate embedding for the content
        try:
            embedding = emb_module.embed_text(content)
            embedding_json = json.dumps(embedding)
        except Exception as e:
            # Graceful degradation if embedding fails
            import logging

            logging.warning(f"Failed to generate embedding: {e}")
            embedding_json = None

        try:
            with sqlite3.connect(self.db_path) as conn:
                metadata_json = None
                if metadata:
                    try:
                        metadata_json = json.dumps(metadata)
                    except Exception as e:
                        logging.warning(f"Failed to serialize metadata: {e}")

                query = (
                    "INSERT INTO observations "
                    "(id, project, content, created_at, last_accessed, "
                    "access_count, embedding, expires_at, metadata) "
                    "VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)"
                )
                conn.execute(
                    query,
                    (obs_id, project, content, now, now, embedding_json, expires_at, metadata_json),
                )
                conn.commit()
        except sqlite3.IntegrityError:
            # Already exists, update access time
            update_query = (
                "UPDATE observations SET last_accessed = ?, "
                "access_count = access_count + 1 WHERE id = ?"
            )
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(update_query, (now, obs_id))
                conn.commit()

        return self._get_by_id(obs_id)

    def _get_project_ttl(self, project: str) -> int:
        """Get TTL for a project (uses override or default)."""
        return self.project_ttls.get(project, self.default_ttl_days)

    def set_project_ttl(self, project: str, ttl_days: int) -> None:
        """Set TTL for a specific project."""
        if ttl_days <= 0:
            self.project_ttls.pop(project, None)
        else:
            self.project_ttls[project] = ttl_days

    def _get_by_id(self, obs_id: str) -> Observation:
        """Get observation by ID."""
        query = (
            "SELECT id, project, content, created_at, last_accessed, "
            "access_count, embedding, expires_at, metadata FROM observations WHERE id = ?"
        )
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(query, (obs_id,)).fetchone()

        if row:
            embedding = None
            if row[6]:
                try:
                    embedding = json.loads(row[6])
                except (json.JSONDecodeError, TypeError):
                    pass
            metadata = None
            if row[8]:
                try:
                    metadata = json.loads(row[8])
                except (json.JSONDecodeError, TypeError):
                    pass

            return Observation(
                id=row[0],
                project=row[1],
                content=row[2],
                created_at=row[3],
                last_accessed=row[4],
                access_count=row[5],
                embedding=embedding,
                expires_at=row[7],
                metadata=metadata,
            )
        return None

    def get_by_project(self, project: str) -> list[Observation]:
        """Get all observations for a project."""
        query = (
            "SELECT id, project, content, created_at, last_accessed, "
            "access_count, embedding, expires_at, metadata FROM observations WHERE project = ? "
            "ORDER BY last_accessed DESC"
        )
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(query, (project,)).fetchall()

        observations = []
        for row in rows:
            embedding = None
            if row[6]:
                try:
                    embedding = json.loads(row[6])
                except (json.JSONDecodeError, TypeError):
                    pass
            metadata = None
            if row[8]:
                try:
                    metadata = json.loads(row[8])
                except (json.JSONDecodeError, TypeError):
                    pass

            observations.append(
                Observation(
                    id=row[0],
                    project=row[1],
                    content=row[2],
                    created_at=row[3],
                    last_accessed=row[4],
                    access_count=row[5],
                    embedding=embedding,
                    expires_at=row[7],
                    metadata=metadata,
                )
            )
        return observations

    def search(self, project: str, query: str, limit: int = 10) -> list[Observation]:
        """Search observations in a project."""
        # Simple substring search for now; will enhance with FTS/BM25
        query_lower = query.lower()

        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT id, project, content, created_at, last_accessed, access_count
                FROM observations
                WHERE project = ? AND (content LIKE ? OR id LIKE ?)
                ORDER BY last_accessed DESC
                LIMIT ?
                """,
                (project, f"%{query_lower}%", f"%{query_lower}%", limit),
            ).fetchall()

        return [
            Observation(
                id=row[0],
                project=row[1],
                content=row[2],
                created_at=row[3],
                last_accessed=row[4],
                access_count=row[5],
            )
            for row in rows
        ]

    def delete(self, obs_id: str) -> bool:
        """Delete an observation."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("DELETE FROM observations WHERE id = ?", (obs_id,))
            conn.commit()
            return cursor.rowcount > 0
            # Invalidate BM25 index
            conn.execute("DELETE FROM bm25_index")
            conn.execute("DELETE FROM bm25_metadata")
            conn.commit()
            return cursor.rowcount > 0

    def save_bm25_index(self, term_freq: dict, idf_cache: dict, num_docs: int, avg_doc_length: float) -> bool:
        """Persist BM25 index to database."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                # Clear old index
                conn.execute("DELETE FROM bm25_index")
                conn.execute("DELETE FROM bm25_metadata")

                # Save metadata
                conn.execute(
                    "INSERT INTO bm25_metadata (key, value) VALUES (?, ?)",
                    ("num_docs", str(num_docs)),
                )
                conn.execute(
                    "INSERT INTO bm25_metadata (key, value) VALUES (?, ?)",
                    ("avg_doc_length", str(avg_doc_length)),
                )

                # Save index
                for term, (doc_freq, idf) in [(t, (term_freq.get(t, 0), idf_cache.get(t, 0))) for t in idf_cache]:
                    conn.execute(
                        "INSERT INTO bm25_index (term, doc_freq, idf) VALUES (?, ?, ?)",
                        (term, doc_freq, idf),
                    )

                conn.commit()
                return True
        except Exception as e:
            import logging
            logging.error(f"Error saving BM25 index: {e}")
            return False

    def load_bm25_index(self) -> tuple[dict, dict, int, float] | None:
        """Load BM25 index from database."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                # Load metadata
                meta_rows = conn.execute(
                    "SELECT key, value FROM bm25_metadata"
                ).fetchall()
                if not meta_rows:
                    return None

                metadata = {k: v for k, v in meta_rows}
                num_docs = int(metadata.get("num_docs", "0"))
                avg_doc_length = float(metadata.get("avg_doc_length", "0"))

                # Load index
                rows = conn.execute(
                    "SELECT term, doc_freq, idf FROM bm25_index"
                ).fetchall()

                term_freq = {row[0]: row[1] for row in rows}
                idf_cache = {row[0]: row[2] for row in rows}

                return term_freq, idf_cache, num_docs, avg_doc_length
        except Exception as e:
            import logging
            logging.error(f"Error loading BM25 index: {e}")
            return None

    def invalidate_bm25_index(self) -> bool:
        """Clear BM25 index (called when observations change)."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("DELETE FROM bm25_index")
                conn.execute("DELETE FROM bm25_metadata")
                conn.commit()
                return True
        except Exception as e:
            import logging
            logging.error(f"Error invalidating BM25 index: {e}")
            return False

    def delete_project(self, project: str) -> int:
        """Delete all observations for a project."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM observations WHERE project = ?", (project,)
            )
            conn.commit()
            return cursor.rowcount

    def get_project_stats(self, project: str) -> dict:
        """Get statistics for a project."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT COUNT(*), SUM(LENGTH(content))
                FROM observations
                WHERE project = ?
                """,
                (project,),
            ).fetchone()

            count = row[0] or 0
            size_bytes = row[1] or 0

        return {
            "project": project,
            "observation_count": count,
            "size_mb": size_bytes / (1024 * 1024),
        }

    def get_by_function(self, function_name: str) -> list[Observation]:
        """Get observations containing a specific function."""
        query = """
            SELECT id, project, content, created_at, last_accessed,
                   access_count, embedding, expires_at, metadata
            FROM observations
            WHERE metadata LIKE ?
        """
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(query, (f"%\"name\":\"{function_name}\"%",)).fetchall()

        return self._rows_to_observations(rows)

    def get_by_file(self, file_path: str) -> list[Observation]:
        """Get observations for a specific file."""
        query = """
            SELECT id, project, content, created_at, last_accessed,
                   access_count, embedding, expires_at, metadata
            FROM observations
            WHERE metadata LIKE ?
        """
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(query, (f"%\"path\":\"{file_path}\"%",)).fetchall()

        return self._rows_to_observations(rows)

    def _rows_to_observations(self, rows: list[tuple]) -> list[Observation]:
        """Convert database rows to Observation objects."""
        observations = []
        for row in rows:
            metadata = None
            if row[8]:
                try:
                    metadata = json.loads(row[8])
                except (json.JSONDecodeError, TypeError):
                    pass

            embedding = None
            if row[6]:
                try:
                    embedding = json.loads(row[6])
                except (json.JSONDecodeError, TypeError):
                    pass

            observations.append(
                Observation(
                    id=row[0],
                    project=row[1],
                    content=row[2],
                    created_at=row[3],
                    last_accessed=row[4],
                    access_count=row[5],
                    embedding=embedding,
                    expires_at=row[7],
                    metadata=metadata,
                )
            )
        return observations

    def cleanup_expired(self, dry_run: bool = False, archive_manager=None) -> dict:
        """Delete expired observations across all projects.

        Args:
            dry_run: If True, only count what would be deleted
            archive_manager: Optional ArchiveManager for archival before deletion

        Returns:
            Statistics: count deleted, space freed, projects affected, archive info
        """
        now = datetime.utcnow().isoformat()

        with sqlite3.connect(self.db_path) as conn:
            # Find expired observations
            expired_rows = conn.execute(
                """
                SELECT id, project, content, created_at, last_accessed,
                       access_count, expires_at
                FROM observations
                WHERE expires_at IS NOT NULL AND expires_at < ?
                """,
                (now,),
            ).fetchall()

            if dry_run:
                # Just count
                count = len(expired_rows)
                size_freed = sum(len(row[2]) for row in expired_rows)
                projects = set(row[1] for row in expired_rows)
                archive_result = None
            else:
                # Archive before deletion
                archive_result = None
                if archive_manager and expired_rows:
                    # Convert rows to Observation objects for archival

                    obs_list = [
                        Observation(
                            id=row[0],
                            project=row[1],
                            content=row[2],
                            created_at=row[3],
                            last_accessed=row[4],
                            access_count=row[5],
                            expires_at=row[6],
                        )
                        for row in expired_rows
                    ]
                    archive_result = archive_manager.archive_observations(obs_list)

                # Delete them
                expired_ids = [row[0] for row in expired_rows]
                if expired_ids:
                    placeholders = ",".join("?" * len(expired_ids))
                    conn.execute(
                        f"DELETE FROM observations WHERE id IN ({placeholders})",
                        expired_ids,
                    )
                    conn.commit()
                count = len(expired_ids)
                size_freed = sum(len(row[2]) for row in expired_rows)
                projects = set(row[1] for row in expired_rows)

        result = {
            "expired_count": count,
            "size_freed_mb": size_freed / (1024 * 1024),
            "projects_affected": list(projects),
            "dry_run": dry_run,
        }

        if archive_result:
            result["archive"] = archive_result

        return result
