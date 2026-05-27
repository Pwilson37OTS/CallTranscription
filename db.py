import sqlite3
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List

from config import DB_PATH


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # journal_mode=DELETE (the SQLite default) instead of WAL. WAL is faster
    # for concurrent access but is unreliable on Railway's network-mounted
    # persistent volumes — boot can fail with "disk I/O error" when SQLite
    # tries to create the .db-wal/.db-shm sidecar files. DELETE works on any
    # filesystem; the timeout=10 above means readers/writers wait on locks
    # instead of failing.
    conn.execute("PRAGMA journal_mode = DELETE")
    return conn


def init_db() -> None:
    conn = get_conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                display_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT DEFAULT 'recruiter',
                is_active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL,
                team TEXT
            )
            """
        )
        # Idempotent migration for installs created before team existed.
        # Managers are scoped to a team — their team is what they manage.
        try:
            conn.execute("ALTER TABLE users ADD COLUMN team TEXT")
        except sqlite3.OperationalError:
            pass  # column already exists
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                original_filename TEXT NOT NULL,
                stored_filename TEXT NOT NULL,
                stored_path TEXT NOT NULL,
                mime_type TEXT,
                recruiter_name TEXT,
                subject_name TEXT,
                company_name TEXT,
                notes TEXT,
                call_type TEXT DEFAULT 'general_recruiter_call',
                status TEXT DEFAULT 'uploaded',
                transcript_text TEXT,
                summary_text TEXT,
                call_summary TEXT,
                transcription_model TEXT,
                summary_model TEXT,
                user_id INTEGER REFERENCES users(id)
            )
            """
        )
        # Idempotent migration for installs created before call_summary existed.
        # summary_text continues to hold the coaching analysis; call_summary is
        # the Bullhorn-ready note generated alongside the transcript.
        try:
            conn.execute("ALTER TABLE calls ADD COLUMN call_summary TEXT")
        except sqlite3.OperationalError:
            pass  # column already exists
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
        conn.commit()
    finally:
        conn.close()


