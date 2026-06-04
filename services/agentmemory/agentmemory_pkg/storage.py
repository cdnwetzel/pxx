import sqlite3
import os
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


class ObservationStore:
    """SQLite-based observation storage."""

    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = os.path.expanduser("~/.pxx/memory.db")

        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Initialize database schema."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS observations (
                    id TEXT PRIMARY KEY,
                    project TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_accessed TEXT NOT NULL,
                    access_count INTEGER DEFAULT 0,
                    UNIQUE(project, content)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_project ON observations(project)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_created ON observations(created_at)
            """)
            conn.commit()

    def store(self, project: str, content: str) -> Observation:
        """Store a new observation."""
        obs_id = f"obs-{hashlib.md5(f'{project}{content}'.encode()).hexdigest()[:12]}"
        now = datetime.utcnow().isoformat()

        try:
            with sqlite3.connect(self.db_path) as conn:
                query = (
                    "INSERT INTO observations "
                    "(id, project, content, created_at, last_accessed, access_count) "
                    "VALUES (?, ?, ?, ?, ?, 0)"
                )
                conn.execute(query, (obs_id, project, content, now, now))
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

    def _get_by_id(self, obs_id: str) -> Observation:
        """Get observation by ID."""
        query = (
            "SELECT id, project, content, created_at, last_accessed, "
            "access_count FROM observations WHERE id = ?"
        )
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(query, (obs_id,)).fetchone()

        if row:
            return Observation(
                id=row[0],
                project=row[1],
                content=row[2],
                created_at=row[3],
                last_accessed=row[4],
                access_count=row[5],
            )
        return None

    def get_by_project(self, project: str) -> list[Observation]:
        """Get all observations for a project."""
        query = (
            "SELECT id, project, content, created_at, last_accessed, "
            "access_count FROM observations WHERE project = ? "
            "ORDER BY last_accessed DESC"
        )
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(query, (project,)).fetchall()

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
                (project, f"%{query_lower}%", f"%{query_lower}%", limit)
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
            cursor = conn.execute(
                "DELETE FROM observations WHERE id = ?", (obs_id,)
            )
            conn.commit()
            return cursor.rowcount > 0

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
                (project,)
            ).fetchone()

            count = row[0] or 0
            size_bytes = row[1] or 0

        return {
            "project": project,
            "observation_count": count,
            "size_mb": size_bytes / (1024 * 1024),
        }
