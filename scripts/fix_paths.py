"""
One-time migration script to convert absolute stored_path values to relative paths.

Run from the project root:
    python scripts/fix_paths.py

This script:
1. Reads all calls from the database
2. Converts absolute paths to paths relative to the project root
3. Updates the database records

Safe to run multiple times - skips paths that are already relative.
"""

import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "data" / "calls.db"


def main():
    if not DB_PATH.exists():
        print(f"Database not found at {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    rows = conn.execute("SELECT id, stored_path FROM calls").fetchall()
    updated = 0

    for row in rows:
        stored_path = row["stored_path"]
        p = Path(stored_path)

        # Skip if already relative
        if not p.is_absolute():
            print(f"  Call #{row['id']}: already relative ({stored_path})")
            continue

        # Try to make it relative to project root
        try:
            relative = p.relative_to(PROJECT_ROOT)
            conn.execute(
                "UPDATE calls SET stored_path = ? WHERE id = ?",
                (str(relative), row["id"]),
            )
            updated += 1
            print(f"  Call #{row['id']}: {stored_path} -> {relative}")
        except ValueError:
            print(f"  Call #{row['id']}: WARNING - path {stored_path} is not under project root, skipping")

    conn.commit()
    conn.close()
    print(f"\nDone. Updated {updated} of {len(rows)} records.")


if __name__ == "__main__":
    main()
