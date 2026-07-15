from datetime import datetime

import pytest


class TestUserCRUD:
    def test_insert_and_get_user(self, db_mod, sample_user_record):
        uid = db_mod.insert_user(sample_user_record)
        assert uid >= 1

        user = db_mod.get_user_by_id(uid)
        assert user is not None
        assert user["email"] == "tester@example.com"
        assert user["display_name"] == "Test User"
        assert user["role"] == "recruiter"
        assert user["is_active"] == 1

    def test_get_user_by_email(self, db_mod, sample_user_record):
        db_mod.insert_user(sample_user_record)
        user = db_mod.get_user_by_email("tester@example.com")
        assert user is not None
        assert user["display_name"] == "Test User"

    def test_get_user_by_email_not_found(self, db_mod):
        assert db_mod.get_user_by_email("nobody@example.com") is None

    def test_get_user_by_id_not_found(self, db_mod):
        assert db_mod.get_user_by_id(9999) is None

    def test_get_all_users(self, db_mod, sample_user_record):
        db_mod.insert_user(sample_user_record)
        second = {**sample_user_record, "email": "second@example.com", "display_name": "Second"}
        db_mod.insert_user(second)
        users = db_mod.get_all_users()
        assert len(users) == 2

    def test_update_user(self, db_mod, sample_user_record):
        uid = db_mod.insert_user(sample_user_record)
        db_mod.update_user(uid, display_name="Updated Name", role="admin")
        user = db_mod.get_user_by_id(uid)
        assert user["display_name"] == "Updated Name"
        assert user["role"] == "admin"

    def test_update_user_disallowed_column(self, db_mod, sample_user_record):
        uid = db_mod.insert_user(sample_user_record)
        with pytest.raises(ValueError, match="Disallowed user column"):
            db_mod.update_user(uid, email="hacker@evil.com")

    def test_must_change_password_defaults_to_zero(self, db_mod, sample_user_record):
        uid = db_mod.insert_user(sample_user_record)
        user = db_mod.get_user_by_id(uid)
        assert user["must_change_password"] == 0

    def test_update_must_change_password(self, db_mod, sample_user_record):
        uid = db_mod.insert_user(sample_user_record)
        db_mod.update_user(uid, must_change_password=1)
        assert db_mod.get_user_by_id(uid)["must_change_password"] == 1
        db_mod.update_user(uid, must_change_password=0)
        assert db_mod.get_user_by_id(uid)["must_change_password"] == 0

    def test_update_user_no_fields(self, db_mod, sample_user_record):
        uid = db_mod.insert_user(sample_user_record)
        # Should be a no-op, not raise
        db_mod.update_user(uid)

    def test_duplicate_email_raises(self, db_mod, sample_user_record):
        db_mod.insert_user(sample_user_record)
        with pytest.raises(Exception):
            db_mod.insert_user(sample_user_record)


