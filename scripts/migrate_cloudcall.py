"""
CloudCall integration migration: Add cloudcall_user_mapping and cloudcall_recordings tables.

Run from the project root:
    python scripts/migrate_cloudcall.py

Safe to run multiple times.
"""

import sys
from pathlib import Path

# Add project root to path so we can import config
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import DB_PATH


def main():
    if not DB_PATH.exists():
        print(f"Database not found at {DB_PATH}. Run the app first to create it.")
        sys.exit(1)

    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # 1. Create cloudcall_user_mapping table
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cloudcall_user_mapping (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cloudcall_user_id TEXT UNIQUE NOT NULL,
            cloudcall_display_name TEXT,
            app_user_id INTEGER NOT NULL REFERENCES users(id),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    print("cloudcall_user_mapping table: OK")

    # 2. Create cloudcall_recordings table
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cloudcall_recordings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cloudcall_recording_id TEXT UNIQUE NOT NULL,
            cloudcall_user_id TEXT,
            app_user_id INTEGER REFERENCES users(id),
            recording_url TEXT NOT NULL,
            caller_number TEXT,
            callee_number TEXT,
            direction TEXT,
            call_duration_seconds INTEGER,
            call_timestamp TEXT NOT NULL,
            status TEXT DEFAULT 'available',
            imported_call_id INTEGER REFERENCES calls(id),
            error_message TEXT,
            webhook_received_at TEXT NOT NULL,
            webhook_payload TEXT
        )
        """
    )
    print("cloudcall_recordings table: OK")

    # 3. Create indexes
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_cc_mapping_cloudcall_user "
        "ON cloudcall_user_mapping(cloudcall_user_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_cc_mapping_app_user "
        "ON cloudcall_user_mapping(app_user_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_cc_recordings_app_user "
        "ON cloudcall_recordings(app_user_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_cc_recordings_status "
        "ON cloudcall_recordings(status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_cc_recordings_received_at "
        "ON cloudcall_recordings(webhook_received_at)"
    )
    print("Indexes: OK")

    conn.commit()
    conn.close()
    print("\nCloudCall migration complete.")


if __name__ == "__main__":
    main()
