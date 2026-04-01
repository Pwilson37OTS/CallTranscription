"""Add recruiter_name and contact_name columns to cloudcall_recordings.

Idempotent — safe to run multiple times.
"""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import DB_PATH


def migrate():
    conn = sqlite3.connect(DB_PATH)
    try:
        # Check existing columns
        cursor = conn.execute("PRAGMA table_info(cloudcall_recordings)")
        existing = {row[1] for row in cursor.fetchall()}

        if "recruiter_name" not in existing:
            conn.execute("ALTER TABLE cloudcall_recordings ADD COLUMN recruiter_name TEXT")
            print("Added column: recruiter_name")

        if "contact_name" not in existing:
            conn.execute("ALTER TABLE cloudcall_recordings ADD COLUMN contact_name TEXT")
            print("Added column: contact_name")

        conn.commit()
        print("Migration complete.")
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
