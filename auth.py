from typing import Optional, Dict, Any

import bcrypt
import streamlit as st

from db import get_user_by_email, insert_user, get_user_by_id


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def create_user(
    email: str,
    display_name: str,
    password: str,
    role: str = "recruiter",
    team: Optional[str] = None,
) -> int:
    record = {
        "email": email.lower().strip(),
        "display_name": display_name.strip(),
        "password_hash": hash_password(password),
        "role": role,
        "team": (team or None) if (team is None or team.strip()) else None,
    }
    return insert_user(record)


def authenticate(email: str, password: str) -> Optional[Dict[str, Any]]:
    user = get_user_by_email(email.lower().strip())
    if not user:
        return None
    if not user["is_active"]:
        return None
    if not verify_password(password, user["password_hash"]):
        return None
    return dict(user)


def login(user: Dict[str, Any]) -> None:
    st.session_state["authenticated"] = True
    st.session_state["user_id"] = user["id"]
    st.session_state["user_email"] = user["email"]
    st.session_state["user_name"] = user["display_name"]
    st.session_state["user_role"] = user["role"]
    # team is set for recruiters and managers. Managers manage the team
    # they're a member of. Admins typically have team = None.
    try:
        st.session_state["user_team"] = user["team"]
    except (KeyError, IndexError, TypeError):
        st.session_state["user_team"] = None
    # Drives the forced password-change gate (default password on a new
    # account, or a temporary password set by an admin/manager reset).
    try:
        st.session_state["must_change_password"] = bool(user["must_change_password"])
    except (KeyError, IndexError, TypeError):
        st.session_state["must_change_password"] = False


def logout() -> None:
    for key in [
        "authenticated", "user_id", "user_email", "user_name", "user_role", "user_team",
        "must_change_password",
    ]:
        st.session_state.pop(key, None)


def get_current_user() -> Optional[Dict[str, Any]]:
    if not st.session_state.get("authenticated"):
        return None
    return {
        "id": st.session_state["user_id"],
        "email": st.session_state["user_email"],
        "display_name": st.session_state["user_name"],
        "role": st.session_state["user_role"],
        "team": st.session_state.get("user_team"),
    }


def is_admin() -> bool:
    return st.session_state.get("user_role") == "admin"


def is_manager() -> bool:
    return st.session_state.get("user_role") == "manager"


def get_user_team() -> Optional[str]:
    """The team the current user belongs to (and manages, if they're a manager)."""
    return st.session_state.get("user_team")


def require_auth() -> Dict[str, Any]:
    user = get_current_user()
    if not user:
        st.error("Please log in to continue.")
        st.stop()
    return user


def ensure_admin_exists() -> None:
    """Create the seed admin user if no users exist yet."""
    from config import ADMIN_EMAIL, ADMIN_DEFAULT_PASSWORD
    from db import get_all_users

    users = get_all_users()
    if len(users) == 0:
        create_user(
            email=ADMIN_EMAIL,
            display_name="Admin",
            password="changeme123" if not ADMIN_DEFAULT_PASSWORD else ADMIN_DEFAULT_PASSWORD,
            role="admin",
        )
