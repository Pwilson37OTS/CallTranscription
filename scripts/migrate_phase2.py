"""
Phase 2 migration: Add users table, api_usage table, and user_id column to calls.

Run from the project root:
    python scripts/migrate_phase2.py

This script:
1. Creates users and api_usage tables (if they don't exist)
2. Adds user_id column to calls table (if not present)
3. Creates a seed admin user
4. Assigns all existing calls (with NULL user_id) to the admin

Safe to run multiple times.
"""

import sqlite3
import sys
from pathlib import Path

# Add project root to path so we can import config
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import DB_PATH, ADMIN_EMAIL, ADMIN_DEFAULT_PASSWORD


def main():
    if not DB_PATH.exists():
        print(f"Database not found at {DB_PATH}. Run the app first to create it.")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # 1. Create users table
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            display_name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT DEFAULT 'recruiter',
            is_active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL
        )
        """
    )
    print("users table: OK")

    # 2. Create api_usage table
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS api_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER REFERENCES users(id),
            call_id INTEGER REFERENCES calls(id),
            operation TEXT NOT NULL,
            model TEXT NOT NULL,
            estimated_cost_cents INTEGER,
            created_at TEXT NOT NULL
        )
        """
    )
    print("api_usage table: OK")

    # 3. Add user_id column to calls if not present
    columns = [row["name"] for row in conn.execute("PRAGMA table_info(calls)").fetchall()]
    if "user_id" not in columns:
        conn.execute("ALTER TABLE calls ADD COLUMN user_id INTEGER REFERENCES users(id)")
        print("calls.user_id column: ADDED")
    else:
        print("calls.user_id column: already exists")

    # 4. Create seed admin user if no users exist
    existing_users = conn.execute("SELECT COUNT(*) as cnt FROM users").fetchone()["cnt"]
    if existing_users == 0:
        import bcrypt
        from datetime import datetime

        password_hash = bcrypt.hashpw(
            ADMIN_DEFAULT_PASSWORD.encode("utf-8"), bcrypt.gensalt()
        ).decode("utf-8")

        conn.execute(
            "INSERT INTO users (email, display_name, password_hash, role, is_active, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (ADMIN_EMAIL, "Admin", password_hash, "admin", 1, datetime.utcnow().isoformat()),
        )
        print(f"Seed admin user created: {ADMIN_EMAIL}")
    else:
        print(f"Users already exist ({existing_users}), skipping seed")

    # 5. Assign existing calls with NULL user_id to the admin
    admin = conn.execute("SELECT id FROM users WHERE role = 'admin' LIMIT 1").fetchone()
    if admin:
        result = conn.execute(
            "UPDATE calls SET user_id = ? WHERE user_id IS NULL", (admin["id"],)
        )
        if result.rowcount > 0:
            print(f"Assigned {result.rowcount} existing calls to admin (user_id={admin['id']})")
        else:
            print("No orphaned calls to reassign")

    # 6. Create indexes
    conn.execute("CREATE INDEX IF NOT EXISTS idx_calls_user_id ON calls(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_calls_status ON calls(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_calls_created_at ON calls(created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_api_usage_user_id ON api_usage(user_id)")
    print("Indexes: OK")

    conn.commit()
    conn.close()
    print("\nMigration complete.")


if __name__ == "__main__":
    main()
