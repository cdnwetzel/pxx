"""Tests for observation retention and cleanup."""

import tempfile
from pathlib import Path
from datetime import datetime, timedelta
from agentmemory_pkg.storage import ObservationStore
from agentmemory_pkg.cleanup import CleanupManager


class TestObservationTTL:
    """Test TTL and expiration logic."""

    def test_store_with_default_ttl(self):
        """Test storing observation with default TTL."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            store = ObservationStore(db_path=db_path, default_ttl_days=90)

            obs = store.store("test", "some content")
            assert obs.expires_at is not None

            # Parse and verify expiration is roughly 90 days from now
            expires = datetime.fromisoformat(obs.expires_at)
            created = datetime.fromisoformat(obs.created_at)
            delta = (expires - created).days
            assert 89 <= delta <= 91  # Allow 1 day variance

    def test_store_with_zero_ttl(self):
        """Test storing observation with zero TTL (no expiration)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            store = ObservationStore(db_path=db_path, default_ttl_days=0)

            obs = store.store("test", "some content")
            assert obs.expires_at is None

    def test_store_with_custom_ttl(self):
        """Test storing observation with custom TTL."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            store = ObservationStore(db_path=db_path, default_ttl_days=90)

            # Store with custom TTL
            obs = store.store("test", "some content", ttl_days=30)

            expires = datetime.fromisoformat(obs.expires_at)
            created = datetime.fromisoformat(obs.created_at)
            delta = (expires - created).days
            assert 29 <= delta <= 31

    def test_project_ttl_override(self):
        """Test per-project TTL override."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            store = ObservationStore(db_path=db_path, default_ttl_days=90)

            # Set override for "special" project
            store.set_project_ttl("special", 30)

            obs1 = store.store("default", "content")
            obs2 = store.store("special", "content")

            expires1 = datetime.fromisoformat(obs1.expires_at)
            expires2 = datetime.fromisoformat(obs2.expires_at)
            created = datetime.fromisoformat(obs1.created_at)

            delta1 = (expires1 - created).days
            delta2 = (expires2 - created).days

            assert 89 <= delta1 <= 91  # Default 90 days
            assert 29 <= delta2 <= 31  # Override 30 days

    def test_remove_project_ttl_override(self):
        """Test removing per-project TTL override."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            store = ObservationStore(db_path=db_path, default_ttl_days=90)

            store.set_project_ttl("special", 30)
            assert "special" in store.project_ttls

            # Remove override by setting to 0
            store.set_project_ttl("special", 0)
            assert "special" not in store.project_ttls


class TestCleanup:
    """Test cleanup of expired observations."""

    def test_cleanup_expired_observations(self):
        """Test cleanup of expired observations."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            store = ObservationStore(db_path=db_path, default_ttl_days=1)

            # Store observations
            obs1 = store.store("test", "content 1")
            obs2 = store.store("test", "content 2")

            # Manually set one to be expired
            now = datetime.utcnow()
            expired_time = (now - timedelta(days=2)).isoformat()

            import sqlite3

            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    "UPDATE observations SET expires_at = ? WHERE id = ?",
                    (expired_time, obs1.id),
                )
                conn.commit()

            # Cleanup should remove expired one
            result = store.cleanup_expired(dry_run=False)

            assert result["expired_count"] == 1
            assert result["size_freed_mb"] > 0
            assert "test" in result["projects_affected"]

            # Verify it's gone
            retrieved = store._get_by_id(obs1.id)
            assert retrieved is None

            # Verify non-expired still exists
            retrieved = store._get_by_id(obs2.id)
            assert retrieved is not None

    def test_cleanup_dry_run(self):
        """Test dry-run cleanup (no deletion)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            store = ObservationStore(db_path=db_path, default_ttl_days=1)

            obs = store.store("test", "content")

            # Expire it
            expired_time = (datetime.utcnow() - timedelta(days=2)).isoformat()
            import sqlite3

            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    "UPDATE observations SET expires_at = ? WHERE id = ?",
                    (expired_time, obs.id),
                )
                conn.commit()

            # Dry run should report but not delete
            result = store.cleanup_expired(dry_run=True)
            assert result["dry_run"] is True
            assert result["expired_count"] == 1

            # Observation should still exist
            retrieved = store._get_by_id(obs.id)
            assert retrieved is not None

    def test_cleanup_multiple_projects(self):
        """Test cleanup across multiple projects."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            store = ObservationStore(db_path=db_path, default_ttl_days=1)

            obs1 = store.store("project1", "content 1")
            obs2 = store.store("project2", "content 2")
            store.store("project1", "content 3")

            # Expire observations in both projects
            expired_time = (datetime.utcnow() - timedelta(days=2)).isoformat()
            import sqlite3

            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    "UPDATE observations SET expires_at = ? WHERE id IN (?, ?)",
                    (expired_time, obs1.id, obs2.id),
                )
                conn.commit()

            result = store.cleanup_expired(dry_run=False)

            assert result["expired_count"] == 2
            assert set(result["projects_affected"]) == {"project1", "project2"}

    def test_cleanup_with_no_expiration(self):
        """Test cleanup when observations have no expiration."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            store = ObservationStore(db_path=db_path, default_ttl_days=0)

            obs = store.store("test", "content")
            assert obs.expires_at is None

            result = store.cleanup_expired(dry_run=False)
            assert result["expired_count"] == 0


class TestCleanupManager:
    """Test background cleanup manager."""

    def test_cleanup_manager_start_stop(self):
        """Test starting and stopping cleanup manager."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            store = ObservationStore(db_path=db_path)
            manager = CleanupManager(store, interval_seconds=60)

            manager.start()
            assert manager.thread is not None
            assert manager.thread.is_alive()

            manager.stop()
            # Give thread time to stop
            import time

            time.sleep(0.5)
            assert not manager.thread.is_alive()

    def test_cleanup_manager_disabled(self):
        """Test that disabled cleanup manager doesn't start thread."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            store = ObservationStore(db_path=db_path)
            manager = CleanupManager(store, enabled=False)

            manager.start()
            assert manager.thread is None

    def test_cleanup_manager_stats(self):
        """Test cleanup manager statistics."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            store = ObservationStore(db_path=db_path)
            manager = CleanupManager(store, enabled=False)

            stats = manager.get_stats()
            assert "last_cleanup" in stats
            assert "total_expired" in stats
            assert "total_freed_mb" in stats
            assert stats["total_expired"] == 0
