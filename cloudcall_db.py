"""Database operations for CloudCall integration.

Follows the same patterns as db.py: uses get_conn(), manual SQL, try/finally.
"""

import sqlite3
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List

from db import get_conn


def init_cloudcall_tables() -> None:
    """Create CloudCall tables if they don't exist."""
    conn = get_conn()
    try:
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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cloudcall_recordings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cloudcall_recording_id TEXT UNIQUE NOT NULL,
                cloudcall_user_id TEXT,
                app_user_id INTEGER REFERENCES users(id),
                recording_url TEXT NOT NULL,
                recruiter_name TEXT,
                contact_name TEXT,
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
        # Auto-migrate: add columns that may not exist in older DBs
        existing_cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(cloudcall_recordings)").fetchall()
        }
        if "recruiter_name" not in existing_cols:
            conn.execute("ALTER TABLE cloudcall_recordings ADD COLUMN recruiter_name TEXT")
        if "contact_name" not in existing_cols:
            conn.execute("ALTER TABLE cloudcall_recordings ADD COLUMN contact_name TEXT")

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
        conn.commit()
    finally:
        conn.close()


# -----------------------------
# User Mapping CRUD
# -----------------------------
def get_cloudcall_user_mapping(cloudcall_user_id: str) -> Optional[sqlite3.Row]:
    conn = get_conn()
    try:
        return conn.execute(
            "SELECT * FROM cloudcall_user_mapping WHERE cloudcall_user_id = ?",
            (cloudcall_user_id,),
        ).fetchone()
    finally:
        conn.close()


def get_all_cloudcall_user_mappings(user_ids=None) -> List[sqlite3.Row]:
    """Return user mappings. user_ids=None -> all. user_ids=[...] -> only those app users."""
    if isinstance(user_ids, int):
        user_ids = [user_ids]

    conn = get_conn()
    try:
        if user_ids is None:
            return conn.execute(
                "SELECT m.*, u.display_name as app_user_name, u.email as app_user_email "
                "FROM cloudcall_user_mapping m "
                "JOIN users u ON m.app_user_id = u.id "
                "ORDER BY m.id"
            ).fetchall()
        if not user_ids:
            return []
        placeholders = ",".join("?" for _ in user_ids)
        return conn.execute(
            f"SELECT m.*, u.display_name as app_user_name, u.email as app_user_email "
            f"FROM cloudcall_user_mapping m "
            f"JOIN users u ON m.app_user_id = u.id "
            f"WHERE m.app_user_id IN ({placeholders}) "
            f"ORDER BY m.id",
            tuple(user_ids),
        ).fetchall()
    finally:
        conn.close()


def upsert_cloudcall_user_mapping(
    cloudcall_user_id: str, app_user_id: int, cloudcall_display_name: str = ""
) -> None:
    now = datetime.utcnow().isoformat()
    conn = get_conn()
    try:
        existing = conn.execute(
            "SELECT id FROM cloudcall_user_mapping WHERE cloudcall_user_id = ?",
            (cloudcall_user_id,),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE cloudcall_user_mapping SET app_user_id = ?, cloudcall_display_name = ?, updated_at = ? "
                "WHERE cloudcall_user_id = ?",
                (app_user_id, cloudcall_display_name, now, cloudcall_user_id),
            )
        else:
            conn.execute(
                "INSERT INTO cloudcall_user_mapping "
                "(cloudcall_user_id, cloudcall_display_name, app_user_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (cloudcall_user_id, cloudcall_display_name, app_user_id, now, now),
            )
        conn.commit()
    finally:
        conn.close()


def delete_cloudcall_user_mapping(mapping_id: int) -> None:
    conn = get_conn()
    try:
        conn.execute("DELETE FROM cloudcall_user_mapping WHERE id = ?", (mapping_id,))
        conn.commit()
    finally:
        conn.close()


