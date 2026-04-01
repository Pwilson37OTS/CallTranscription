"""FastAPI webhook receiver for CloudCall event notifications.

Runs as a sidecar alongside Streamlit on port 8080.
Start with: uvicorn webhook_server:app --host 0.0.0.0 --port 8080

CloudCall sends ALL events (SMS, CallStart, CallComplete, CallRecording, etc.)
to a single registered endpoint. This server filters for CallRecording events
and ignores the rest.

Webhook signature: x-cloudcall-sig header = HMAC-SHA256(body, signing_key)
"""

import hashlib
import hmac
import json
import logging
from datetime import datetime
from time import time
from typing import List, Optional

from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel, Field

from cloudcall_config import CLOUDCALL_WEBHOOK_SIGNING_KEY, CLOUDCALL_RECORDING_RETENTION_HOURS
from cloudcall_db import (
    init_cloudcall_tables,
    insert_cloudcall_recording,
    get_cloudcall_user_mapping,
    delete_expired_cloudcall_recordings,
)

logger = logging.getLogger("calltranscription.webhook")

app = FastAPI(title="CloudCall Webhook Receiver", docs_url=None, redoc_url=None)

# Ensure tables exist on startup
init_cloudcall_tables()

# Throttle cleanup to max once per 15 minutes
_last_cleanup_time: float = 0.0
_CLEANUP_INTERVAL_SECONDS = 900


# --- CloudCall webhook payload models (matching actual API schemas) ---

class CloudCallUser(BaseModel):
    id: str = Field(default="")
    email: str = Field(default="")


class CloudCallRecordingItem(BaseModel):
    url: str = Field(..., description="Temporary recording URL")


class CallRecordingEvent(BaseModel):
    callId: str = Field(..., description="Session ID of the call")
    user: CloudCallUser = Field(default_factory=CloudCallUser)
    recordings: List[CloudCallRecordingItem] = Field(default_factory=list)


def _verify_signature(body: bytes, signature: str) -> bool:
    """Verify HMAC-SHA256 webhook signature using the signing_key."""
    if not CLOUDCALL_WEBHOOK_SIGNING_KEY:
        logger.warning("CLOUDCALL_WEBHOOK_SIGNING_KEY not set — skipping signature verification")
        return True
    expected = hmac.new(
        CLOUDCALL_WEBHOOK_SIGNING_KEY.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def _maybe_cleanup():
    """Run expired recording cleanup if enough time has passed."""
    global _last_cleanup_time
    now = time()
    if now - _last_cleanup_time >= _CLEANUP_INTERVAL_SECONDS:
        _last_cleanup_time = now
        try:
            deleted = delete_expired_cloudcall_recordings(CLOUDCALL_RECORDING_RETENTION_HOURS)
            if deleted > 0:
                logger.info("Cleanup: deleted %d expired CloudCall recordings", deleted)
        except Exception as e:
            logger.error("Cleanup failed: %s", e)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/webhooks/cloudcall")
async def cloudcall_webhook(request: Request):
    """Receive all CloudCall webhook events. Filters for CallRecording events."""
    body = await request.body()

    # Validate webhook signature (x-cloudcall-sig header)
    signature = request.headers.get("x-cloudcall-sig", "")
    if CLOUDCALL_WEBHOOK_SIGNING_KEY and not _verify_signature(body, signature):
        logger.warning("Webhook rejected: invalid signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    # Parse JSON body
    try:
        data = json.loads(body)
    except Exception as e:
        logger.warning("Webhook rejected: invalid JSON: %s", e)
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Determine event type — CallRecording events have "recordings" array
    if "recordings" not in data:
        # Not a recording event (could be CallStart, CallComplete, SMS, Note, etc.)
        # Acknowledge and ignore
        logger.debug("Non-recording webhook event received, ignoring")
        return {"status": "ok", "detail": "event type not handled"}

    # Parse as CallRecording event
    try:
        event = CallRecordingEvent.model_validate(data)
    except Exception as e:
        logger.warning("Webhook rejected: malformed CallRecording payload: %s", e)
        raise HTTPException(status_code=400, detail=f"Invalid payload: {e}")

    if not event.recordings:
        logger.info("CallRecording event with empty recordings array, ignoring: callId=%s", event.callId)
        return {"status": "ok", "detail": "no recordings in event"}

    # Look up CloudCall user -> app user mapping (try user.id first, then email)
    app_user_id = None
    cloudcall_user_id = event.user.id or event.user.email or None
    if cloudcall_user_id:
        mapping = get_cloudcall_user_mapping(cloudcall_user_id)
        if not mapping and event.user.email and event.user.id:
            # Try email as fallback lookup
            mapping = get_cloudcall_user_mapping(event.user.email)
        if mapping:
            app_user_id = mapping["app_user_id"]
        else:
            logger.warning(
                "Unmapped CloudCall user id=%s email=%s for callId=%s",
                event.user.id, event.user.email, event.callId,
            )

    # Insert one row per recording URL (usually just one)
    inserted = 0
    for i, rec in enumerate(event.recordings):
        recording_id = f"{event.callId}" if len(event.recordings) == 1 else f"{event.callId}_{i}"
        try:
            insert_cloudcall_recording({
                "cloudcall_recording_id": recording_id,
                "cloudcall_user_id": cloudcall_user_id,
                "app_user_id": app_user_id,
                "recording_url": rec.url,
                "caller_number": "",
                "callee_number": "",
                "direction": "",
                "call_duration_seconds": None,
                "call_timestamp": datetime.utcnow().isoformat(),
                "status": "available",
                "webhook_received_at": datetime.utcnow().isoformat(),
                "webhook_payload": body.decode("utf-8", errors="replace"),
            })
            inserted += 1
        except Exception as e:
            if "UNIQUE constraint" in str(e):
                logger.info("Duplicate recording ignored: %s", recording_id)
            else:
                logger.error("Failed to insert recording %s: %s", recording_id, e)

    if inserted > 0:
        logger.info(
            "Webhook processed: callId=%s user=%s app_user_id=%s recordings=%d",
            event.callId, cloudcall_user_id, app_user_id, inserted,
        )

    # Opportunistic cleanup
    _maybe_cleanup()

    return {"status": "ok", "recordings_inserted": inserted}