# -----------------------------
# User CRUD
# -----------------------------
def insert_user(record: Dict[str, Any]) -> int:
    conn = get_conn()
    try:
        cursor = conn.execute(
            """
            INSERT INTO users (email, display_name, password_hash, role, is_active, created_at, team)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["email"],
                record["display_name"],
                record["password_hash"],
                record.get("role", "recruiter"),
                record.get("is_active", 1),
                record.get("created_at", datetime.utcnow().isoformat()),
                record.get("team"),
            ),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_team_member_ids(team: str) -> List[int]:
    """Return active user IDs belonging to a team (used for manager scoping)."""
    if not team:
        return []
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id FROM users WHERE team = ? AND is_active = 1 ORDER BY id",
            (team,),
        ).fetchall()
        return [r["id"] for r in rows]
    finally:
        conn.close()


def get_user_by_email(email: str) -> Optional[sqlite3.Row]:
    conn = get_conn()
    try:
        return conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    finally:
        conn.close()


def get_user_by_id(user_id: int) -> Optional[sqlite3.Row]:
    conn = get_conn()
    try:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    finally:
        conn.close()


def get_all_users() -> List[sqlite3.Row]:
    conn = get_conn()
    try:
        return conn.execute("SELECT * FROM users ORDER BY id").fetchall()
    finally:
        conn.close()


def update_user(user_id: int, **fields) -> None:
    allowed = {"display_name", "role", "is_active", "password_hash", "team"}
    if not fields:
        return
    invalid = set(fields.keys()) - allowed
    if invalid:
        raise ValueError(f"Disallowed user column(s): {invalid}")
    set_clause = ", ".join([f"{k} = ?" for k in fields.keys()])
    values = list(fields.values()) + [user_id]
    conn = get_conn()
    try:
        conn.execute(f"UPDATE users SET {set_clause} WHERE id = ?", values)
        conn.commit()
    finally:
        conn.close()


# -----------------------------
# Call CRUD (user-scoped)
# -----------------------------
def insert_call(record: Dict[str, Any]) -> int:
    conn = get_conn()
    try:
        cursor = conn.execute(
            """
            INSERT INTO calls (
                created_at, original_filename, stored_filename, stored_path, mime_type,
                recruiter_name, subject_name, company_name, notes, call_type, status,
                transcript_text, summary_text, transcription_model, summary_model, user_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["created_at"],
                record["original_filename"],
                record["stored_filename"],
                record["stored_path"],
                record.get("mime_type"),
                record.get("recruiter_name"),
                record.get("subject_name"),
                record.get("company_name"),
                record.get("notes"),
                record.get("call_type", "general_recruiter_call"),
                record.get("status", "uploaded"),
                record.get("transcript_text"),
                record.get("summary_text"),
                record.get("transcription_model"),
                record.get("summary_model"),
                record.get("user_id"),
            ),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


_ALLOWED_COLUMNS = {
    "recruiter_name", "subject_name", "company_name", "notes", "call_type",
    "status", "transcript_text", "summary_text", "call_summary",
    "transcription_model", "summary_model", "user_id",
}


def update_call(call_id: int, **fields) -> None:
    if not fields:
        return
    invalid = set(fields.keys()) - _ALLOWED_COLUMNS
    if invalid:
        raise ValueError(f"Attempted update with disallowed column(s): {invalid}")
    set_clause = ", ".join([f"{k} = ?" for k in fields.keys()])
    values = list(fields.values()) + [call_id]
    conn = get_conn()
    try:
        conn.execute(f"UPDATE calls SET {set_clause} WHERE id = ?", values)
        conn.commit()
    finally:
        conn.close()


def _user_scope_clause(user_ids):
    """Build a SQL fragment for filtering by a list of user IDs.

    Returns (sql_fragment, values_tuple). user_ids semantics:
      - None  -> no filter (admin view): returns ("", ())
      - []    -> match nothing (manager whose team has no members yet)
      - list  -> WHERE user_id IN (?, ?, ...)
    """
    if user_ids is None:
        return "", ()
    if not user_ids:
        return " AND 1=0", ()
    placeholders = ",".join("?" for _ in user_ids)
    return f" AND user_id IN ({placeholders})", tuple(user_ids)


def get_all_calls(user_ids=None, user_id=None) -> List[sqlite3.Row]:
    """Return calls. user_ids=None (admin) -> all calls. user_ids=[...] -> filtered.

    Accepts both the new `user_ids` list and the legacy `user_id` single int
    for backward compatibility with older tests / callers.
    """
    if user_ids is None and user_id is not None:
        user_ids = user_id
    if isinstance(user_ids, int):
        user_ids = [user_ids]

    conn = get_conn()
    try:
        if user_ids is None:
            return conn.execute("SELECT * FROM calls ORDER BY id DESC").fetchall()
        if not user_ids:
            return []
        placeholders = ",".join("?" for _ in user_ids)
        return conn.execute(
            f"SELECT * FROM calls WHERE user_id IN ({placeholders}) ORDER BY id DESC",
            tuple(user_ids),
        ).fetchall()
    finally:
        conn.close()


def get_call(call_id: int, user_ids=None, user_id=None) -> Optional[sqlite3.Row]:
    """Return a call by id, optionally filtered to a user-ID scope.

    Accepts the legacy `user_id` (single int) keyword for backward compat.
    """
    if user_ids is None and user_id is not None:
        user_ids = user_id
    if isinstance(user_ids, int):
        user_ids = [user_ids]

    conn = get_conn()
    try:
        if user_ids is None:
            return conn.execute("SELECT * FROM calls WHERE id = ?", (call_id,)).fetchone()
        if not user_ids:
            return None
        placeholders = ",".join("?" for _ in user_ids)
        return conn.execute(
            f"SELECT * FROM calls WHERE id = ? AND user_id IN ({placeholders})",
            (call_id, *user_ids),
        ).fetchone()
    finally:
        conn.close()


# -----------------------------
# API Usage tracking
# -----------------------------
def insert_api_usage(record: Dict[str, Any]) -> int:
    conn = get_conn()
    try:
        cursor = conn.execute(
            """
            INSERT INTO api_usage (user_id, call_id, operation, model, estimated_cost_cents, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                record["user_id"],
                record.get("call_id"),
                record["operation"],
                record["model"],
                record.get("estimated_cost_cents", 0),
                record.get("created_at", datetime.utcnow().isoformat()),
            ),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_api_usage(user_id: Optional[int] = None, days: int = 30) -> List[sqlite3.Row]:
    conn = get_conn()
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    try:
        if user_id is not None:
            return conn.execute(
                """
                SELECT au.*, u.display_name
                FROM api_usage au
                JOIN users u ON au.user_id = u.id
                WHERE au.user_id = ? AND au.created_at >= ?
                ORDER BY au.created_at DESC
                """,
                (user_id, cutoff),
            ).fetchall()
        return conn.execute(
            """
            SELECT au.*, u.display_name
            FROM api_usage au
            JOIN users u ON au.user_id = u.id
            WHERE au.created_at >= ?
            ORDER BY au.created_at DESC
            """,
            (cutoff,),
        ).fetchall()
    finally:
        conn.close()


def get_usage_summary(days: int = 30, user_ids=None) -> List[sqlite3.Row]:
    """Get per-user cost summary for the admin dashboard, optionally team-scoped."""
    if isinstance(user_ids, int):
        user_ids = [user_ids]

    conn = get_conn()
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    try:
        if user_ids is None:
            return conn.execute(
                """
                SELECT
                    u.display_name,
                    u.email,
                    COUNT(au.id) as total_calls,
                    SUM(CASE WHEN au.operation = 'transcription' THEN 1 ELSE 0 END) as transcriptions,
                    SUM(CASE WHEN au.operation = 'summarization' THEN 1 ELSE 0 END) as summarizations,
                    SUM(au.estimated_cost_cents) as total_cost_cents
                FROM api_usage au
                JOIN users u ON au.user_id = u.id
                WHERE au.created_at >= ?
                GROUP BY au.user_id
                ORDER BY total_cost_cents DESC
                """,
                (cutoff,),
            ).fetchall()
        if not user_ids:
            return []
        placeholders = ",".join("?" for _ in user_ids)
        return conn.execute(
            f"""
            SELECT
                u.display_name,
                u.email,
                COUNT(au.id) as total_calls,
                SUM(CASE WHEN au.operation = 'transcription' THEN 1 ELSE 0 END) as transcriptions,
                SUM(CASE WHEN au.operation = 'summarization' THEN 1 ELSE 0 END) as summarizations,
                SUM(au.estimated_cost_cents) as total_cost_cents
            FROM api_usage au
            JOIN users u ON au.user_id = u.id
            WHERE au.created_at >= ? AND au.user_id IN ({placeholders})
            GROUP BY au.user_id
            ORDER BY total_cost_cents DESC
            """,
            (cutoff, *user_ids),
        ).fetchall()
    finally:
        conn.close()
