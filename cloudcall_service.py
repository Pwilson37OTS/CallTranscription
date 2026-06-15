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

    def _do_refresh(refresh_token: str, source: str) -> Dict[str, Any]:
        with httpx.Client(timeout=30.0) as client:
            r = client.post(
                CLOUDCALL_AUTH_URL,
                data={
                    "client_id": CLOUDCALL_CLIENT_ID,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
            )
        if r.status_code != 200:
            body = (r.text or "")[:500]
            raise RuntimeError(
                f"CloudCall token refresh rejected (source={source}, status={r.status_code}): {body}"
            )
        return r.json()

    source = "stored" if stored["refresh_token"] else "env"
    try:
        token_data = _do_refresh(current_refresh, source)
    except RuntimeError as e:
        # Common case: the DB-stored token got rotated/invalidated but the
        # original env-var bootstrap is still valid. Retry once with that.
        if (
            source == "stored"
            and CLOUDCALL_REFRESH_TOKEN
            and CLOUDCALL_REFRESH_TOKEN != current_refresh
        ):
            logger.warning("Stored refresh token rejected; retrying with env-var bootstrap. Original error: %s", e)
            token_data = _do_refresh(CLOUDCALL_REFRESH_TOKEN, "env-fallback")
        else:
            raise
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

    # Validate that what came back is actually audio. CloudCall has been
    # observed to return small HTML error pages or placeholder bodies for
    # calls where the audio file doesn't actually exist on their side
    # (their UI shows "an audio file does not exist" for these). The HTTP
    # status is 200 so response.raise_for_status() lets it through, but
    # the payload is junk and import_recording_to_pipeline used to mark
    # these as 'imported' — they'd then appear in the Calls dropdown but
    # transcription would fail (or produce garbage). Catch them here so
    # they get tagged status="no_audio" by the import exception handler.
    content_type = (response.headers.get("content-type") or "").lower()
    body_size = len(response.content)

    # Text / JSON / XML payloads are never audio.
    if content_type.startswith(("text/", "application/json", "application/xml", "application/problem+json")):
        raise RuntimeError(
            f"Recording has no audio content (content-type={content_type or 'unknown'}, "
            f"size={body_size} bytes)"
        )

    # Any reasonable phone recording is at least a few KB. Sub-1KB
    # responses are almost certainly placeholders or error stubs that
    # slipped past the content-type check.
    if body_size < 1024:
        raise RuntimeError(
            f"Recording has no audio content (only {body_size} bytes returned)"
        )

    # Determine file extension from content-type or URL
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
    call_type: str = "standard_call",
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
        # CloudCall recording URLs are S3 pre-signed URLs valid for ~10 min.
        # Always fetch a fresh URL at import time — the one stored at poll time
        # is almost certainly expired by the time a user clicks Import.
        fresh_url = get_recording_url(recording["cloudcall_recording_id"])
        if not fresh_url:
            raise RuntimeError(
                "Recording URL no longer available from CloudCall — "
                "the call may have been deleted or retention expired."
            )

        download_path = download_recording(
            fresh_url,
            recording["cloudcall_recording_id"],
        )

        # CloudCall recordings arrive in OpenAI-compatible formats (mp3/mp4/
        # wav/ogg/m4a). Skip the ffmpeg conversion to WAV — that step ~5x'd
        # the on-disk size of every call without adding any value for either
        # transcription or playback. Move the original into CONVERTED_DIR
        # (the "official" audio location) and use it as-is.
        import shutil
        final_path = CONVERTED_DIR / download_path.name
        if final_path.exists():
            final_path.unlink()  # in case of retry collisions
        shutil.move(str(download_path), str(final_path))
        download_path = None  # don't double-delete in finally

        # Pick a mime type from the extension
        ext = final_path.suffix.lower()
        mime_map = {
            ".mp3":  "audio/mpeg",
            ".mp4":  "audio/mp4",
            ".m4a":  "audio/mp4",
            ".wav":  "audio/wav",
            ".ogg":  "audio/ogg",
            ".webm": "audio/webm",
            ".flac": "audio/flac",
        }
        mime_type = mime_map.get(ext, "audio/mpeg")

        call_id_short = recording["cloudcall_recording_id"][:20]
        original_filename = f"cloudcall_{call_id_short}{final_path.suffix}"

        relative_path = final_path.relative_to(Path(__file__).parent)

        # Insert into calls table
        call_record = {
            "created_at": datetime.utcnow().isoformat(),
            "original_filename": original_filename,
            "stored_filename": final_path.name,
            "stored_path": str(relative_path),
            "mime_type": mime_type,
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
        # Distinguish "the audio file genuinely doesn't exist" from
        # everything else (network blips, conversion errors, etc.) so the
        # Call Log can show a clear "Missing recording" label and disable
        # the retry button — there's nothing to retry when CloudCall has
        # no audio.
        err_str = str(e)
        err_lower = err_str.lower()
        no_audio_markers = (
            "no longer available",
            "empty (0 bytes)",
            "0 bytes",
            "no audio content",  # raised by download_recording when payload is invalid
        )
        is_missing_audio = any(m in err_lower for m in no_audio_markers)
        final_status = "no_audio" if is_missing_audio else "error"
        update_cloudcall_recording(
            cloudcall_recording_db_id,
            status=final_status,
            error_message=err_str[:500],
        )
        logger.error(
            "Import failed for CloudCall recording %d (status=%s): %s",
            cloudcall_recording_db_id, final_status, e,
        )
        raise
    finally:
        # Clean up temp download
        if download_path and download_path.exists():
            download_path.unlink(missing_ok=True)


def cleanup_old_files(retention_hours: int = None) -> int:
    """Delete files older than retention_hours across all data directories.

    Covers data/converted/, data/uploads/, data/cloudcall_downloads/, and
    rotated log backups (data/logs/*.log.*). The active app.log is left
    alone — RotatingFileHandler manages it. Returns the count removed.
    """
    from config import CONVERTED_DIR, UPLOAD_DIR, DATA_DIR

    if retention_hours is None:
        retention_hours = CLOUDCALL_RECORDING_RETENTION_HOURS

    dirs_to_clean = [CONVERTED_DIR, UPLOAD_DIR, CLOUDCALL_DOWNLOAD_DIR]
    cutoff_seconds = time() - (retention_hours * 3600)
    removed = 0
    bytes_freed = 0

    for d in dirs_to_clean:
        if not d.exists():
            continue
        for f in d.iterdir():
            try:
                if f.is_file() and f.stat().st_mtime < cutoff_seconds:
                    size = f.stat().st_size
                    f.unlink()
                    removed += 1
                    bytes_freed += size
            except OSError as e:
                logger.warning("Failed to remove %s: %s", f, e)

    # Rotated log backups (app.log.1, app.log.2, ...). Skip the active
    # app.log so we don't disrupt the running logger.
    log_dir = DATA_DIR / "logs"
    if log_dir.exists():
        for f in log_dir.iterdir():
            try:
                if (
                    f.is_file()
                    and f.name != "app.log"
                    and f.stat().st_mtime < cutoff_seconds
                ):
                    size = f.stat().st_size
                    f.unlink()
                    removed += 1
                    bytes_freed += size
            except OSError as e:
                logger.warning("Failed to remove %s: %s", f, e)

    if removed > 0:
        logger.info(
            "File cleanup: removed %d file(s), freed %.1f MB",
            removed, bytes_freed / (1024 * 1024),
        )
    return removed


def maybe_cleanup_expired(force: bool = False):
    """Run expired recording + audio file cleanup if enough time has passed.

    Pass force=True to bypass the 15-minute throttle (used by the admin
    "Force Cleanup Now" button).
    """
    global _last_cleanup_time
    now = time()
    if force or (now - _last_cleanup_time >= _CLEANUP_INTERVAL_SECONDS):
        _last_cleanup_time = now
        try:
            deleted = delete_expired_cloudcall_recordings(CLOUDCALL_RECORDING_RETENTION_HOURS)
            if deleted > 0:
                logger.info("DB cleanup: deleted %d expired CloudCall recording rows", deleted)
            removed = cleanup_old_files(CLOUDCALL_RECORDING_RETENTION_HOURS)
            return {"db_rows_deleted": deleted, "files_removed": removed}
        except Exception as e:
            logger.error("Cleanup failed: %s", e)
            return {"db_rows_deleted": 0, "files_removed": 0, "error": str(e)}
    return {"db_rows_deleted": 0, "files_removed": 0}


def get_storage_breakdown() -> list:
    """Return size + file count for each data directory and the SQLite DB."""
    from config import DATA_DIR, UPLOAD_DIR, CONVERTED_DIR, DB_PATH

    items = []

    # Database file (plus any WAL/SHM sidecars)
    db_total = 0
    db_files = 0
    for stem in [DB_PATH, DB_PATH.with_suffix(DB_PATH.suffix + "-wal"), DB_PATH.with_suffix(DB_PATH.suffix + "-shm")]:
        if stem.exists():
            db_total += stem.stat().st_size
            db_files += 1
    items.append({
        "path": "data/calls.db (+sidecars)",
        "files": db_files,
        "size_bytes": db_total,
    })

    dirs_to_check = [
        ("data/converted", CONVERTED_DIR),
        ("data/uploads", UPLOAD_DIR),
        ("data/cloudcall_downloads", CLOUDCALL_DOWNLOAD_DIR),
        ("data/logs", DATA_DIR / "logs"),
    ]
    for label, d in dirs_to_check:
        size = 0
        count = 0
        if d.exists():
            for f in d.iterdir():
                if f.is_file():
                    try:
                        size += f.stat().st_size
                        count += 1
                    except OSError:
                        pass
        items.append({"path": label, "files": count, "size_bytes": size})

    return items


def retry_pending_url_recordings(age_out_hours: int = 24) -> dict:
    """Retry the URL fetch for every cloudcall_recordings row stuck at
    status='pending_url' (calls whose URL wasn't available at first poll).

    For each pending row:
      - If get_recording_url now returns a URL: update status to 'available',
        store the URL, and trigger auto-import if a user mapping exists.
      - If get_recording_url returns cleanly with no URL: CloudCall confirmed
        no audio file, transition to 'no_audio'.
      - If get_recording_url still throws AND the row is older than
        age_out_hours: give up, transition to 'no_audio' with a clear
        error message.
      - Otherwise: leave at pending_url for the next poll.

    Called at the start of every poll_recent_recordings() pass so that
    long-finalizing recordings (long calls, transient CloudCall errors,
    etc.) get retried indefinitely rather than silently dropped after
    the auto-poll's lookback window slides past them.

    Returns counts: retried, succeeded, aged_out, still_pending.
    """
    from cloudcall_db import (
        get_conn as _cc_get_conn,
        update_cloudcall_recording,
    )

    cc_conn = _cc_get_conn()
    try:
        rows = cc_conn.execute(
            "SELECT * FROM cloudcall_recordings WHERE status = 'pending_url'"
        ).fetchall()
    finally:
        cc_conn.close()

    if not rows:
        return {"retried": 0, "succeeded": 0, "aged_out": 0, "still_pending": 0}

    retried = 0
    succeeded = 0
    aged_out = 0
    still_pending = 0
    now = datetime.utcnow()
    age_out_seconds = age_out_hours * 3600

    for row in rows:
        retried += 1
        rec_id = row["id"]
        cc_recording_id = row["cloudcall_recording_id"]

        rec_url = None
        url_error = None
        try:
            rec_url = get_recording_url(cc_recording_id)
        except Exception as e:
            url_error = str(e)

        if rec_url:
            # Success — CloudCall now has the URL ready.
            update_cloudcall_recording(
                rec_id,
                status="available",
                recording_url=rec_url,
                error_message="",
            )
            succeeded += 1
            logger.info(
                "Pending URL retry succeeded: rec_id=%d cc_id=%s",
                rec_id, cc_recording_id,
            )

            # Auto-import if the recording is mapped to an app user.
            if row["app_user_id"]:
                try:
                    contact_label = (
                        row["contact_name"] or row["callee_number"] or ""
                    ).strip()
                    import_recording_to_pipeline(
                        cloudcall_recording_db_id=rec_id,
                        user_id=row["app_user_id"],
                        metadata={
                            "recruiter_name": row["recruiter_name"] or "",
                            "subject_name": contact_label,
                        },
                    )
                except Exception as e:
                    logger.warning(
                        "Auto-import failed after pending_url retry (rec_id=%d): %s",
                        rec_id, e,
                    )
            continue

        if url_error is None:
            # Clean 200 response from CloudCall with no URL in the body.
            # That's their definitive "no audio file" signal for this call.
            update_cloudcall_recording(
                rec_id,
                status="no_audio",
                error_message="CloudCall returned no recording URL on retry",
            )
            aged_out += 1
            continue

        # URL fetch threw — check age and decide whether to keep waiting.
        try:
            received_at = datetime.fromisoformat(row["webhook_received_at"])
            age_secs = (now - received_at).total_seconds()
        except Exception:
            age_secs = 0

        if age_secs > age_out_seconds:
            update_cloudcall_recording(
                rec_id,
                status="no_audio",
                error_message=(
                    f"URL never became available after {age_out_hours}h. "
                    f"Last attempt: {url_error[:200]}"
                ),
            )
            aged_out += 1
        else:
            still_pending += 1

    logger.info(
        "Pending URL retry pass: %d processed (%d succeeded, %d aged out to no_audio, %d still pending)",
        retried, succeeded, aged_out, still_pending,
    )
    return {
        "retried": retried,
        "succeeded": succeeded,
        "aged_out": aged_out,
        "still_pending": still_pending,
    }


def try_ingest_calls(calls: list) -> list:
    """Attempt to ingest a list of CloudCall call dicts. Reports per-call result.

    Used by the admin Call Inspector to forensically retry specific calls
    that didn't make it through the regular poll. Surfaces the exact reason
    each call failed (URL-fetch exception, no_audio, insert error, etc.)
    so admins can diagnose CloudCall-side issues directly.

    Each call dict should be a raw entry as returned by fetch_call_logs /
    inspect_call_logs. Returns a list of result dicts: { call_data_id,
    user_name, contact_name, success, status, message }.
    """
    from cloudcall_db import (
        insert_cloudcall_recording,
        get_cloudcall_user_mapping,
        get_conn as _cc_get_conn,
    )

    # Pull the known-IDs set once so we can short-circuit duplicates fast.
    cc_conn = _cc_get_conn()
    try:
        known_ids = {
            r["cloudcall_recording_id"]
            for r in cc_conn.execute(
                "SELECT cloudcall_recording_id FROM cloudcall_recordings"
            ).fetchall()
        }
    finally:
        cc_conn.close()

    results = []
    now = datetime.utcnow()

    for call in calls:
        call_data_id = call.get("user_call_data_id", "")
        result = {
            "call_data_id": call_data_id,
            "user_name": call.get("user_name", ""),
            "contact_name": call.get("contact_name", ""),
            "duration": call.get("duration", 0),
            "success": False,
            "status": "",
            "message": "",
        }

        if not call_data_id:
            result["status"] = "skipped_no_call_id"
            result["message"] = "Call has no user_call_data_id"
            results.append(result)
            continue

        if call_data_id in known_ids:
            result["status"] = "already_known"
            result["message"] = "Already in DB — use Re-validate Stored Recordings if status looks wrong"
            results.append(result)
            continue

        # Fetch URL — this is the step that usually fails for the 'missing'
        # cases. Capture the exact exception so the admin can see it.
        rec_url = None
        url_error = None
        try:
            rec_url = get_recording_url(call_data_id)
        except Exception as e:
            url_error = str(e)

        if url_error:
            result["status"] = "url_fetch_error"
            result["message"] = f"get_recording_url raised: {url_error}"
            results.append(result)
            continue

        if rec_url:
            record_status = "available"
            url_to_store = rec_url
        else:
            record_status = "no_audio"
            url_to_store = ""

        # Resolve user mapping
        cloudcall_user_id = call.get("cloudcall_user_id", "")
        app_user_id = None
        if cloudcall_user_id:
            mapping = get_cloudcall_user_mapping(cloudcall_user_id)
            if not mapping:
                email = call.get("email", "")
                if email:
                    mapping = get_cloudcall_user_mapping(email)
            if mapping:
                app_user_id = mapping["app_user_id"]

        # Insert
        recording_db_id = None
        try:
            recording_db_id = insert_cloudcall_recording({
                "cloudcall_recording_id": call_data_id,
                "cloudcall_user_id": cloudcall_user_id,
                "app_user_id": app_user_id,
                "recording_url": url_to_store,
                "recruiter_name": call.get("user_name", ""),
                "contact_name": call.get("contact_name", ""),
                "caller_number": call.get("user_number", ""),
                "callee_number": call.get("contact_number", ""),
                "direction": call.get("direction", ""),
                "call_duration_seconds": call.get("duration"),
                "call_timestamp": call.get("created_on", now.isoformat()),
                "status": record_status,
                "webhook_received_at": now.isoformat(),
                "webhook_payload": "",
            })
            result["status"] = record_status
            result["message"] = f"Inserted with status={record_status}"
            result["success"] = True
        except Exception as e:
            if "UNIQUE constraint" in str(e):
                result["status"] = "already_known"
                result["message"] = "Race condition — already inserted by another process"
            else:
                result["status"] = "insert_error"
                result["message"] = f"Insert failed: {e}"
            results.append(result)
            continue

        # Try auto-import for mapped + available recordings
        if record_status == "available" and app_user_id and recording_db_id:
            try:
                contact_label = (
                    call.get("contact_name") or call.get("contact_number") or ""
                ).strip()
                new_call_id = import_recording_to_pipeline(
                    cloudcall_recording_db_id=recording_db_id,
                    user_id=app_user_id,
                    metadata={
                        "recruiter_name": call.get("user_name", ""),
                        "subject_name": contact_label,
                    },
                )
                result["message"] = f"Inserted and auto-imported as call #{new_call_id}"
            except Exception as e:
                result["message"] = (
                    f"Inserted as {record_status} but auto-import failed: {e}"
                )

        results.append(result)

    return results


def inspect_call_logs(
    from_dt: str,
    to_dt: str,
    include_unrecorded: bool = False,
    rows: int = 100,
    max_pages: int = 50,
) -> list:
    """Fetch raw CloudCall call_logs entries for admin diagnostic inspection.

    Returns the unfiltered list of call dicts CloudCall's API returns so an
    admin can compare what CloudCall is reporting against what ECHO has
    actually ingested. Used to investigate cases where one logical call
    in CloudCall appears as multiple short calls in ECHO (likely caused
    by CloudCall returning multiple user_call_data_id entries for a single
    call session — transfers, holds, conference legs, or chunked
    recordings).

    Args:
        from_dt, to_dt: ISO datetime strings ("YYYY-MM-DDTHH:MM:SS").
        include_unrecorded: If True, drop the is_recorded=1 filter so the
            results show every call CloudCall has in the window, not just
            ones flagged as recorded.
        rows: Page size.
        max_pages: Safety cap (50 pages = 5,000 calls).

    Returns:
        List of raw call dicts from the API.
    """
    all_calls: list = []
    token = get_access_token()

    base_payload: Dict[str, Any] = {
        "from": from_dt,
        "to": to_dt,
        "order_by": [{"dimension": "created_on", "order": "DESC"}],
    }
    if not include_unrecorded:
        base_payload["filter"] = [
            {"dimension": "is_recorded", "logical_op": "AND", "values": ["1"]},
        ]

    with httpx.Client(timeout=30.0) as client:
        for page in range(1, max_pages + 1):
            payload = dict(base_payload)
            payload["pagination"] = {"rows": rows, "pgnum": page}
            response = client.post(
                f"{CLOUDCALL_API_BASE_URL}/report/call_logs",
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
            )
            response.raise_for_status()
            page_calls = response.json().get("Data", []) or []
            if not page_calls:
                break
            all_calls.extend(page_calls)
            if len(page_calls) < rows:
                break

    logger.info(
        "inspect_call_logs: %d calls between %s and %s (include_unrecorded=%s)",
        len(all_calls), from_dt, to_dt, include_unrecorded,
    )
    return all_calls


def revalidate_imported_recordings() -> dict:
    """Audit every status='imported' CloudCall recording for a valid audio file.

    Used to clean up rows that were imported before download-time validation
    existed (or where CloudCall served a tiny/invalid placeholder). For each
    row we look up the linked calls.stored_path; if the file is missing or
    suspiciously small, the recording is marked status='no_audio', the
    linked calls row is deleted, and the on-disk file is removed.

    Returns dict with: checked, fixed, errors.
    """
    from cloudcall_db import (
        get_conn as _cc_get_conn,
        update_cloudcall_recording as _update_rec,
    )
    from db import get_call as _get_call, get_conn as _get_conn
    from config import APP_DIR

    MIN_AUDIO_BYTES = 1024  # match download-time threshold

    cc_conn = _cc_get_conn()
    try:
        rows = cc_conn.execute(
            "SELECT id, imported_call_id FROM cloudcall_recordings "
            "WHERE status = 'imported' AND imported_call_id IS NOT NULL"
        ).fetchall()
    finally:
        cc_conn.close()

    checked = 0
    fixed = 0
    errors = 0

    for row in rows:
        checked += 1
        rec_id = row["id"]
        call_id = row["imported_call_id"]

        try:
            call = _get_call(call_id)
            if not call:
                continue

            stored_path_str = call["stored_path"] if call["stored_path"] else ""
            if not stored_path_str:
                continue

            full_path = APP_DIR / stored_path_str
            file_invalid = False
            err_reason = ""

            if not full_path.exists():
                file_invalid = True
                err_reason = "audio file missing on disk"
            else:
                size = full_path.stat().st_size
                if size < MIN_AUDIO_BYTES:
                    file_invalid = True
                    err_reason = f"audio file too small ({size} bytes — likely a placeholder)"

            if not file_invalid:
                continue

            # Mark the CloudCall recording row as no_audio with the reason
            _update_rec(
                rec_id,
                status="no_audio",
                error_message=f"Re-validation: {err_reason}",
            )

            # Delete the linked calls row (it points at invalid audio; we
            # don't want this call appearing in any recruiter's dropdown).
            del_conn = _get_conn()
            try:
                del_conn.execute("DELETE FROM calls WHERE id = ?", (call_id,))
                del_conn.commit()
            finally:
                del_conn.close()

            # Remove the on-disk file if it's there
            try:
                if full_path.exists():
                    full_path.unlink()
            except OSError as ose:
                logger.warning("Could not delete file %s: %s", full_path, ose)

            fixed += 1
            logger.info(
                "Re-validation: rec_id=%d call_id=%d reason=%r -> status=no_audio, call row deleted",
                rec_id, call_id, err_reason,
            )
        except Exception as e:
            errors += 1
            logger.warning("Re-validation failed for recording id=%d: %s", rec_id, e)

    return {"checked": checked, "fixed": fixed, "errors": errors}


def reimport_unmapped_recordings() -> dict:
    """Find recordings that were polled before a CloudCall user mapping existed
    and now have a mapping. Attempt to import each one.

    Returns dict with: scanned, mappable, succeeded, failed.
    """
    from cloudcall_db import get_conn as _get_cc_conn, get_cloudcall_user_mapping, update_cloudcall_recording

    conn = _get_cc_conn()
    try:
        rows = conn.execute(
            "SELECT id, cloudcall_user_id FROM cloudcall_recordings "
            "WHERE status = 'available' AND app_user_id IS NULL"
        ).fetchall()
    finally:
        conn.close()

    scanned = len(rows)
    mappable = 0
    succeeded = 0
    failed = 0

    for row in rows:
        cc_user_id = row["cloudcall_user_id"]
        if not cc_user_id:
            continue
        mapping = get_cloudcall_user_mapping(cc_user_id)
        if not mapping:
            continue
        mappable += 1
        try:
            update_cloudcall_recording(row["id"], app_user_id=mapping["app_user_id"])
            import_recording_to_pipeline(
                cloudcall_recording_db_id=row["id"],
                user_id=mapping["app_user_id"],
                metadata={},
            )
            succeeded += 1
        except Exception as e:
            failed += 1
            logger.warning("Re-import failed for recording %d: %s", row["id"], e)

    return {
        "scanned": scanned,
        "mappable": mappable,
        "succeeded": succeeded,
        "failed": failed,
    }


def get_cloudcall_ingest_stats() -> dict:
    """Return counts useful for diagnosing why few calls are visible.

    Counts cloudcall_recordings rows by status, and counts unmapped recordings.
    """
    from cloudcall_db import get_conn as _get_cc_conn

    conn = _get_cc_conn()
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS c FROM cloudcall_recordings GROUP BY status"
        ).fetchall()
        by_status = {r["status"]: r["c"] for r in rows}

        unmapped = conn.execute(
            "SELECT COUNT(*) AS c FROM cloudcall_recordings "
            "WHERE status = 'available' AND app_user_id IS NULL"
        ).fetchone()["c"]

        total = conn.execute("SELECT COUNT(*) AS c FROM cloudcall_recordings").fetchone()["c"]
    finally:
        conn.close()

    return {
        "total_recordings": total,
        "by_status": by_status,
        "unmapped_available": unmapped,
    }


# ---------------------------------------------------------------------------
# Polling: fetch recorded calls from CloudCall Call Logs API
# ---------------------------------------------------------------------------


def fetch_call_logs(from_dt: str, to_dt: str, rows: int = 100, max_pages: int = 50) -> list:
    """Fetch ALL pages of call logs from CloudCall in the time range.

    Previously this only fetched page 1 (100 results). With multiple
    recruiters making 14+ recorded calls a day each, a 72-hour window
    can easily contain 300+ calls. Results are ordered DESC by created_on,
    so page 1 = the 100 most recent calls; older calls in the window were
    silently dropped, even when CloudCall had them and we asked for them.

    Now we loop pages 1..max_pages, stopping when a page returns fewer
    than `rows` results (= last page) or when max_pages is hit (safety
    cap to prevent runaway loops).

    Args:
        from_dt: Start datetime ISO string (e.g. "2026-03-31T06:00:00")
        to_dt: End datetime ISO string
        rows: Page size (CloudCall limit applies — 100 is typical max)
        max_pages: Hard cap to avoid infinite loops; 50 pages = 5,000 calls.

    Returns:
        List of all call log dicts in the window.
    """
    all_calls: list = []
    token = get_access_token()

    with httpx.Client(timeout=30.0) as client:
        for page in range(1, max_pages + 1):
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
            page_calls = response.json().get("Data", []) or []
            if not page_calls:
                break
            all_calls.extend(page_calls)
            if len(page_calls) < rows:
                # Partial page = last page; stop here.
                break
        else:
            # Hit max_pages without an early break. Log so we know to bump
            # the cap if real call volume ever exceeds 5,000 in the window.
            logger.warning(
                "fetch_call_logs hit max_pages=%d; some calls may have been dropped",
                max_pages,
            )

    logger.info("fetch_call_logs: %d total calls across %d page(s)", len(all_calls), page)
    return all_calls


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
        # Minimum 60 minutes so long recordings (which CloudCall takes
        # several minutes to finalize on their side) get caught on the
        # regular poll cadence. Combined with pending_url retry below,
        # calls don't slip through if CloudCall is briefly slow to make
        # the URL available.
        lookback_minutes = max(CLOUDCALL_POLL_INTERVAL_MINUTES * 2, 60)

    now = datetime.utcnow()
    from_dt = (now - __import__("datetime").timedelta(minutes=lookback_minutes)).strftime("%Y-%m-%dT%H:%M:%S")
    to_dt = now.strftime("%Y-%m-%dT%H:%M:%S")

    logger.info("Polling CloudCall call logs from %s to %s", from_dt, to_dt)

    # First, retry any rows stuck at status='pending_url' from prior polls.
    # These are calls where CloudCall returned an error or an empty URL
    # when we first saw them. Once CloudCall finalizes the recording on
    # their side, the retry succeeds and the row transitions to 'available'
    # + auto-imports. Rows that stay broken for >24h transition to
    # 'no_audio' so they don't linger as pending forever.
    retry_stats = retry_pending_url_recordings()

    try:
        calls = fetch_call_logs(from_dt, to_dt)
    except Exception as e:
        logger.error("Failed to fetch call logs: %s", e)
        raise

    if not calls:
        logger.info(
            "No recorded calls found in polling window (pending_url retries: %s)",
            retry_stats,
        )
        return 0

    # Pre-fetch every cloudcall_recording_id we already have so we can
    # skip the per-call URL fetch for duplicates. Without this, a 72-hour
    # re-pull over 300 calls would fire 300 URL requests at CloudCall even
    # though 99% would be no-ops at the insert step.
    from cloudcall_db import get_conn as _cc_get_conn
    _cc_conn = _cc_get_conn()
    try:
        known_ids = {
            r["cloudcall_recording_id"]
            for r in _cc_conn.execute(
                "SELECT cloudcall_recording_id FROM cloudcall_recordings"
            ).fetchall()
        }
    finally:
        _cc_conn.close()

    inserted = 0
    inserted_no_audio = 0
    skipped_known = 0
    skipped_no_call_id = 0
    url_fetch_failed = 0
    for call in calls:
        call_data_id = call.get("user_call_data_id", "")
        if not call_data_id:
            skipped_no_call_id += 1
            continue

        # NOTE: previously this block also skipped calls with
        # recording_duration == 0, on the theory they were voicemails
        # without recordings. But CloudCall also reports duration=0 for
        # calls where the audio file is missing on their side — and the
        # recruiter still wants visibility into those. We now let them
        # through; if the URL fetch confirms there's no audio, the row
        # gets inserted with status=no_audio for explicit display.

        # Already have this one — skip the URL fetch entirely.
        if call_data_id in known_ids:
            skipped_known += 1
            continue

        # Fetch recording URL
        try:
            rec_url = get_recording_url(call_data_id)
            url_fetch_error_str = None
        except Exception as e:
            logger.warning(
                "Failed to get recording URL for %s: %s — inserting as pending_url for retry",
                call_data_id, e,
            )
            url_fetch_failed += 1
            rec_url = None
            url_fetch_error_str = str(e)

        # If the URL fetch raised an exception (not a clean empty response),
        # insert the row at status='pending_url' so retry_pending_url_recordings()
        # picks it up on subsequent polls. Don't skip silently — that's how
        # calls were getting lost when CloudCall took >10 min to finalize
        # the recording.
        if url_fetch_error_str is not None:
            cloudcall_user_id_pu = call.get("cloudcall_user_id", "")
            app_user_id_pu = None
            if cloudcall_user_id_pu:
                m = get_cloudcall_user_mapping(cloudcall_user_id_pu)
                if not m:
                    em = call.get("email", "")
                    if em:
                        m = get_cloudcall_user_mapping(em)
                if m:
                    app_user_id_pu = m["app_user_id"]
            try:
                insert_cloudcall_recording({
                    "cloudcall_recording_id": call_data_id,
                    "cloudcall_user_id": cloudcall_user_id_pu,
                    "app_user_id": app_user_id_pu,
                    "recording_url": "",
                    "recruiter_name": call.get("user_name", ""),
                    "contact_name": call.get("contact_name", ""),
                    "caller_number": call.get("user_number", ""),
                    "callee_number": call.get("contact_number", ""),
                    "direction": call.get("direction", ""),
                    "call_duration_seconds": call.get("duration"),
                    "call_timestamp": call.get("created_on", now.isoformat()),
                    "status": "pending_url",
                    "webhook_received_at": now.isoformat(),
                    "webhook_payload": "",
                })
            except Exception as insert_err:
                if "UNIQUE constraint" not in str(insert_err):
                    logger.error(
                        "Failed to insert pending_url row for %s: %s",
                        call_data_id, insert_err,
                    )
            continue

        # CloudCall sometimes flags a call as is_recorded=1 but its audio
        # file is missing on their side (their UI shows "an audio file does
        # not exist"). Previously we silently dropped those — the recruiter
        # never knew the call existed. Now we insert a row with
        # status="no_audio" so it shows up in the Call Log with a clear
        # indicator, and the Import button is disabled for it.
        if rec_url:
            record_status = "available"
            url_to_store = rec_url
        else:
            record_status = "no_audio"
            url_to_store = ""  # column is NOT NULL — use empty placeholder
            logger.info(
                "CloudCall has no audio file for call %s — storing with status=no_audio",
                call_data_id,
            )

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
        recording_db_id = None
        try:
            recording_db_id = insert_cloudcall_recording({
                "cloudcall_recording_id": call_data_id,
                "cloudcall_user_id": cloudcall_user_id,
                "app_user_id": app_user_id,
                "recording_url": url_to_store,
                "recruiter_name": call.get("user_name", ""),
                "contact_name": call.get("contact_name", ""),
                "caller_number": call.get("user_number", ""),
                "callee_number": call.get("contact_number", ""),
                "direction": call.get("direction", ""),
                "call_duration_seconds": call.get("duration"),
                "call_timestamp": call.get("created_on", now.isoformat()),
                "status": record_status,
                "webhook_received_at": now.isoformat(),
                "webhook_payload": "",
            })
            inserted += 1
            if record_status == "no_audio":
                inserted_no_audio += 1
            # Forensic log: every field that helps explain why a call may
            # have been split / duplicated by CloudCall. Captures the
            # per-call identifiers + numbers + both durations + direction.
            logger.info(
                "Polled recording: id=%s user=%s (cc_id=%s) contact=%s "
                "caller=%s callee=%s call_duration=%ss rec_duration=%ss "
                "direction=%s status=%s created_on=%s",
                call_data_id,
                call.get("user_name", ""), cloudcall_user_id,
                call.get("contact_name", ""),
                call.get("user_number", ""),
                call.get("contact_number", ""),
                call.get("duration", 0),
                call.get("recording_duration", 0),
                call.get("direction", ""),
                record_status,
                call.get("created_on", ""),
            )
        except Exception as e:
            if "UNIQUE constraint" in str(e):
                continue  # Already exists, expected for overlap window
            else:
                logger.error("Failed to insert polled recording %s: %s", call_data_id, e)
                continue

        # Auto-import: only for recordings with an actual audio file. The
        # "no_audio" rows are just visibility placeholders — there's nothing
        # to download or transcribe.
        if recording_db_id and app_user_id and record_status == "available":
            try:
                # Prefer the contact name; fall back to the phone number when
                # CloudCall didn't know the contact (cold call to unknown number).
                # This drives the dropdown label in the Calls tab.
                contact_label = (
                    call.get("contact_name") or call.get("contact_number") or ""
                ).strip()
                import_recording_to_pipeline(
                    cloudcall_recording_db_id=recording_db_id,
                    user_id=app_user_id,
                    metadata={
                        "recruiter_name": call.get("user_name", ""),
                        "subject_name": contact_label,
                    },
                )
            except Exception as e:
                # Don't fail the whole poll — leave the recording at 'error'
                # status (set by import_recording_to_pipeline) for admin review.
                logger.warning(
                    "Auto-import failed for recording %s (db_id=%d): %s",
                    call_data_id, recording_db_id, e,
                )

    inserted_with_audio = inserted - inserted_no_audio
    logger.info(
        "Polling complete: %d total calls from CloudCall | %d inserted with audio | "
        "%d inserted as no_audio | %d already known (skipped) | "
        "%d had no call_data_id (skipped) | %d URL fetch errors (queued as pending_url for retry) | "
        "pending_url retry stats: %s",
        len(calls), inserted_with_audio, inserted_no_audio, skipped_known,
        skipped_no_call_id, url_fetch_failed, retry_stats,
    )

    # Opportunistic cleanup
    maybe_cleanup_expired()

    return inserted
