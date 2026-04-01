import os
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Set a dummy OPENAI_API_KEY so config module doesn't cause issues
os.environ.setdefault("OPENAI_API_KEY", "test-key-not-real")


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Provide a temporary SQLite database for each test.

    Patches config.DB_PATH and reloads db module so all db functions
    use the temp database.
    """
    db_file = tmp_path / "test_calls.db"
    monkeypatch.setattr("config.DB_PATH", db_file)

    # Also patch the DATA_DIR-related paths so dirs exist
    monkeypatch.setattr("config.DATA_DIR", tmp_path)
    monkeypatch.setattr("config.UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr("config.CONVERTED_DIR", tmp_path / "converted")
    (tmp_path / "uploads").mkdir(exist_ok=True)
    (tmp_path / "converted").mkdir(exist_ok=True)

    # CloudCall directories
    cc_downloads = tmp_path / "cloudcall_downloads"
    cc_downloads.mkdir(exist_ok=True)
    monkeypatch.setattr("cloudcall_config.CLOUDCALL_DOWNLOAD_DIR", cc_downloads)

    # Re-import db to pick up patched DB_PATH
    import db
    import importlib
    importlib.reload(db)

    db.init_db()

    # Also init CloudCall tables so CloudCall tests work with shared fixture
    import cloudcall_db
    import importlib as _il
    _il.reload(cloudcall_db)
    cloudcall_db.init_cloudcall_tables()

    return db_file


@pytest.fixture()
def db_mod(tmp_db):
    """Return the db module configured with a temp database."""
    import db
    return db


@pytest.fixture()
def sample_user_record():
    """Return a minimal user record dict for insert_user."""
    from auth import hash_password
    return {
        "email": "tester@example.com",
        "display_name": "Test User",
        "password_hash": hash_password("password123"),
        "role": "recruiter",
        "is_active": 1,
    }


@pytest.fixture()
def sample_call_record():
    """Return a minimal call record dict for insert_call."""
    from datetime import datetime
    return {
        "created_at": datetime.utcnow().isoformat(),
        "original_filename": "test_call.mp3",
        "stored_filename": "test_call.wav",
        "stored_path": "data/converted/test_call.wav",
        "mime_type": "audio/wav",
        "recruiter_name": "Jane Doe",
        "subject_name": "John Smith",
        "company_name": "Acme Corp",
        "notes": "Test notes",
        "call_type": "screening_call",
        "status": "uploaded",
    }
