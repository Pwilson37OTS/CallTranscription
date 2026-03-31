from unittest.mock import patch, MagicMock

import pytest


class TestPasswordHashing:
    def test_hash_and_verify(self):
        from auth import hash_password, verify_password

        hashed = hash_password("mypassword")
        assert hashed != "mypassword"
        assert verify_password("mypassword", hashed) is True
        assert verify_password("wrongpassword", hashed) is False

    def test_different_passwords_different_hashes(self):
        from auth import hash_password

        h1 = hash_password("password1")
        h2 = hash_password("password2")
        assert h1 != h2

    def test_same_password_different_salts(self):
        from auth import hash_password

        h1 = hash_password("samepass")
        h2 = hash_password("samepass")
        # bcrypt generates unique salts each time
        assert h1 != h2


class TestCreateUser:
    def test_creates_user_in_db(self, db_mod):
        from auth import create_user

        uid = create_user("newuser@example.com", "New User", "pass123", "recruiter")
        assert uid >= 1

        user = db_mod.get_user_by_email("newuser@example.com")
        assert user is not None
        assert user["display_name"] == "New User"
        assert user["role"] == "recruiter"

    def test_normalizes_email(self, db_mod):
        from auth import create_user

        create_user("  Admin@Example.COM  ", "Admin", "pass123")
        user = db_mod.get_user_by_email("admin@example.com")
        assert user is not None

    def test_strips_display_name(self, db_mod):
        from auth import create_user

        create_user("user@test.com", "  Spaced Name  ", "pass123")
        user = db_mod.get_user_by_email("user@test.com")
        assert user["display_name"] == "Spaced Name"


class TestAuthenticate:
    def test_successful_auth(self, db_mod):
        from auth import create_user, authenticate

        create_user("auth@test.com", "Auth User", "correctpass")
        result = authenticate("auth@test.com", "correctpass")
        assert result is not None
        assert result["email"] == "auth@test.com"

    def test_wrong_password(self, db_mod):
        from auth import create_user, authenticate

        create_user("auth@test.com", "Auth User", "correctpass")
        result = authenticate("auth@test.com", "wrongpass")
        assert result is None

    def test_nonexistent_user(self, db_mod):
        from auth import authenticate

        result = authenticate("nobody@test.com", "pass")
        assert result is None

    def test_inactive_user_rejected(self, db_mod):
        from auth import create_user, authenticate

        uid = create_user("inactive@test.com", "Inactive", "pass123")
        db_mod.update_user(uid, is_active=0)
        result = authenticate("inactive@test.com", "pass123")
        assert result is None

    def test_email_case_insensitive(self, db_mod):
        from auth import create_user, authenticate

        create_user("user@test.com", "User", "pass123")
        result = authenticate("USER@TEST.COM", "pass123")
        assert result is not None


class TestEnsureAdminExists:
    def test_creates_admin_when_no_users(self, db_mod, monkeypatch):
        monkeypatch.setattr("config.ADMIN_EMAIL", "seedadmin@test.com")
        monkeypatch.setattr("config.ADMIN_DEFAULT_PASSWORD", "adminpass")

        from auth import ensure_admin_exists
        ensure_admin_exists()

        user = db_mod.get_user_by_email("seedadmin@test.com")
        assert user is not None
        assert user["role"] == "admin"

    def test_does_not_create_when_users_exist(self, db_mod, monkeypatch):
        from auth import create_user, ensure_admin_exists

        create_user("existing@test.com", "Existing", "pass123")
        monkeypatch.setattr("config.ADMIN_EMAIL", "newadmin@test.com")
        monkeypatch.setattr("config.ADMIN_DEFAULT_PASSWORD", "adminpass")

        ensure_admin_exists()

        # New admin should NOT have been created
        assert db_mod.get_user_by_email("newadmin@test.com") is None


class TestSessionHelpers:
    """Test login/logout/get_current_user/is_admin using mocked st.session_state."""

    @patch("auth.st")
    def test_login_sets_session_state(self, mock_st):
        mock_st.session_state = {}
        from auth import login

        login({"id": 1, "email": "a@b.com", "display_name": "A", "role": "admin"})
        assert mock_st.session_state["authenticated"] is True
        assert mock_st.session_state["user_id"] == 1
        assert mock_st.session_state["user_role"] == "admin"

    @patch("auth.st")
    def test_logout_clears_session(self, mock_st):
        mock_st.session_state = {
            "authenticated": True,
            "user_id": 1,
            "user_email": "a@b.com",
            "user_name": "A",
            "user_role": "admin",
        }
        from auth import logout

        logout()
        assert "authenticated" not in mock_st.session_state
        assert "user_id" not in mock_st.session_state

    @patch("auth.st")
    def test_get_current_user_when_logged_in(self, mock_st):
        mock_st.session_state = {
            "authenticated": True,
            "user_id": 5,
            "user_email": "x@y.com",
            "user_name": "X",
            "user_role": "recruiter",
        }
        from auth import get_current_user

        user = get_current_user()
        assert user is not None
        assert user["id"] == 5

    @patch("auth.st")
    def test_get_current_user_when_not_logged_in(self, mock_st):
        mock_st.session_state = {}
        from auth import get_current_user

        assert get_current_user() is None

    @patch("auth.st")
    def test_is_admin(self, mock_st):
        mock_st.session_state = {"user_role": "admin"}
        from auth import is_admin
        assert is_admin() is True

        mock_st.session_state = {"user_role": "recruiter"}
        assert is_admin() is False

        mock_st.session_state = {}
        assert is_admin() is False