class TestCallCRUD:
    def test_insert_and_get_call(self, db_mod, sample_user_record, sample_call_record):
        uid = db_mod.insert_user(sample_user_record)
        sample_call_record["user_id"] = uid
        call_id = db_mod.insert_call(sample_call_record)
        assert call_id >= 1

        call = db_mod.get_call(call_id)
        assert call is not None
        assert call["original_filename"] == "test_call.mp3"
        assert call["user_id"] == uid

    def test_get_call_not_found(self, db_mod):
        assert db_mod.get_call(9999) is None

    def test_user_scoped_get_all_calls(self, db_mod, sample_user_record, sample_call_record):
        uid1 = db_mod.insert_user(sample_user_record)
        uid2 = db_mod.insert_user({
            **sample_user_record,
            "email": "other@example.com",
            "display_name": "Other",
        })

        sample_call_record["user_id"] = uid1
        db_mod.insert_call(sample_call_record)
        db_mod.insert_call({**sample_call_record, "user_id": uid2})

        # User 1 sees only their call
        calls_u1 = db_mod.get_all_calls(user_id=uid1)
        assert len(calls_u1) == 1

        # Admin (no user_id filter) sees all
        calls_all = db_mod.get_all_calls()
        assert len(calls_all) == 2

    def test_user_scoped_get_call(self, db_mod, sample_user_record, sample_call_record):
        uid1 = db_mod.insert_user(sample_user_record)
        uid2 = db_mod.insert_user({
            **sample_user_record,
            "email": "other@example.com",
            "display_name": "Other",
        })
        sample_call_record["user_id"] = uid1
        call_id = db_mod.insert_call(sample_call_record)

        # Owner can access
        assert db_mod.get_call(call_id, user_id=uid1) is not None
        # Other user cannot
        assert db_mod.get_call(call_id, user_id=uid2) is None
        # Admin (no filter) can
        assert db_mod.get_call(call_id) is not None

    def test_update_call(self, db_mod, sample_user_record, sample_call_record):
        uid = db_mod.insert_user(sample_user_record)
        sample_call_record["user_id"] = uid
        call_id = db_mod.insert_call(sample_call_record)

        db_mod.update_call(call_id, status="transcribed", transcript_text="Hello world")
        call = db_mod.get_call(call_id)
        assert call["status"] == "transcribed"
        assert call["transcript_text"] == "Hello world"

    def test_update_call_disallowed_column(self, db_mod, sample_user_record, sample_call_record):
        uid = db_mod.insert_user(sample_user_record)
        sample_call_record["user_id"] = uid
        call_id = db_mod.insert_call(sample_call_record)

        with pytest.raises(ValueError, match="disallowed column"):
            db_mod.update_call(call_id, created_at="2020-01-01")

    def test_update_call_no_fields(self, db_mod, sample_user_record, sample_call_record):
        uid = db_mod.insert_user(sample_user_record)
        sample_call_record["user_id"] = uid
        call_id = db_mod.insert_call(sample_call_record)
        db_mod.update_call(call_id)  # no-op

    def test_calls_ordered_desc(self, db_mod, sample_user_record, sample_call_record):
        uid = db_mod.insert_user(sample_user_record)
        sample_call_record["user_id"] = uid
        id1 = db_mod.insert_call(sample_call_record)
        id2 = db_mod.insert_call({**sample_call_record, "original_filename": "second.mp3"})

        calls = db_mod.get_all_calls()
        assert calls[0]["id"] == id2  # newest first
        assert calls[1]["id"] == id1


class TestApiUsage:
    def test_insert_and_get_usage(self, db_mod, sample_user_record, sample_call_record):
        uid = db_mod.insert_user(sample_user_record)
        sample_call_record["user_id"] = uid
        call_id = db_mod.insert_call(sample_call_record)

        usage_id = db_mod.insert_api_usage({
            "user_id": uid,
            "call_id": call_id,
            "operation": "transcription",
            "model": "gpt-4o-transcribe",
            "estimated_cost_cents": 60,
            "created_at": datetime.utcnow().isoformat(),
        })
        assert usage_id >= 1

        usage = db_mod.get_api_usage(user_id=uid)
        assert len(usage) == 1
        assert usage[0]["operation"] == "transcription"

    def test_usage_summary(self, db_mod, sample_user_record, sample_call_record):
        uid = db_mod.insert_user(sample_user_record)
        sample_call_record["user_id"] = uid
        call_id = db_mod.insert_call(sample_call_record)

        now = datetime.utcnow().isoformat()
        db_mod.insert_api_usage({
            "user_id": uid, "call_id": call_id,
            "operation": "transcription", "model": "gpt-4o-transcribe",
            "estimated_cost_cents": 60, "created_at": now,
        })
        db_mod.insert_api_usage({
            "user_id": uid, "call_id": call_id,
            "operation": "summarization", "model": "gpt-4.1",
            "estimated_cost_cents": 100, "created_at": now,
        })

        summary = db_mod.get_usage_summary(days=30)
        assert len(summary) == 1
        assert summary[0]["total_calls"] == 2
        assert summary[0]["transcriptions"] == 1
        assert summary[0]["summarizations"] == 1
        assert summary[0]["total_cost_cents"] == 160

    def test_usage_filtered_by_user(self, db_mod, sample_user_record, sample_call_record):
        uid1 = db_mod.insert_user(sample_user_record)
        uid2 = db_mod.insert_user({
            **sample_user_record,
            "email": "other@example.com",
            "display_name": "Other",
        })
        sample_call_record["user_id"] = uid1
        call_id = db_mod.insert_call(sample_call_record)

        now = datetime.utcnow().isoformat()
        db_mod.insert_api_usage({
            "user_id": uid1, "call_id": call_id,
            "operation": "transcription", "model": "gpt-4o-transcribe",
            "estimated_cost_cents": 60, "created_at": now,
        })

        assert len(db_mod.get_api_usage(user_id=uid1)) == 1
        assert len(db_mod.get_api_usage(user_id=uid2)) == 0
        assert len(db_mod.get_api_usage()) == 1  # admin sees all
