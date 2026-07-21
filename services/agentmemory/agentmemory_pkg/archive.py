"""Observation archival for compliance and recovery."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class ArchiveManager:
    """Manages archival of expired observations."""

    def __init__(self, archive_root: str | None = None):
        """Initialize archive manager.

        Args:
            archive_root: Root directory for archives (default ~/.pxx/memory-archive)
        """
        if archive_root is None:
            archive_root = str(Path.home() / ".pxx" / "memory-archive")

        self.archive_root = Path(archive_root)
        self.archive_root.mkdir(parents=True, exist_ok=True)

    def archive_observations(self, observations: list) -> dict:
        """Archive a batch of observations.

        Args:
            observations: List of Observation objects to archive

        Returns:
            Archive info: count, path, date
        """
        if not observations:
            return {"archived_count": 0}

        now = datetime.utcnow()
        archive_date = now.strftime("%Y-%m")  # YYYY-MM format
        archive_dir = self.archive_root / archive_date
        archive_dir.mkdir(parents=True, exist_ok=True)

        # Archive filename with timestamp
        archive_file = archive_dir / f"archive-{now.strftime('%Y%m%d-%H%M%S')}.jsonl"

        try:
            # Write observations as JSONL (one per line)
            with open(archive_file, "w") as f:
                for obs in observations:
                    record = {
                        "id": obs.id,
                        "project": obs.project,
                        "content": obs.content,
                        "created_at": obs.created_at,
                        "last_accessed": obs.last_accessed,
                        "access_count": obs.access_count,
                        "archived_at": now.isoformat(),
                        "expires_at": obs.expires_at,
                    }
                    f.write(json.dumps(record) + "\n")

            file_size_kb = archive_file.stat().st_size / 1024
            logger.info(
                f"Archived {len(observations)} observations to {archive_file} "
                f"({file_size_kb:.1f}KB)"
            )

            return {
                "archived_count": len(observations),
                "archive_path": str(archive_file),
                "archive_date": archive_date,
                "file_size_kb": file_size_kb,
            }

        except Exception as e:
            logger.error(f"Error archiving observations: {e}")
            return {"archived_count": 0, "error": str(e)}

    def list_archives(self) -> list[dict]:
        """List all archives.

        Returns:
            List of archive info with path, date, count, size
        """
        archives = []

        for date_dir in sorted(self.archive_root.glob("*/"), reverse=True):
            for archive_file in sorted(date_dir.glob("*.jsonl"), reverse=True):
                try:
                    file_size_kb = archive_file.stat().st_size / 1024
                    # Count lines in archive
                    count = sum(1 for _ in open(archive_file))

                    archives.append(
                        {
                            "path": str(archive_file),
                            "date": date_dir.name,
                            "count": count,
                            "size_kb": file_size_kb,
                            "modified": datetime.fromtimestamp(
                                archive_file.stat().st_mtime
                            ).isoformat(),
                        }
                    )
                except Exception as e:
                    logger.warning(f"Error reading archive {archive_file}: {e}")

        return archives

    def get_archive_stats(self) -> dict:
        """Get overall archive statistics.

        Returns:
            Total count, size, oldest/newest archive dates
        """
        archives = self.list_archives()

        if not archives:
            return {
                "total_archives": 0,
                "total_observations": 0,
                "total_size_mb": 0.0,
            }

        total_count = sum(a["count"] for a in archives)
        total_size_mb = sum(a["size_kb"] for a in archives) / 1024
        dates = [a["date"] for a in archives]

        return {
            "total_archives": len(archives),
            "total_observations": total_count,
            "total_size_mb": total_size_mb,
            "oldest_archive": min(dates) if dates else None,
            "newest_archive": max(dates) if dates else None,
        }

    def search_archive(self, query: str, limit: int = 10) -> list[dict]:
        """Search archived observations (simple substring search).

        Args:
            query: Search query (substring)
            limit: Max results to return

        Returns:
            List of matching observations
        """
        results = []
        query_lower = query.lower()

        for archive_file in self.archive_root.glob("**/*.jsonl"):
            try:
                with open(archive_file) as f:
                    for line in f:
                        if len(results) >= limit:
                            return results

                        record = json.loads(line)
                        if (
                            query_lower in record["content"].lower()
                            or query_lower in record["id"].lower()
                        ):
                            record["archive_file"] = str(archive_file)
                            results.append(record)
            except Exception as e:
                logger.warning(f"Error searching archive {archive_file}: {e}")

        return results
