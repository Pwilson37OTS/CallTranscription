"""Tests for cloudcall_db module."""

from datetime import datetime, timedelta

import pytest


@pytest.fixture()
def cc_db(tmp_db):
    """Return the cloudcall_db module configured with a temp database."""
    import cloudcall_db
    import importlib
    importlib.reload(cloudcall_db)
    return cloudcall_db


@pytest.fixture()
def sample_user(db_mod):
    """Create and return a sample user for mapping tests."""
    from auth import hash_password
    user_id = db_mod.insert_user({
        "email": "recruiter@test.com",
        "display_name": "Test Recruiter",
        "password_hash": hash_password("pass123"),
        "role": "recruiter",
        "is_active": 1,
    })
    return user_id


# --- User Mapping Tests ---

class TestCloudCallUserMapping:
    def test_upsert_insert(self, cc_db, sample_user):
        cc_db.upsert_cloudcall_user_mapping("ext-100", sample_user, "John Ext 100")
        mapping = cc_db.get_cloudcall_user_mapping("ext-100")
        assert mapping is not None
        assert mapping["app_user_id"] == sample_user
        assert mapping["cloudcall_display_name"] == "John Ext 100"

    def test_upsert_update(self, cc_db, sample_user, db_mod):
        from auth import hash_password
        user2 = db_mod.insert_user({
            "email": "recruiter2@test.com",
            "display_name": "Test Recruiter 2",
            "password_hash": hash_password("pass123"),
            "role": "recruiter",
            "is_active": 1,
        })
        cc_db.upsert_cloudcall_user_mapping("ext-100", sample_user, "Name A")
        cc_db.upsert_cloudcall_user_mapping("ext-100", user2, "Name B")

        mapping = cc_db.get_cloudcall_user_mapping("ext-100")
        assert mapping["app_user_id"] == user2
        assert mapping["cloudcall_display_name"] == "Name B"

    def test_get_all_mappings(self, cc_db, sample_user):
        cc_db.upsert_cloudcall_user_mapping("ext-1", sample_user, "")
        cc_db.upsert_cloudcall_user_mapping("ext-2", sample_user, "")
        all_mappings = cc_db.get_all_cloudcall_user_mappings()
        assert len(all_mappings) == 2

    def test_delete_mapping(self, cc_db, sample_user):
        cc_db.upsert_cloudcall_user_mapping("ext-del", sample_user, "")
        mapping = cc_db.get_cloudcall_user_mapping("ext-del")
        cc_db.delete_cloudcall_user_mapping(mapping["id"])
        assert cc_db.get_cloudcall_user_mapping("ext-del") is None

    def test_get_nonexistent_mapping(self, cc_db):
        assert cc_db.get_cloudcall_user_mapping("nonexistent") is None


# --- Recording Tests ---

def _make_recording(overrides=None):
    base = {
        "cloudcall_recording_id": "rec-001",
        "cloudcall_user_id": "ext-100",
        "app_user_id": None,
        "recording_url": "https://api.cloudcall.com/recordings/rec-001.mp3",
        "caller_number": "+15551234567",
        "callee_number": "+15559876543",
        "direction": "outbound",
        "call_duration_seconds": 180,
        "call_timestamp": datetime.utcnow().isoformat(),
        "status": "available",
        "webhook_received_at": datetime.utcnow().isoformat(),
        "webhook_payload": '{"test": true}',
    }
    if overrides:
        base.update(overrides)
    return base


class TestCloudCallRecordings:
    def test_insert_and_get(self, cc_db):
        rec_id = cc_db.insert_cloudcall_recording(_make_recording())
        assert rec_id > 0
        rec = cc_db.get_cloudcall_recording(rec_id)
        assert rec is not None
        assert rec["cloudcall_recording_id"] == "rec-001"
        assert rec["status"] == "available"

    def test_unique_constraint(self, cc_db):
        cc_db.insert_cloudcall_recording(_make_recording())
        with pytest.raises(Exception, match="UNIQUE"):
            cc_db.insert_cloudcall_recording(_make_recording())

    def test_update_status(self, cc_db, db_mod, sample_user):
        # Create a real call to satisfy the foreign key on imported_call_id
        call_id = db_mod.insert_call({
            "created_at": datetime.utcnow().isoformat(),
            "original_filename": "test.mp3",
            "stored_filename": "test.wav",
            "stored_path": "data/converted/test.wav",
            "mime_type": "audio/wav",
            "status": "uploaded",
            "user_id": sample_user,
        })
        rec_id = cc_db.insert_cloudcall_recording(_make_recording())
        cc_db.update_cloudcall_recording(rec_id, status="imported", imported_call_id=call_id)
        rec = cc_db.get_cloudcall_recording(rec_id)
        assert rec["status"] == "imported"
        assert rec["imported_call_id"] == call_id

    def test_update_disallowed_column(self, cc_db):
        rec_id = cc_db.insert_cloudcall_recording(_make_recording())
        with pytest.raises(ValueError, match="Disallowed"):
            cc_db.update_cloudcall_recording(rec_id, recording_url="http://evil.com")

    def test_get_recordings_user_scoped(self, cc_db, sample_user):
        cc_db.insert_cloudcall_recording(_make_recording({"app_user_id": sample_user}))
        cc_db.insert_cloudcall_recording(_make_recording({
            "cloudcall_recording_id": "rec-002",
            "app_user_id": None,
        }))
        user_recs = cc_db.get_cloudcall_recordings(user_id=sample_user, hours=48)
        assert len(user_recs) == 1
        all_recs = cc_db.get_cloudcall_recordings(user_id=None, hours=48)
        assert len(all_recs) == 2

    def test_delete_expired(self, cc_db):
        old_time = (datetime.utcnow() - timedelta(hours=72)).isoformat()
        cc_db.insert_cloudcall_recording(_make_recording({
            "cloudcall_recording_id": "old-rec",
            "webhook_received_at": old_time,
        }))
        cc_db.insert_cloudcall_recording(_make_recording({
            "cloudcall_recording_id": "new-rec",
            "webhook_received_at": datetime.utcnow().isoformat(),
        }))
        deleted = cc_db.delete_expired_cloudcall_recordings(retention_hours=48)
        assert deleted == 1
        all_recs = cc_db.get_cloudcall_recordings(user_id=None, hours=999)
        assert len(all_recs) == 1
        assert all_recs[0]["cloudcall_recording_id"] == "new-rec"

    def test_get_unmapped_user_ids(self, cc_db):
        cc_db.insert_cloudcall_recording(_make_recording({
            "cloudcall_recording_id": "rec-unmap1",
            "cloudcall_user_id": "ext-999",
            "app_user_id": None,
        }))
        cc_db.insert_cloudcall_recording(_make_recording({
            "cloudcall_recording_id": "rec-unmap2",
            "cloudcall_user_id": "ext-999",
            "app_user_id": None,
        }))
        unmapped = cc_db.get_unmapped_cloudcall_user_ids()
        assert unmapped == ["ext-999"]

    def test_get_nonexistent_recording(self, cc_db):
        assert cc_db.get_cloudcall_recording(9999) is None
