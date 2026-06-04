"""Tests for observation archival."""

import json
import tempfile
from pathlib import Path
from agentmemory_pkg.storage import Observation
from agentmemory_pkg.archive import ArchiveManager


class TestArchiveManager:
    """Test archive manager functionality."""

    def test_archive_observations(self):
        """Test archiving observations."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ArchiveManager(archive_root=tmpdir)

            obs = [
                Observation(
                    id="obs-1",
                    project="test",
                    content="content 1",
                    created_at="2024-01-01T00:00:00",
                    last_accessed="2024-01-01T00:00:00",
                    access_count=5,
                    expires_at="2024-04-01T00:00:00",
                ),
                Observation(
                    id="obs-2",
                    project="test",
                    content="content 2",
                    created_at="2024-01-02T00:00:00",
                    last_accessed="2024-01-02T00:00:00",
                    access_count=3,
                    expires_at="2024-04-02T00:00:00",
                ),
            ]

            result = manager.archive_observations(obs)

            assert result["archived_count"] == 2
            assert "archive_path" in result
            assert Path(result["archive_path"]).exists()

    def test_archive_file_format(self):
        """Test that archived observations are in JSONL format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ArchiveManager(archive_root=tmpdir)

            obs = Observation(
                id="obs-1",
                project="test",
                content="test content",
                created_at="2024-01-01T00:00:00",
                last_accessed="2024-01-01T00:00:00",
                access_count=1,
                expires_at="2024-04-01T00:00:00",
            )

            result = manager.archive_observations([obs])
            archive_file = result["archive_path"]

            # Read and verify JSONL format
            with open(archive_file) as f:
                lines = f.readlines()
                assert len(lines) == 1

                record = json.loads(lines[0])
                assert record["id"] == "obs-1"
                assert record["project"] == "test"
                assert record["content"] == "test content"
                assert "archived_at" in record

    def test_archive_directory_structure(self):
        """Test that archives are organized by date."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ArchiveManager(archive_root=tmpdir)

            obs = Observation(
                id="obs-1",
                project="test",
                content="content",
                created_at="2024-01-01T00:00:00",
                last_accessed="2024-01-01T00:00:00",
                access_count=0,
            )

            result = manager.archive_observations([obs])
            archive_date = result["archive_date"]

            # Check YYYY-MM format
            assert len(archive_date) == 7
            assert archive_date[4] == "-"

    def test_list_archives(self):
        """Test listing archives."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ArchiveManager(archive_root=tmpdir)

            obs = [
                Observation(
                    id=f"obs-{i}",
                    project="test",
                    content=f"content {i}",
                    created_at="2024-01-01T00:00:00",
                    last_accessed="2024-01-01T00:00:00",
                    access_count=0,
                )
                for i in range(5)
            ]

            manager.archive_observations(obs)
            archives = manager.list_archives()

            assert len(archives) == 1
            assert archives[0]["count"] == 5
            assert archives[0]["size_kb"] > 0

    def test_search_archive(self):
        """Test searching archived observations."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ArchiveManager(archive_root=tmpdir)

            obs = [
                Observation(
                    id="obs-1",
                    project="test",
                    content="Python code changes",
                    created_at="2024-01-01T00:00:00",
                    last_accessed="2024-01-01T00:00:00",
                    access_count=0,
                ),
                Observation(
                    id="obs-2",
                    project="test",
                    content="JavaScript changes",
                    created_at="2024-01-02T00:00:00",
                    last_accessed="2024-01-02T00:00:00",
                    access_count=0,
                ),
                Observation(
                    id="obs-3",
                    project="test",
                    content="Python improvements",
                    created_at="2024-01-03T00:00:00",
                    last_accessed="2024-01-03T00:00:00",
                    access_count=0,
                ),
            ]

            manager.archive_observations(obs)

            # Search for Python
            results = manager.search_archive("Python", limit=10)
            assert len(results) == 2
            assert all("Python" in r["content"] for r in results)

    def test_search_archive_respects_limit(self):
        """Test that archive search respects limit."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ArchiveManager(archive_root=tmpdir)

            obs = [
                Observation(
                    id=f"obs-{i}",
                    project="test",
                    content="matching content",
                    created_at="2024-01-01T00:00:00",
                    last_accessed="2024-01-01T00:00:00",
                    access_count=0,
                )
                for i in range(10)
            ]

            manager.archive_observations(obs)

            results = manager.search_archive("matching", limit=3)
            assert len(results) <= 3

    def test_get_archive_stats(self):
        """Test archive statistics."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ArchiveManager(archive_root=tmpdir)

            # Empty stats
            stats = manager.get_archive_stats()
            assert stats["total_archives"] == 0
            assert stats["total_observations"] == 0

            # Add archives
            obs = [
                Observation(
                    id=f"obs-{i}",
                    project="test",
                    content="x" * 100,
                    created_at="2024-01-01T00:00:00",
                    last_accessed="2024-01-01T00:00:00",
                    access_count=0,
                )
                for i in range(5)
            ]
            manager.archive_observations(obs)

            stats = manager.get_archive_stats()
            assert stats["total_archives"] == 1
            assert stats["total_observations"] == 5
            assert stats["total_size_mb"] > 0

    def test_archive_empty_list(self):
        """Test archiving empty list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ArchiveManager(archive_root=tmpdir)

            result = manager.archive_observations([])
            assert result["archived_count"] == 0
