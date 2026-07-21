"""Background cleanup of expired observations."""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentmemory_pkg.storage import ObservationStore

logger = logging.getLogger(__name__)


class CleanupManager:
    """Background thread for cleaning up expired observations."""

    def __init__(
        self,
        store: ObservationStore,
        interval_seconds: int = 3600,
        enabled: bool = True,
    ):
        """Initialize cleanup manager.

        Args:
            store: ObservationStore instance
            interval_seconds: Cleanup interval (default 1 hour)
            enabled: Whether cleanup is enabled
        """
        self.store = store
        self.interval_seconds = interval_seconds
        self.enabled = enabled
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.stats = {
            "last_cleanup": None,
            "total_expired": 0,
            "total_freed_mb": 0,
        }

    def start(self) -> None:
        """Start the cleanup thread."""
        if not self.enabled:
            logger.debug("Cleanup disabled")
            return

        if self.thread and self.thread.is_alive():
            logger.warning("Cleanup thread already running")
            return

        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.name = "agentmemory-cleanup"
        self.thread.start()
        logger.info(f"Cleanup thread started (interval={self.interval_seconds}s)")

    def stop(self) -> None:
        """Stop the cleanup thread gracefully."""
        if not self.thread or not self.thread.is_alive():
            return

        self.stop_event.set()
        self.thread.join(timeout=5)
        logger.info("Cleanup thread stopped")

    def _run(self) -> None:
        """Main cleanup loop (runs in background thread)."""
        logger.info("Cleanup thread running")
        while not self.stop_event.is_set():
            try:
                # Wait for interval or stop signal
                if self.stop_event.wait(timeout=self.interval_seconds):
                    break  # Stop signal received

                # Run cleanup
                result = self.store.cleanup_expired(dry_run=False)
                if result["expired_count"] > 0:
                    logger.info(
                        f"Cleanup: deleted {result['expired_count']} expired "
                        f"observations, freed {result['size_freed_mb']:.2f}MB"
                    )
                    self.stats["last_cleanup"] = datetime.utcnow().isoformat()
                    self.stats["total_expired"] += result["expired_count"]
                    self.stats["total_freed_mb"] += result["size_freed_mb"]

            except Exception as e:
                logger.error(f"Cleanup error: {e}", exc_info=True)

    def get_stats(self) -> dict:
        """Get cleanup statistics."""
        return self.stats.copy()
