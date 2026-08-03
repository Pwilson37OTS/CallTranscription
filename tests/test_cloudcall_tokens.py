"""Tests for CloudCall token cache management."""

from time import time


def test_get_access_token_uses_valid_stored_without_refresh(tmp_path, monkeypatch):
    import cloudcall_service as ccs

    monkeypatch.setattr(ccs, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(ccs, "_access_token", "")
    monkeypatch.setattr(ccs, "_access_token_expires_at", 0.0)
    ccs._store_tokens("STORED-ACC", "STORED-REFRESH", time() + 100_000)

    def boom(*a, **k):
        raise AssertionError("must not refresh when a valid stored token exists")

    monkeypatch.setattr(ccs, "_do_refresh", boom)
    assert ccs.get_access_token() == "STORED-ACC"


def test_get_access_token_refreshes_and_persists(tmp_path, monkeypatch):
    import cloudcall_service as ccs

    monkeypatch.setattr(ccs, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(ccs, "CLOUDCALL_REFRESH_TOKEN", "env-seed")
    monkeypatch.setattr(ccs, "_access_token", "")
    monkeypatch.setattr(ccs, "_access_token_expires_at", 0.0)

    calls = []

    def fake_refresh(refresh_token, source):
        calls.append((refresh_token, source))
        return {"access_token": "ACC", "refresh_token": "ROTATED", "expires_in": 86400}

    monkeypatch.setattr(ccs, "_do_refresh", fake_refresh)

    token = ccs.get_access_token()
    assert token == "ACC"
    # No stored token yet → bootstraps from the env seed.
    assert calls == [("env-seed", "env")]
    # Rotated token is persisted for other processes.
    stored = ccs._get_stored_tokens()
    assert stored["refresh_token"] == "ROTATED"
    assert stored["access_token"] == "ACC"


def test_refresh_releases_lock_on_failure(tmp_path, monkeypatch):
    import cloudcall_service as ccs

    monkeypatch.setattr(ccs, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(ccs, "CLOUDCALL_REFRESH_TOKEN", "env-seed")
    monkeypatch.setattr(ccs, "_access_token", "")
    monkeypatch.setattr(ccs, "_access_token_expires_at", 0.0)

    def failing_refresh(refresh_token, source):
        raise RuntimeError("invalid_grant")

    monkeypatch.setattr(ccs, "_do_refresh", failing_refresh)

    import pytest
    with pytest.raises(RuntimeError, match="invalid_grant"):
        ccs.get_access_token()
    # Lock must be released — a second attempt should reach the refresh again,
    # not block/deadlock on the prior transaction.
    with pytest.raises(RuntimeError, match="invalid_grant"):
        ccs.get_access_token()


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
