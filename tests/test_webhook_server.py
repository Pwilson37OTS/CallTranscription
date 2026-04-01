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
    monkeypatch.setattr("cloudcall_config.CLOUDCALL_WEBHOOK_SECRET", "test-secret-123")

    import webhook_server
    import importlib
    importlib.reload(webhook_server)

    return TestClient(webhook_server.app)


def _sign(body: bytes, secret: str = "test-secret-123") -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _make_payload(overrides=None):
    base = {
        "recording_id": "rec-test-001",
        "user_id": "ext-100",
        "recording_url": "https://api.cloudcall.com/recordings/rec-test-001.mp3",
        "caller_number": "+15551234567",
        "callee_number": "+15559876543",
        "direction": "outbound",
        "duration": 120,
        "timestamp": datetime.utcnow().isoformat(),
    }
    if overrides:
        base.update(overrides)
    return base


class TestHealthEndpoint:
    def test_health(self, webhook_client):
        response = webhook_client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


class TestRecordingReadyWebhook:
    def test_valid_webhook(self, webhook_client):
        payload = _make_payload()
        body = json.dumps(payload).encode("utf-8")
        signature = _sign(body)

        response = webhook_client.post(
            "/webhooks/cloudcall/recording-ready",
            content=body,
            headers={"X-CloudCall-Signature": signature, "Content-Type": "application/json"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_invalid_signature(self, webhook_client):
        payload = _make_payload()
        body = json.dumps(payload).encode("utf-8")

        response = webhook_client.post(
            "/webhooks/cloudcall/recording-ready",
            content=body,
            headers={"X-CloudCall-Signature": "bad-signature", "Content-Type": "application/json"},
        )
        assert response.status_code == 401

    def test_missing_signature(self, webhook_client):
        payload = _make_payload()
        body = json.dumps(payload).encode("utf-8")

        response = webhook_client.post(
            "/webhooks/cloudcall/recording-ready",
            content=body,
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 401

    def test_duplicate_recording_idempotent(self, webhook_client):
        payload = _make_payload()
        body = json.dumps(payload).encode("utf-8")
        signature = _sign(body)
        headers = {"X-CloudCall-Signature": signature, "Content-Type": "application/json"}

        resp1 = webhook_client.post("/webhooks/cloudcall/recording-ready", content=body, headers=headers)
        assert resp1.status_code == 200

        resp2 = webhook_client.post("/webhooks/cloudcall/recording-ready", content=body, headers=headers)
        assert resp2.status_code == 200
        assert "duplicate" in resp2.json().get("detail", "")

    def test_malformed_payload(self, webhook_client):
        body = b'{"not_valid": true}'
        signature = _sign(body)

        response = webhook_client.post(
            "/webhooks/cloudcall/recording-ready",
            content=body,
            headers={"X-CloudCall-Signature": signature, "Content-Type": "application/json"},
        )
        assert response.status_code == 400

    def test_unmapped_user_still_inserts(self, webhook_client):
        """Recordings from unmapped users should still be saved with app_user_id=NULL."""
        payload = _make_payload({"user_id": "unknown-ext-999", "recording_id": "rec-unmapped"})
        body = json.dumps(payload).encode("utf-8")
        signature = _sign(body)

        response = webhook_client.post(
            "/webhooks/cloudcall/recording-ready",
            content=body,
            headers={"X-CloudCall-Signature": signature, "Content-Type": "application/json"},
        )
        assert response.status_code == 200

        import cloudcall_db
        recs = cloudcall_db.get_cloudcall_recordings(user_id=None, hours=1)
        found = [r for r in recs if r["cloudcall_recording_id"] == "rec-unmapped"]
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

        payload = _make_payload({"user_id": "ext-mapped", "recording_id": "rec-mapped"})
        body = json.dumps(payload).encode("utf-8")
        signature = _sign(body)

        response = webhook_client.post(
            "/webhooks/cloudcall/recording-ready",
            content=body,
            headers={"X-CloudCall-Signature": signature, "Content-Type": "application/json"},
        )
        assert response.status_code == 200

        recs = cloudcall_db.get_cloudcall_recordings(user_id=user_id, hours=1)
        assert len(recs) == 1
        assert recs[0]["app_user_id"] == user_id


class TestNoSecretConfigured:
    def test_webhook_without_secret(self, tmp_db, monkeypatch):
        """When no webhook secret is configured, signature validation is skipped."""
        monkeypatch.setattr("cloudcall_config.CLOUDCALL_WEBHOOK_SECRET", "")

        import webhook_server
        import importlib
        importlib.reload(webhook_server)

        client = TestClient(webhook_server.app)

        payload = _make_payload({"recording_id": "rec-nosecret"})
        body = json.dumps(payload).encode("utf-8")

        response = client.post(
            "/webhooks/cloudcall/recording-ready",
            content=body,
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 200
