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
)
from cloudcall_db import (
    get_cloudcall_recording,
    update_cloudcall_recording,
    delete_expired_cloudcall_recordings,
)
from db import insert_call
from file_service import convert_file_to_wav

logger = logging.getLogger("calltranscription.cloudcall")

# Throttle cleanup to max once per 15 minutes
_last_cleanup_time: float = 0.0
_CLEANUP_INTERVAL_SECONDS = 900

# In-memory token cache
_access_token: str = ""
_access_token_expires_at: float = 0.0
_current_refresh_token: str = CLOUDCALL_REFRESH_TOKEN


def get_access_token() -> str:
    """Get a valid CloudCall access token, refreshing if needed.

    The refresh token flow returns a new refresh_token each time,
    which we store in memory for subsequent calls. The initial
    refresh_token comes from the CLOUDCALL_REFRESH_TOKEN env var.
    """
    global _access_token, _access_token_expires_at, _current_refresh_token

    # Return cached token if still valid (with 5 min buffer)
    if _access_token and time() < (_access_token_expires_at - 300):
        return _access_token

    if not _current_refresh_token:
        raise RuntimeError("CLOUDCALL_REFRESH_TOKEN is not configured")

    with httpx.Client(timeout=30.0) as client:
        response = client.post(
            CLOUDCALL_AUTH_URL,
            data={
                "client_id": CLOUDCALL_CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": _current_refresh_token,
            },
        )
        response.raise_for_status()

    token_data = response.json()
    _access_token = token_data["access_token"]
    _access_token_expires_at = time() + token_data.get("expires_in", 86400)
    _current_refresh_token = token_data.get("refresh_token", _current_refresh_token)

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
