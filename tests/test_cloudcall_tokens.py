"""Tests for CloudCall token cache management."""


def test_clear_stored_tokens_wipes_db_and_memory(tmp_path, monkeypatch):
    import cloudcall_service as ccs

    db_file = tmp_path / "tok.db"
    monkeypatch.setattr(ccs, "DB_PATH", db_file)

    # Seed a stale token in the DB + in-memory cache.
    ccs._store_tokens("stale-access", "stale-refresh", 9_999_999_999.0)
    ccs._access_token = "stale-access"
    ccs._access_token_expires_at = 9_999_999_999.0
    assert ccs._get_stored_tokens()["refresh_token"] == "stale-refresh"

    ccs.clear_stored_tokens()

    stored = ccs._get_stored_tokens()
    assert stored["refresh_token"] == ""
    assert stored["access_token"] == ""
    assert ccs._access_token == ""
    assert ccs._access_token_expires_at == 0.0


def test_clear_stored_tokens_safe_when_empty(tmp_path, monkeypatch):
    import cloudcall_service as ccs

    db_file = tmp_path / "empty.db"
    monkeypatch.setattr(ccs, "DB_PATH", db_file)

    # Should not raise even when nothing has been stored yet.
    ccs.clear_stored_tokens()
    assert ccs._get_stored_tokens()["refresh_token"] == ""
