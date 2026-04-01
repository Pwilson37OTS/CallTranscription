"""Tests for webhook_server.py (FastAPI CloudCall webhook receiver)."""

import hashlib
import hmac
import json
from datetime import datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def webhook_client(tmp_db, monkeypatch):
    """Create a FastAPI test client with a temp database."""
    monkeypatch.setattr("cloudcall_config.CLOUDCALL_WEBHOOK_SIGNING_KEY", "test-secret-123")

    import webhook_server
    import importlib
    importlib.reload(webhook_server)

    return TestClient(webhook_server.app)


def _sign(body: bytes, secret: str = "test-secret-123") -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _make_recording_event(overrides=None):
    """Build a CloudCall CallRecording webhook event payload."""
    base = {
        "callId": "call-test-001",
        "user": {
            "id": "ext-100",
            "email": "recruiter@oaktree.com",
        },
        "recordings": [
            {"url": "https://recordings.cloudcall.com/temp/call-test-001.mp3"}
        ],
    }
    if overrides:
        base.update(overrides)
    return base


class TestHealthEndpoint:
    def test_health(self, webhook_client):
        response = webhook_client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


class TestCloudCallWebhook:
    def test_valid_recording_event(self, webhook_client):
        payload = _make_recording_event()
        body = json.dumps(payload).encode("utf-8")
        signature = _sign(body)

        response = webhook_client.post(
            "/webhooks/cloudcall",
            content=body,
            headers={"x-cloudcall-sig": signature, "Content-Type": "application/json"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert response.json()["recordings_inserted"] == 1

    def test_invalid_signature(self, webhook_client):
        payload = _make_recording_event()
        body = json.dumps(payload).encode("utf-8")

        response = webhook_client.post(
            "/webhooks/cloudcall",
            content=body,
            headers={"x-cloudcall-sig": "bad-signature", "Content-Type": "application/json"},
        )
        assert response.status_code == 401

    def test_missing_signature(self, webhook_client):
        payload = _make_recording_event()
        body = json.dumps(payload).encode("utf-8")

        response = webhook_client.post(
            "/webhooks/cloudcall",
            content=body,
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 401

    def test_duplicate_recording_idempotent(self, webhook_client):
        payload = _make_recording_event()
        body = json.dumps(payload).encode("utf-8")
        signature = _sign(body)
        headers = {"x-cloudcall-sig": signature, "Content-Type": "application/json"}

        resp1 = webhook_client.post("/webhooks/cloudcall", content=body, headers=headers)
        assert resp1.status_code == 200
        assert resp1.json()["recordings_inserted"] == 1

        resp2 = webhook_client.post("/webhooks/cloudcall", content=body, headers=headers)
        assert resp2.status_code == 200
        assert resp2.json()["recordings_inserted"] == 0  # duplicate ignored

    def test_non_recording_event_ignored(self, webhook_client):
        """Non-recording events (CallStart, SMS, etc.) should be acknowledged but ignored."""
        payload = {
            "callId": "call-start-001",
            "user": {"id": "ext-100", "email": "test@test.com"},
            "status": "ringing",
        }
        body = json.dumps(payload).encode("utf-8")
        signature = _sign(body)

        response = webhook_client.post(
            "/webhooks/cloudcall",
            content=body,
            headers={"x-cloudcall-sig": signature, "Content-Type": "application/json"},
        )
        assert response.status_code == 200
        assert "not handled" in response.json().get("detail", "")

    def test_empty_recordings_array(self, webhook_client):
        payload = _make_recording_event({"recordings": []})
        body = json.dumps(payload).encode("utf-8")
        signature = _sign(body)

        response = webhook_client.post(
            "/webhooks/cloudcall",
            content=body,
            headers={"x-cloudcall-sig": signature, "Content-Type": "application/json"},
        )
        assert response.status_code == 200
        assert "no recordings" in response.json().get("detail", "")

    def test_unmapped_user_still_inserts(self, webhook_client):
        """Recordings from unmapped users should still be saved with app_user_id=NULL."""
        payload = _make_recording_event({
            "callId": "call-unmapped",
            "user": {"id": "unknown-ext-999", "email": "nobody@test.com"},
        })
        body = json.dumps(payload).encode("utf-8")
        signature = _sign(body)

        response = webhook_client.post(
            "/webhooks/cloudcall",
            content=body,
            headers={"x-cloudcall-sig": signature, "Content-Type": "application/json"},
        )
        assert response.status_code == 200

        import cloudcall_db
        recs = cloudcall_db.get_cloudcall_recordings(user_id=None, hours=1)
        found = [r for r in recs if r["cloudcall_recording_id"] == "call-unmapped"]
        assert len(found) == 1
        assert found[0]["app_user_id"] is None

    def test_mapped_user_gets_app_user_id(self, webhook_client, db_mod):
        """When a CloudCall user is mapped, the recording should get the correct app_user_id."""
        from auth import hash_password
        user_id = db_mod.insert_user({
            "email": "mapped@test.com",
            "display_name": "Mapped User",
            "password_hash": hash_password("pass123"),
            "role": "recruiter",
            "is_active": 1,
        })

        import cloudcall_db
        cloudcall_db.upsert_cloudcall_user_mapping("ext-mapped", user_id, "Mapped")

        payload = _make_recording_event({
            "callId": "call-mapped",
            "user": {"id": "ext-mapped", "email": "mapped@test.com"},
        })
        body = json.dumps(payload).encode("utf-8")
        signature = _sign(body)

        response = webhook_client.post(
            "/webhooks/cloudcall",
            content=body,
            headers={"x-cloudcall-sig": signature, "Content-Type": "application/json"},
        )
        assert response.status_code == 200

        recs = cloudcall_db.get_cloudcall_recordings(user_id=user_id, hours=1)
        assert len(recs) == 1
        assert recs[0]["app_user_id"] == user_id


class TestNoSigningKeyConfigured:
    def test_webhook_without_signing_key(self, tmp_db, monkeypatch):
        """When no signing key is configured, signature validation is skipped."""
        monkeypatch.setattr("cloudcall_config.CLOUDCALL_WEBHOOK_SIGNING_KEY", "")

        import webhook_server
        import importlib
        importlib.reload(webhook_server)

        client = TestClient(webhook_server.app)

        payload = _make_recording_event({"callId": "call-nosecret"})
        body = json.dumps(payload).encode("utf-8")

        response = client.post(
            "/webhooks/cloudcall",
            content=body,
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 200
