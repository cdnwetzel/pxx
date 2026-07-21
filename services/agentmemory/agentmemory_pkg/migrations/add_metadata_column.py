"""Migration script to add metadata column to observations table."""

import sqlite3
from pathlib import Path

def apply_migration(db_path: str | None = None) -> bool:
    """Add metadata column to observations table."""
    if db_path is None:
        db_path = Path.home() / ".pxx" / "memory.db"

    try:
        with sqlite3.connect(db_path) as conn:
            # Add metadata column
            try:
                conn.execute("ALTER TABLE observations ADD COLUMN metadata TEXT")
                conn.commit()
                print(f"Successfully added metadata column to {db_path}")
                return True
            except sqlite3.OperationalError as e:
                print(f"Failed to add metadata column: {e}")
                return False
    except Exception as e:
        print(f"Error applying migration: {e}")
        return False

if __name__ == "__main__":
    apply_migration()