# -----------------------------
# Recording CRUD
# -----------------------------
def insert_cloudcall_recording(record: Dict[str, Any]) -> int:
    conn = get_conn()
    try:
        cursor = conn.execute(
            """
            INSERT INTO cloudcall_recordings (
                cloudcall_recording_id, cloudcall_user_id, app_user_id,
                recording_url, recruiter_name, contact_name,
                caller_number, callee_number, direction,
                call_duration_seconds, call_timestamp, status,
                webhook_received_at, webhook_payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["cloudcall_recording_id"],
                record.get("cloudcall_user_id"),
                record.get("app_user_id"),
                record["recording_url"],
                record.get("recruiter_name"),
                record.get("contact_name"),
                record.get("caller_number"),
                record.get("callee_number"),
                record.get("direction"),
                record.get("call_duration_seconds"),
                record["call_timestamp"],
                record.get("status", "available"),
                record.get("webhook_received_at", datetime.utcnow().isoformat()),
                record.get("webhook_payload"),
            ),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_cloudcall_recordings(
    user_ids=None, hours: int = 48, user_id=None
) -> List[sqlite3.Row]:
    """Get recordings from the last N hours.

    user_ids semantics:
      - None      -> no filter (admin / system)
      - int       -> single-user filter (legacy callers / recruiter scope)
      - list[int] -> multi-user filter (manager scope = team members)
      - []        -> match nothing (manager whose team has no members)

    The legacy `user_id` (single int) keyword is also accepted for backward
    compatibility with older callers and tests.
    """
    if user_ids is None and user_id is not None:
        user_ids = user_id
    if isinstance(user_ids, int):
        user_ids = [user_ids]

    conn = get_conn()
    cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    try:
        if user_ids is None:
            return conn.execute(
                "SELECT * FROM cloudcall_recordings "
                "WHERE webhook_received_at >= ? "
                "ORDER BY call_timestamp DESC",
                (cutoff,),
            ).fetchall()
        if not user_ids:
            return []
        placeholders = ",".join("?" for _ in user_ids)
        return conn.execute(
            f"SELECT * FROM cloudcall_recordings "
            f"WHERE app_user_id IN ({placeholders}) AND webhook_received_at >= ? "
            "ORDER BY call_timestamp DESC",
            (*user_ids, cutoff),
        ).fetchall()
    finally:
        conn.close()


def get_cloudcall_recording(recording_id: int) -> Optional[sqlite3.Row]:
    conn = get_conn()
    try:
        return conn.execute(
            "SELECT * FROM cloudcall_recordings WHERE id = ?", (recording_id,)
        ).fetchone()
    finally:
        conn.close()


def update_cloudcall_recording(recording_id: int, **fields) -> None:
    allowed = {
        "status", "imported_call_id", "error_message", "app_user_id",
        "recording_url",
    }
    if not fields:
        return
    invalid = set(fields.keys()) - allowed
    if invalid:
        raise ValueError(f"Disallowed column(s): {invalid}")
    set_clause = ", ".join([f"{k} = ?" for k in fields.keys()])
    values = list(fields.values()) + [recording_id]
    conn = get_conn()
    try:
        conn.execute(
            f"UPDATE cloudcall_recordings SET {set_clause} WHERE id = ?", values
        )
        conn.commit()
    finally:
        conn.close()


def delete_expired_cloudcall_recordings(retention_hours: int = 48) -> int:
    """Delete recordings older than retention_hours. Returns count deleted."""
    conn = get_conn()
    cutoff = (datetime.utcnow() - timedelta(hours=retention_hours)).isoformat()
    try:
        cursor = conn.execute(
            "DELETE FROM cloudcall_recordings WHERE webhook_received_at < ?",
            (cutoff,),
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def get_ingestion_statuses_for_call_ids(call_data_ids) -> Dict[str, Dict[str, Any]]:
    """Look up the ingestion status in ECHO for a batch of CloudCall call IDs.

    Returns a dict mapping cloudcall_recording_id -> {"status": ..., "error_message": ...}.
    Calls not present in the DB are absent from the result. Used by the
    admin Call Inspector to annotate CloudCall's raw data with what ECHO
    actually has, so admins can see at a glance which calls fell through
    ingestion.
    """
    call_data_ids = list(call_data_ids or [])
    if not call_data_ids:
        return {}
    conn = get_conn()
    try:
        placeholders = ",".join("?" for _ in call_data_ids)
        rows = conn.execute(
            f"SELECT cloudcall_recording_id, status, error_message "
            f"FROM cloudcall_recordings "
            f"WHERE cloudcall_recording_id IN ({placeholders})",
            tuple(call_data_ids),
        ).fetchall()
        return {
            r["cloudcall_recording_id"]: {
                "status": r["status"],
                "error_message": r["error_message"] or "",
            }
            for r in rows
        }
    finally:
        conn.close()


def get_unmapped_cloudcall_user_ids() -> List[str]:
    """Get distinct CloudCall user IDs from recordings that have no user mapping."""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT cloudcall_user_id FROM cloudcall_recordings "
            "WHERE app_user_id IS NULL AND cloudcall_user_id IS NOT NULL"
        ).fetchall()
        return [row["cloudcall_user_id"] for row in rows]
    finally:
        conn.close()
