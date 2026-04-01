"""CloudCall API client and recording import pipeline.

Handles OAuth2 token refresh, downloading recordings from CloudCall,
and importing them into the existing transcription/summarization pipeline.

Auth flow:
  POST https://auth.cloudcall.com/connect/token
  Body: client_id=o1-public-api&grant_type=refresh_token&refresh_token=<token>
  Returns: access_token (24hr), new refresh_token
"""

import logging
import os
from datetime import datetime
from pathlib import Path
from time import time
from typing import Dict, Any, Optional

import httpx

from config import CONVERTED_DIR
from cloudcall_config import (
    CLOUDCALL_API_BASE_URL,
    CLOUDCALL_AUTH_URL,
    CLOUDCALL_CLIENT_ID,
    CLOUDCALL_REFRESH_TOKEN,
    CLOUDCALL_DOWNLOAD_DIR,
    CLOUDCALL_RECORDING_RETENTION_HOURS,
    CLOUDCALL_POLL_INTERVAL_MINUTES,
)
from cloudcall_db import (
    get_cloudcall_recording,
    insert_cloudcall_recording,
    update_cloudcall_recording,
    delete_expired_cloudcall_recordings,
    get_cloudcall_user_mapping,
)
from db import insert_call
from file_service import convert_file_to_wav

logger = logging.getLogger("calltranscription.cloudcall")

# Throttle cleanup to max once per 15 minutes
_last_cleanup_time: float = 0.0
_CLEANUP_INTERVAL_SECONDS = 900

# In-memory token cache (per-process)
_access_token: str = ""
_access_token_expires_at: float = 0.0

# ---------------------------------------------------------------------------
# Token persistence: shared across poller + Streamlit via SQLite
# ---------------------------------------------------------------------------
import json
import sqlite3
from config import DB_PATH


def _get_stored_tokens() -> Dict[str, Any]:
    """Read the current tokens from the DB (shared across processes)."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS cloudcall_tokens ("
            "  id INTEGER PRIMARY KEY CHECK (id = 1),"
            "  access_token TEXT, refresh_token TEXT,"
            "  expires_at REAL"
            ")"
        )
        row = conn.execute("SELECT access_token, refresh_token, expires_at FROM cloudcall_tokens WHERE id = 1").fetchone()
        if row:
            return {"access_token": row[0] or "", "refresh_token": row[1] or "", "expires_at": row[2] or 0.0}
        return {"access_token": "", "refresh_token": "", "expires_at": 0.0}
    finally:
        conn.close()


def _store_tokens(access_token: str, refresh_token: str, expires_at: float):
    """Persist tokens to DB so all processes share them."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS cloudcall_tokens ("
            "  id INTEGER PRIMARY KEY CHECK (id = 1),"
            "  access_token TEXT, refresh_token TEXT,"
            "  expires_at REAL"
            ")"
        )
        conn.execute(
            "INSERT OR REPLACE INTO cloudcall_tokens (id, access_token, refresh_token, expires_at) "
            "VALUES (1, ?, ?, ?)",
            (access_token, refresh_token, expires_at),
        )
        conn.commit()
    finally:
        conn.close()


