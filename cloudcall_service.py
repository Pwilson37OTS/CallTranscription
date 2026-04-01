"""CloudCall API client and recording import pipeline.

Handles downloading recordings from CloudCall and importing them
into the existing transcription/summarization pipeline.
"""

import logging
from datetime import datetime
from pathlib import Path
from time import time
from typing import Dict, Any, Optional

import httpx

from config import CONVERTED_DIR
from cloudcall_config import (
    CLOUDCALL_API_BASE_URL,
    CLOUDCALL_API_KEY,
    CLOUDCALL_API_SECRET,
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


def download_recording(recording_url: str, cloudcall_recording_id: str) -> Path:
    """Download a recording from CloudCall API.

    Args:
        recording_url: The URL to download the recording from.
        cloudcall_recording_id: Used as the filename stem.

    Returns:
        Path to the downloaded file.
    """
    headers = {}
    if CLOUDCALL_API_KEY:
        headers["Authorization"] = f"Bearer {CLOUDCALL_API_KEY}"

    with httpx.Client(timeout=120.0) as client:
        response = client.get(recording_url, headers=headers, follow_redirects=True)
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
        caller = recording["caller_number"] or "unknown"
        callee = recording["callee_number"] or "unknown"
        original_filename = f"cloudcall_{caller}_to_{callee}_{recording['cloudcall_recording_id']}{download_path.suffix}"

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
            "notes": metadata.get("notes", f"Imported from CloudCall. Direction: {recording['direction'] or 'unknown'}"),
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
