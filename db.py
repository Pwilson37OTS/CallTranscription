import sqlite3
from typing import Dict, Any, Optional, List

from config import DB_PATH


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_conn()
    try:
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
                transcription_model TEXT,
                summary_model TEXT
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def insert_call(record: Dict[str, Any]) -> int:
    conn = get_conn()
    try:
        cursor = conn.execute(
            """
            INSERT INTO calls (
                created_at, original_filename, stored_filename, stored_path, mime_type,
                recruiter_name, subject_name, company_name, notes, call_type, status,
                transcript_text, summary_text, transcription_model, summary_model
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            ),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


_ALLOWED_COLUMNS = {
    "recruiter_name", "subject_name", "company_name", "notes", "call_type",
    "status", "transcript_text", "summary_text", "transcription_model", "summary_model",
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


def get_all_calls() -> List[sqlite3.Row]:
    conn = get_conn()
    try:
        return conn.execute("SELECT * FROM calls ORDER BY id DESC").fetchall()
    finally:
        conn.close()


def get_call(call_id: int) -> Optional[sqlite3.Row]:
    conn = get_conn()
    try:
        return conn.execute("SELECT * FROM calls WHERE id = ?", (call_id,)).fetchone()
    finally:
        conn.close()