def get_access_token() -> str:
    """Get a valid CloudCall access token, refreshing if needed.

    Tokens are shared across processes (poller + Streamlit) via SQLite.
    The refresh token is single-use: each refresh returns a new one.
    """
    global _access_token, _access_token_expires_at

    # Return in-memory cached token if still valid (with 5 min buffer)
    if _access_token and time() < (_access_token_expires_at - 300):
        return _access_token

    # Check DB for a token another process may have refreshed
    stored = _get_stored_tokens()
    if stored["access_token"] and time() < (stored["expires_at"] - 300):
        _access_token = stored["access_token"]
        _access_token_expires_at = stored["expires_at"]
        return _access_token

    # Need to refresh — use stored refresh token or env var as fallback
    current_refresh = stored["refresh_token"] or CLOUDCALL_REFRESH_TOKEN
    if not current_refresh:
        raise RuntimeError("CLOUDCALL_REFRESH_TOKEN is not configured")

    with httpx.Client(timeout=30.0) as client:
        response = client.post(
            CLOUDCALL_AUTH_URL,
            data={
                "client_id": CLOUDCALL_CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": current_refresh,
            },
        )
        response.raise_for_status()

    token_data = response.json()
    _access_token = token_data["access_token"]
    _access_token_expires_at = time() + token_data.get("expires_in", 86400)
    new_refresh = token_data.get("refresh_token", current_refresh)

    # Persist so other processes can use the new tokens
    _store_tokens(_access_token, new_refresh, _access_token_expires_at)

    logger.info("CloudCall access token refreshed, expires in %ds", token_data.get("expires_in", 0))
    return _access_token


def download_recording(recording_url: str, cloudcall_recording_id: str) -> Path:
    """Download a recording from CloudCall.

    CloudCall webhook recording URLs are temporary pre-signed URLs,
    so they may not need auth headers. We try without auth first,
    then fall back to Bearer token auth if that fails.

    Args:
        recording_url: The URL to download the recording from.
        cloudcall_recording_id: Used as the filename stem.

    Returns:
        Path to the downloaded file.
    """
    with httpx.Client(timeout=120.0) as client:
        # Try direct download first (temp URLs are often pre-signed)
        response = client.get(recording_url, follow_redirects=True)

        if response.status_code in (401, 403):
            # Fall back to authenticated download
            token = get_access_token()
            response = client.get(
                recording_url,
                headers={"Authorization": f"Bearer {token}"},
                follow_redirects=True,
            )

        response.raise_for_status()

    # Determine file extension from content-type or URL
    content_type = response.headers.get("content-type", "")
    if "wav" in content_type or recording_url.endswith(".wav"):
        ext = ".wav"
    elif "mp3" in content_type or "mpeg" in content_type or recording_url.endswith(".mp3"):
        ext = ".mp3"
    elif "mp4" in content_type or recording_url.endswith(".mp4"):
        ext = ".mp4"
    elif "ogg" in content_type or recording_url.endswith(".ogg"):
        ext = ".ogg"
    else:
        ext = ".mp3"  # Default assumption for phone recordings

    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in cloudcall_recording_id)
    download_path = CLOUDCALL_DOWNLOAD_DIR / f"{safe_id}{ext}"

    with open(download_path, "wb") as f:
        f.write(response.content)

    if download_path.stat().st_size == 0:
        download_path.unlink(missing_ok=True)
        raise RuntimeError("Downloaded recording is empty (0 bytes)")

    logger.info("Downloaded recording %s (%d bytes)", cloudcall_recording_id, download_path.stat().st_size)
    return download_path


