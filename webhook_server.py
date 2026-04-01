"""FastAPI webhook receiver for CloudCall recording notifications.

Runs as a sidecar alongside Streamlit on port 8080.
Start with: uvicorn webhook_server:app --host 0.0.0.0 --port 8080
"""

import hashlib
import hmac
import json
import logging
from datetime import datetime
from time import time

from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel, Field

from cloudcall_config import CLOUDCALL_WEBHOOK_SECRET, CLOUDCALL_RECORDING_RETENTION_HOURS
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


class RecordingReadyPayload(BaseModel):
    recording_id: str = Field(..., description="CloudCall unique recording ID")
    user_id: str = Field(default="", description="CloudCall user ID / extension")
    recording_url: str = Field(..., description="URL to download the recording")
    caller_number: str = Field(default="")
    callee_number: str = Field(default="")
    direction: str = Field(default="")
    duration: int = Field(default=0, description="Call duration in seconds")
    timestamp: str = Field(..., description="Call timestamp (ISO 8601)")


def _verify_signature(body: bytes, signature: str) -> bool:
    """Verify HMAC-SHA256 webhook signature."""
    if not CLOUDCALL_WEBHOOK_SECRET:
        logger.warning("CLOUDCALL_WEBHOOK_SECRET not set — skipping signature verification")
        return True
    expected = hmac.new(
        CLOUDCALL_WEBHOOK_SECRET.encode("utf-8"),
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


@app.post("/webhooks/cloudcall/recording-ready")
async def recording_ready(request: Request):
    body = await request.body()

    # Validate webhook signature
    signature = request.headers.get("X-CloudCall-Signature", "")
    if CLOUDCALL_WEBHOOK_SECRET and not _verify_signature(body, signature):
        logger.warning("Webhook rejected: invalid signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    # Parse payload
    try:
        payload = RecordingReadyPayload.model_validate_json(body)
    except Exception as e:
        logger.warning("Webhook rejected: malformed payload: %s", e)
        raise HTTPException(status_code=400, detail=f"Invalid payload: {e}")

    # Look up CloudCall user -> app user mapping
    app_user_id = None
    if payload.user_id:
        mapping = get_cloudcall_user_mapping(payload.user_id)
        if mapping:
            app_user_id = mapping["app_user_id"]
        else:
            logger.warning(
                "Unmapped CloudCall user_id=%s for recording=%s",
                payload.user_id,
                payload.recording_id,
            )

    # Insert recording (idempotent: skip duplicates)
    try:
        insert_cloudcall_recording({
            "cloudcall_recording_id": payload.recording_id,
            "cloudcall_user_id": payload.user_id or None,
            "app_user_id": app_user_id,
            "recording_url": payload.recording_url,
            "caller_number": payload.caller_number,
            "callee_number": payload.callee_number,
            "direction": payload.direction,
            "call_duration_seconds": payload.duration,
            "call_timestamp": payload.timestamp,
            "status": "available",
            "webhook_received_at": datetime.utcnow().isoformat(),
            "webhook_payload": body.decode("utf-8", errors="replace"),
        })
        logger.info(
            "Webhook processed: recording=%s user=%s app_user_id=%s",
            payload.recording_id,
            payload.user_id,
            app_user_id,
        )
    except Exception as e:
        if "UNIQUE constraint" in str(e):
            logger.info("Duplicate webhook ignored: recording=%s", payload.recording_id)
            return {"status": "ok", "detail": "duplicate ignored"}
        raise

    # Opportunistic cleanup
    _maybe_cleanup()

    return {"status": "ok"}