def import_recording_to_pipeline(
    cloudcall_recording_db_id: int,
    user_id: int,
    call_type: str = "general_recruiter_call",
    metadata: Optional[Dict[str, Any]] = None,
) -> int:
    """Download, convert, and import a CloudCall recording into the calls table.

    Args:
        cloudcall_recording_db_id: The ID from cloudcall_recordings table.
        user_id: The app user_id to assign the call to.
        call_type: The call type for summarization.
        metadata: Optional dict with recruiter_name, subject_name, company_name, notes.

    Returns:
        The new call ID in the calls table.
    """
    metadata = metadata or {}
    recording = get_cloudcall_recording(cloudcall_recording_db_id)
    if not recording:
        raise ValueError(f"CloudCall recording {cloudcall_recording_db_id} not found")

    if recording["status"] not in ("available", "error"):
        raise ValueError(
            f"Recording {cloudcall_recording_db_id} has status '{recording['status']}' — "
            "only 'available' or 'error' recordings can be imported"
        )

    # Mark as importing
    update_cloudcall_recording(cloudcall_recording_db_id, status="importing")

    download_path = None
    try:
        # Download
        download_path = download_recording(
            recording["recording_url"],
            recording["cloudcall_recording_id"],
        )

        # Convert to WAV
        wav_path = convert_file_to_wav(download_path, CLOUDCALL_DOWNLOAD_DIR)

        # Build the original filename for display
        call_id_short = recording["cloudcall_recording_id"][:20]
        original_filename = f"cloudcall_{call_id_short}{download_path.suffix}"

        # Store relative path from project root
        relative_path = wav_path.relative_to(Path(__file__).parent)

        # Insert into calls table
        call_record = {
            "created_at": datetime.utcnow().isoformat(),
            "original_filename": original_filename,
            "stored_filename": wav_path.name,
            "stored_path": str(relative_path),
            "mime_type": "audio/wav",
            "recruiter_name": metadata.get("recruiter_name", ""),
            "subject_name": metadata.get("subject_name", ""),
            "company_name": metadata.get("company_name", ""),
            "notes": metadata.get("notes", "Imported from CloudCall"),
            "call_type": call_type,
            "status": "uploaded",
            "user_id": user_id,
        }
        new_call_id = insert_call(call_record)

        # Update cloudcall_recordings with success
        update_cloudcall_recording(
            cloudcall_recording_db_id,
            status="imported",
            imported_call_id=new_call_id,
        )

        logger.info(
            "Imported CloudCall recording %d as call_id=%d for user_id=%d",
            cloudcall_recording_db_id, new_call_id, user_id,
        )
        return new_call_id

    except Exception as e:
        update_cloudcall_recording(
            cloudcall_recording_db_id,
            status="error",
            error_message=str(e)[:500],
        )
        logger.error(
            "Import failed for CloudCall recording %d: %s",
            cloudcall_recording_db_id, e,
        )
        raise
    finally:
        # Clean up temp download
        if download_path and download_path.exists():
            download_path.unlink(missing_ok=True)


def maybe_cleanup_expired():
    """Run expired recording cleanup if enough time has passed since last run."""
    global _last_cleanup_time
    now = time()
    if now - _last_cleanup_time >= _CLEANUP_INTERVAL_SECONDS:
        _last_cleanup_time = now
        try:
            deleted = delete_expired_cloudcall_recordings(CLOUDCALL_RECORDING_RETENTION_HOURS)
            if deleted > 0:
                logger.info("Cleanup: deleted %d expired CloudCall recordings", deleted)
            return deleted
        except Exception as e:
            logger.error("Cleanup failed: %s", e)
            return 0
    return 0


# ---------------------------------------------------------------------------
# Polling: fetch recorded calls from CloudCall Call Logs API
# ---------------------------------------------------------------------------


def fetch_call_logs(from_dt: str, to_dt: str, page: int = 1, rows: int = 100) -> list:
    """Fetch call logs from CloudCall API, filtered to recorded calls only.

    Args:
        from_dt: Start datetime ISO string (e.g. "2026-03-31T06:00:00")
        to_dt: End datetime ISO string
        page: Page number (1-based)
        rows: Results per page

    Returns:
        List of call log dicts from CloudCall API.
    """
    token = get_access_token()
    with httpx.Client(timeout=30.0) as client:
        response = client.post(
            f"{CLOUDCALL_API_BASE_URL}/report/call_logs",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "from": from_dt,
                "to": to_dt,
                "pagination": {"rows": rows, "pgnum": page},
                "order_by": [{"dimension": "created_on", "order": "DESC"}],
                "filter": [
                    {"dimension": "is_recorded", "logical_op": "AND", "values": ["1"]},
                ],
            },
        )
        response.raise_for_status()

    data = response.json()
    return data.get("Data", [])


def get_recording_url(user_call_data_id: str) -> Optional[str]:
    """Fetch the temporary recording URL for a given call.

    Args:
        user_call_data_id: The call's user_call_data_id from call logs.

    Returns:
        The recording URL string, or None if not available.
    """
    token = get_access_token()
    with httpx.Client(timeout=30.0) as client:
        response = client.get(
            f"{CLOUDCALL_API_BASE_URL}/core/v4/recordings/url/{user_call_data_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()

    data = response.json()
    recordings = data.get("recordings", [])
    if recordings:
        return recordings[0].get("url")
    return None


def poll_recent_recordings(lookback_minutes: int = None) -> int:
    """Poll CloudCall for recent recorded calls and insert new ones.

    Fetches call logs from the last `lookback_minutes`, gets recording URLs
    for each, and inserts them into cloudcall_recordings (skipping duplicates).

    Args:
        lookback_minutes: How far back to look. Defaults to 2x poll interval
                         to ensure overlap and no missed calls.

    Returns:
        Number of new recordings inserted.
    """
    if lookback_minutes is None:
        lookback_minutes = CLOUDCALL_POLL_INTERVAL_MINUTES * 2

    now = datetime.utcnow()
    from_dt = (now - __import__("datetime").timedelta(minutes=lookback_minutes)).strftime("%Y-%m-%dT%H:%M:%S")
    to_dt = now.strftime("%Y-%m-%dT%H:%M:%S")

    logger.info("Polling CloudCall call logs from %s to %s", from_dt, to_dt)

    try:
        calls = fetch_call_logs(from_dt, to_dt)
    except Exception as e:
        logger.error("Failed to fetch call logs: %s", e)
        return 0

    if not calls:
        logger.info("No recorded calls found in polling window")
        return 0

    inserted = 0
    for call in calls:
        call_data_id = call.get("user_call_data_id", "")
        if not call_data_id:
            continue

        # Skip calls with 0 recording duration (voicemails without actual recordings)
        if call.get("recording_duration", 0) == 0:
            continue

        # Fetch recording URL
        try:
            rec_url = get_recording_url(call_data_id)
        except Exception as e:
            logger.warning("Failed to get recording URL for %s: %s", call_data_id, e)
            continue

        if not rec_url:
            continue

        # Map CloudCall user to app user
        cloudcall_user_id = call.get("cloudcall_user_id", "")
        app_user_id = None
        if cloudcall_user_id:
            mapping = get_cloudcall_user_mapping(cloudcall_user_id)
            if not mapping:
                # Try email fallback
                email = call.get("email", "")
                if email:
                    mapping = get_cloudcall_user_mapping(email)
            if mapping:
                app_user_id = mapping["app_user_id"]

        # Insert (idempotent — duplicates are silently skipped)
        try:
            insert_cloudcall_recording({
                "cloudcall_recording_id": call_data_id,
                "cloudcall_user_id": cloudcall_user_id,
                "app_user_id": app_user_id,
                "recording_url": rec_url,
                "recruiter_name": call.get("user_name", ""),
                "contact_name": call.get("contact_name", ""),
                "caller_number": call.get("user_number", ""),
                "callee_number": call.get("contact_number", ""),
                "direction": call.get("direction", ""),
                "call_duration_seconds": call.get("duration"),
                "call_timestamp": call.get("created_on", now.isoformat()),
                "status": "available",
                "webhook_received_at": now.isoformat(),
                "webhook_payload": "",
            })
            inserted += 1
            logger.info(
                "Polled recording: %s user=%s (%s) contact=%s duration=%ds",
                call_data_id, call.get("user_name", ""), cloudcall_user_id,
                call.get("contact_name", ""), call.get("duration", 0),
            )
        except Exception as e:
            if "UNIQUE constraint" in str(e):
                pass  # Already exists, expected for overlap window
            else:
                logger.error("Failed to insert polled recording %s: %s", call_data_id, e)

    logger.info("Polling complete: %d new recordings inserted from %d calls", inserted, len(calls))

    # Opportunistic cleanup
    maybe_cleanup_expired()

    return inserted
