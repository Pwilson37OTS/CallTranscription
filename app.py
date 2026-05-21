import os
from collections import defaultdict
from datetime import datetime
from time import time

import streamlit as st

from config import LOGO_PATH, RATE_LIMIT_PER_HOUR
from styles import APP_CSS
from db import (
    init_db, insert_call, update_call, get_all_calls, get_call,
    insert_api_usage, get_all_users, get_usage_summary, update_user,
)
from openai_service import transcribe_audio, diarize_transcript, analyze_call
from call_templates import CALL_TEMPLATES
from file_service import save_uploaded_file, ffmpeg_available
from auth import (
    authenticate, login, logout, get_current_user, is_admin,
    ensure_admin_exists, create_user,
)
from logging_config import logger
from storage import storage
from cloudcall_config import CLOUDCALL_ENABLED

# ============================================================
# Recruiter Call Review Tool
# Run with: streamlit run app.py
# ============================================================

st.set_page_config(page_title="Recruiter Call Review Tool", layout="wide")

# --- Startup guard: require OpenAI API key ---
if not os.getenv("OPENAI_API_KEY"):
    st.error("**OPENAI_API_KEY** environment variable is not set. Add it to your `.env` file and restart.")
    st.stop()

st.markdown(APP_CSS, unsafe_allow_html=True)

# Startup audio-file cleanup. Runs BEFORE init_db() so that if the persistent
# volume is full, we free space before SQLite tries to write its journal.
# Best-effort: a failure here must never block app boot.
try:
    from cloudcall_service import cleanup_old_files
    cleanup_old_files()
except Exception as _e:
    logger.warning("Startup file cleanup skipped: %s", _e)

init_db()
ensure_admin_exists()


# --- Rate limiting (in-memory, per-user, per-hour) ---
if "_rate_limits" not in st.session_state:
    st.session_state._rate_limits = defaultdict(list)


def _check_rate_limit(user_id: int) -> bool:
    now = time()
    window = st.session_state._rate_limits[user_id]
    st.session_state._rate_limits[user_id] = [t for t in window if now - t < 3600]
    return len(st.session_state._rate_limits[user_id]) < RATE_LIMIT_PER_HOUR


def _record_api_call(user_id: int):
    st.session_state._rate_limits[user_id].append(time())


# --- Defaults for the models (no longer user-selectable in the simplified UI) ---
DEFAULT_TRANSCRIPTION_MODEL = "gpt-4o-transcribe"
DEFAULT_DIARIZATION_MODEL = "gpt-4.1"


# -----------------------------
# Login page
# -----------------------------
def show_login_page():
    col_spacer_l, col_center, col_spacer_r = st.columns([1, 2, 1])
    with col_center:
        if LOGO_PATH.exists():
            st.image(str(LOGO_PATH), width=150)
        st.markdown("### Recruiter Call Review Tool")
        st.markdown("Sign in to continue.")

        with st.form("login_form"):
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign In", use_container_width=True)

            if submitted:
                if not email or not password:
                    st.error("Please enter your email and password.")
                else:
                    user = authenticate(email, password)
                    if user:
                        login(user)
                        logger.info("Login success: user_id=%d email=%s", user["id"], user["email"])
                        st.rerun()
                    else:
                        logger.warning("Login failed: email=%s", email)
                        st.error("Invalid email or password, or account is deactivated.")


# -----------------------------
# Auth gate
# -----------------------------
if not st.session_state.get("authenticated"):
    show_login_page()
    st.stop()

current_user = get_current_user()
current_user_id = current_user["id"]
user_is_admin = is_admin()
query_user_id = None if user_is_admin else current_user_id


# -----------------------------
# Sidebar (minimal — user info + sign out only)
# -----------------------------
with st.sidebar:
    st.markdown(f"**{current_user['display_name']}**")
    st.caption(current_user["email"])
    if user_is_admin:
        st.caption("Role: Admin")
    if st.button("Sign Out", use_container_width=True):
        logger.info("Logout: user_id=%d", current_user_id)
        logout()
        st.rerun()


# -----------------------------
# Header
# -----------------------------
hero_left, hero_right = st.columns([1, 5])
with hero_left:
    if LOGO_PATH.exists():
        st.image(str(LOGO_PATH), width=110)
with hero_right:
    st.markdown(
        """
        <div class="brand-hero">
            <h1>Recruiter Call Review Tool</h1>
            <p>Select a call, review the transcript, and submit it to Bullhorn as a note.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


# -----------------------------
# Helper: ensure a call has a diarized transcript (auto-transcribe on demand)
# -----------------------------
def ensure_transcript(call) -> str:
    """Return the diarized transcript for a call, generating it if needed."""
    if call["transcript_text"]:
        return call["transcript_text"]

    if not _check_rate_limit(current_user_id):
        raise RuntimeError(
            f"Rate limit exceeded ({RATE_LIMIT_PER_HOUR} API calls/hour). Please wait."
        )

    audio_path = storage.resolve(call["stored_path"])
    if not audio_path.exists():
        raise FileNotFoundError("Audio file is missing from storage.")

    logger.info("Auto-transcribe started: user_id=%d call_id=%d", current_user_id, call["id"])

    with st.spinner("Transcribing audio..."):
        raw_transcript = transcribe_audio(str(audio_path), model=DEFAULT_TRANSCRIPTION_MODEL)
    _record_api_call(current_user_id)
    insert_api_usage({
        "user_id": current_user_id,
        "call_id": call["id"],
        "operation": "transcription",
        "model": DEFAULT_TRANSCRIPTION_MODEL,
        "estimated_cost_cents": 0,
        "created_at": datetime.utcnow().isoformat(),
    })

    with st.spinner("Labeling speakers..."):
        labeled = diarize_transcript(
            transcript_text=raw_transcript,
            metadata={
                "recruiter_name": call["recruiter_name"],
                "subject_name": call["subject_name"],
            },
            model=DEFAULT_DIARIZATION_MODEL,
        )
    _record_api_call(current_user_id)
    insert_api_usage({
        "user_id": current_user_id,
        "call_id": call["id"],
        "operation": "diarization",
        "model": DEFAULT_DIARIZATION_MODEL,
        "estimated_cost_cents": 0,
        "created_at": datetime.utcnow().isoformat(),
    })

    update_call(
        call["id"],
        transcript_text=labeled,
        status="transcribed",
        transcription_model=DEFAULT_TRANSCRIPTION_MODEL,
        summary_model=DEFAULT_DIARIZATION_MODEL,
    )
    logger.info("Auto-transcribe complete: user_id=%d call_id=%d", current_user_id, call["id"])
    return labeled


# Note: ensure_summary / summarize_call were removed from the UI per user
# feedback. The summarize_call function and call_summary DB column are
# still in place so the Bullhorn-ready summary can be re-enabled cheaply
# if needed later.


# -----------------------------
# Tabs
# -----------------------------
tab_names = ["Calls", "Call Log"]
if user_is_admin:
    tab_names.extend(["Templates", "Admin"])

tabs = st.tabs(tab_names)
calls_tab = tabs[0]
log_tab = tabs[1]
templates_tab = tabs[2] if user_is_admin else None
admin_tab = tabs[3] if user_is_admin else None


# -----------------------------
# Calls tab (main workflow)
# -----------------------------
with calls_tab:
    st.subheader("Process a Call")

    # Reserve a slot at the top of the page for the Call Coaching section.
    # We render it later in the script (after we know which call is selected)
    # but it displays here, above the call selector.
    coaching_container = st.container()

    calls = get_all_calls(user_id=query_user_id)

    if not calls:
        if CLOUDCALL_ENABLED:
            st.info("No calls available yet. Use **Pull from CloudCall** on the Call Log tab to fetch recent recordings.")
        else:
            st.info("No calls available yet.")
    else:
        # Dropdown selector — labels match the Call Log style: Recruiter |
        # Contact (name or phone) | Date & time in Central Time.
        from zoneinfo import ZoneInfo as _ZI
        _UTC_TZ = _ZI("UTC")
        _CT_TZ = _ZI("America/Chicago")

        def _call_label(row) -> str:
            recruiter = (row["recruiter_name"] or "").strip() or "—"
            contact = (row["subject_name"] or "").strip()
            if not contact:
                # Fall back to a readable identifier if subject_name is blank
                # (older imports may not have the phone-number fallback).
                of = row["original_filename"] or ""
                contact = of if of and not of.startswith("cloudcall_") else "Unknown contact"
            try:
                dt = datetime.fromisoformat(row["created_at"])
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=_UTC_TZ)
                dt_ct = dt.astimezone(_CT_TZ)
                dt_str = dt_ct.strftime("%b %d, %I:%M %p CT")
            except Exception:
                dt_str = (row["created_at"] or "")[:16]
            return f"{recruiter} | {contact} | {dt_str}"

        options = {_call_label(row): row["id"] for row in calls}
        selected_label = st.selectbox("Select a call", list(options.keys()))
        selected_id = options[selected_label]
        call = get_call(selected_id, user_id=query_user_id)

        if call:
            # -------------------------
            # Call Coaching section — rendered AT THE TOP via the container
            # reserved before the dropdown. Defined here (after the call is
            # known) so the call-type default reflects the current call.
            # -------------------------
            coach_options = list(CALL_TEMPLATES.keys())
            default_idx = (
                coach_options.index(call["call_type"])
                if call["call_type"] in coach_options
                else 0
            )
            with coaching_container:
                st.markdown("### Call Coaching")

                coach_left, coach_right = st.columns([3, 1])
                with coach_left:
                    selected_call_type = st.selectbox(
                        "Call Type",
                        options=coach_options,
                        format_func=lambda k: CALL_TEMPLATES[k]["label"],
                        index=default_idx,
                        key=f"coach_type_{call['id']}",
                    )

                is_standard_call = selected_call_type == "standard_call"

                with coach_right:
                    st.markdown("<div style='height: 28px'></div>", unsafe_allow_html=True)
                    if is_standard_call:
                        analyze_clicked = False
                    else:
                        analyze_clicked = st.button(
                            "Analyze Call",
                            key=f"analyze_{call['id']}",
                            use_container_width=True,
                            disabled=not call["transcript_text"],
                            help="Run a template-based coaching evaluation on the transcript.",
                        )

                if is_standard_call:
                    st.info(
                        "Standard calls don't get a coaching evaluation. Use the "
                        "transcript below for your Bullhorn note. Switch to "
                        "**Screening Call** or **Post Interview Rundown** if you "
                        "want this call evaluated."
                    )
                else:
                    st.caption(
                        "Click **Analyze Call** once the transcript is ready to "
                        "run a coaching evaluation against the template. Results "
                        "appear at the bottom of the page."
                    )

                st.markdown("---")

            # -------------------------
            # Call details + transcript
            # -------------------------
            left, right = st.columns([1, 2])

            with left:
                st.markdown("### Call Details")
                try:
                    dt = datetime.fromisoformat(call["created_at"]).strftime("%b %d, %Y at %I:%M %p UTC")
                except Exception:
                    dt = call["created_at"]
                st.write(f"**Date:** {dt}")
                st.write(f"**Contact:** {call['subject_name'] or '—'}")
                st.write(f"**Recruiter:** {call['recruiter_name'] or '—'}")

                audio_path = storage.resolve(call["stored_path"])
                if audio_path.exists():
                    st.audio(str(audio_path))
                else:
                    st.warning("Audio file not found in storage.")

                st.markdown("---")
                st.markdown("### Bullhorn Destination")
                contact_label = call["subject_name"] or "Unmatched contact"
                st.info(
                    f"Note will be attached to: **{contact_label}**\n\n"
                    "_Bullhorn record matching arrives in Phase 2 — for now, submission is stubbed._"
                )

                already_submitted = call["status"] == "submitted"
                if already_submitted:
                    st.success("This call has already been submitted.")

                if st.button(
                    "Submit to Bullhorn",
                    key=f"submit_{call['id']}",
                    type="primary",
                    use_container_width=True,
                    disabled=already_submitted,
                ):
                    if not call["transcript_text"]:
                        st.error("Transcript not ready yet — please wait for transcription to complete.")
                    else:
                        update_call(call["id"], status="submitted")
                        logger.info(
                            "Stub Bullhorn submit: user_id=%d call_id=%d",
                            current_user_id, call["id"],
                        )
                        st.success("Submitted to Bullhorn (stub — Phase 2 wires this to the real API).")
                        st.rerun()

            with right:
                # Transcript only. The Bullhorn-Ready Summary section was
                # removed per user feedback — recruiters use the transcript
                # directly for Standard Calls and the coaching analysis (below
                # the columns) for Screening / Post Interview calls.
                st.markdown("### Transcript")
                try:
                    transcript_text = ensure_transcript(call)
                    st.text_area(
                        "Transcript",
                        value=transcript_text,
                        height=520,
                        disabled=True,
                        label_visibility="collapsed",
                        key=f"transcript_view_{call['id']}",
                    )
                except Exception as e:
                    logger.error(
                        "Auto-transcribe failed: user_id=%d call_id=%d error=%s",
                        current_user_id, call["id"], e,
                    )
                    st.error(f"Transcription failed: {e}")
                    if st.button("Retry Transcription", key=f"retry_{call['id']}"):
                        st.rerun()

            # -------------------------
            # Analyze action (triggered by the button up top in the coaching
            # container). Output renders below the columns.
            # -------------------------
            if analyze_clicked:
                if not _check_rate_limit(current_user_id):
                    st.error(
                        f"Rate limit exceeded ({RATE_LIMIT_PER_HOUR} API calls/hour). Please wait."
                    )
                    logger.warning("Rate limit hit on analyze: user_id=%d", current_user_id)
                else:
                    try:
                        template_cfg = CALL_TEMPLATES[selected_call_type]
                        logger.info(
                            "Call analysis started: user_id=%d call_id=%d call_type=%s",
                            current_user_id, call["id"], selected_call_type,
                        )
                        with st.spinner("Analyzing call against template..."):
                            analysis_text = analyze_call(
                                transcript_text=call["transcript_text"],
                                call_type_label=template_cfg["label"],
                                template=template_cfg["template"],
                                metadata={
                                    "recruiter_name": call["recruiter_name"],
                                    "subject_name": call["subject_name"],
                                },
                                model=DEFAULT_DIARIZATION_MODEL,
                            )
                        stored_analysis = (
                            f"_Analyzed as: **{template_cfg['label']}**_\n\n{analysis_text}"
                        )
                        update_call(
                            call["id"],
                            call_type=selected_call_type,
                            summary_text=stored_analysis,
                        )
                        _record_api_call(current_user_id)
                        insert_api_usage({
                            "user_id": current_user_id,
                            "call_id": call["id"],
                            "operation": "analysis",
                            "model": DEFAULT_DIARIZATION_MODEL,
                            "estimated_cost_cents": 0,
                            "created_at": datetime.utcnow().isoformat(),
                        })
                        logger.info(
                            "Call analysis complete: user_id=%d call_id=%d",
                            current_user_id, call["id"],
                        )
                        st.rerun()
                    except Exception as e:
                        logger.error(
                            "Call analysis failed: user_id=%d call_id=%d error=%s",
                            current_user_id, call["id"], e,
                        )
                        st.error(f"Analysis failed: {e}")

            # -------------------------
            # Coaching analysis output AT THE BOTTOM (for non-standard types)
            # -------------------------
            if not is_standard_call:
                st.markdown("---")
                st.markdown("### Call Analysis")
                if call["summary_text"]:
                    st.markdown(call["summary_text"])
                else:
                    st.caption(
                        "_No analysis yet for this call. Click **Analyze Call** "
                        "above to generate one._"
                    )


# -----------------------------
# Call Log tab (history view, no actions)
# -----------------------------
with log_tab:
    # Header row: title + Pull from CloudCall button (moved from Calls tab)
    if CLOUDCALL_ENABLED:
        log_col_title, log_col_refresh = st.columns([4, 1])
        with log_col_title:
            st.subheader("Call Log")
        with log_col_refresh:
            if st.button(
                "Pull from CloudCall",
                key="log_refresh",
                help="Check CloudCall for any new recordings from the last 72 hours.",
            ):
                try:
                    from cloudcall_service import poll_recent_recordings
                    with st.spinner("Checking CloudCall for new recordings..."):
                        n = poll_recent_recordings(lookback_minutes=72 * 60)
                    if n > 0:
                        st.success(f"Found {n} new recording(s).")
                    else:
                        st.info("No new recordings found.")
                    logger.info("CloudCall refresh: user_id=%d inserted=%d", current_user_id, n)
                    st.rerun()
                except Exception as e:
                    logger.error("CloudCall refresh failed: user_id=%d error=%s", current_user_id, e)
                    st.error(f"Refresh failed: {e}")
    else:
        st.subheader("Call Log")

    st.caption(
        "CloudCall recordings from the last 72 hours. "
        + ("Admin view — showing calls across all users." if user_is_admin
           else "Showing only calls associated with your CloudCall account.")
    )

    if not CLOUDCALL_ENABLED:
        st.info("CloudCall integration is not enabled.")
    else:
        from cloudcall_db import get_cloudcall_recordings as _get_cc_recs
        from cloudcall_service import import_recording_to_pipeline as _import_recording
        from zoneinfo import ZoneInfo as _ZoneInfo

        _CT = _ZoneInfo("America/Chicago")
        _UTC = _ZoneInfo("UTC")

        # Admins see every recording; non-admins are filtered by app_user_id
        # (set when the poller matches a CloudCall user mapping). Recordings
        # without a mapping are invisible to non-admins by design.
        log_scope_user_id = None if user_is_admin else current_user_id
        recordings = _get_cc_recs(user_id=log_scope_user_id, hours=72)

        if not recordings:
            st.info("No CloudCall recordings in the last 72 hours.")
        else:
            def _fmt_length(secs):
                if secs is None:
                    return "—"
                try:
                    secs = int(secs)
                except (TypeError, ValueError):
                    return "—"
                if secs < 60:
                    return f"{secs}s"
                if secs < 3600:
                    m, s = divmod(secs, 60)
                    return f"{m}m {s}s"
                h, rem = divmod(secs, 3600)
                m = rem // 60
                return f"{h}h {m}m"

            # Column layout (must match between header and body rows)
            col_widths = [1.8, 0.8, 1.8, 2.2, 0.9, 1.1, 1.1]

            hdr = st.columns(col_widths)
            hdr[0].markdown("**Date / Time (CT)**")
            hdr[1].markdown("**Length**")
            hdr[2].markdown("**Recruiter**")
            hdr[3].markdown("**Contact**")
            hdr[4].markdown("**Direction**")
            hdr[5].markdown("**Status**")
            hdr[6].markdown("**Import**")
            st.divider()

            for r in recordings:
                row = st.columns(col_widths)

                # Date / time in Central
                with row[0]:
                    try:
                        ts = datetime.fromisoformat((r["call_timestamp"] or "").replace(" ", "T"))
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=_UTC)
                        ts_ct = ts.astimezone(_CT)
                        st.write(ts_ct.strftime("%b %d, %I:%M %p"))
                    except Exception:
                        st.write((r["call_timestamp"] or "")[:16])

                # Length
                with row[1]:
                    st.write(_fmt_length(r["call_duration_seconds"]))

                # Recruiter
                with row[2]:
                    st.write(r["recruiter_name"] or r["caller_number"] or "—")

                # Contact (name + number when available)
                with row[3]:
                    contact = r["contact_name"] or ""
                    number = r["callee_number"] or ""
                    if contact and number:
                        st.write(f"{contact} ({number})")
                    elif contact:
                        st.write(contact)
                    elif number:
                        st.write(number)
                    else:
                        st.write("—")

                # Direction
                with row[4]:
                    st.write(r["direction"] or "—")

                # Status
                with row[5]:
                    status = (r["status"] or "").lower()
                    if status == "imported":
                        st.write("✓ Imported")
                    elif status == "error":
                        err_msg = r["error_message"] if "error_message" in r.keys() else ""
                        st.write("⚠ Error")
                        if err_msg:
                            st.caption(err_msg[:80] + ("…" if len(err_msg) > 80 else ""))
                    elif status == "importing":
                        st.write("Importing…")
                    else:
                        st.write(status.title() if status else "—")

                # Import action
                with row[6]:
                    importable = status in ("available", "error")
                    target_user_id = r["app_user_id"]
                    if importable and target_user_id is None:
                        st.caption("_Needs mapping_")
                    elif importable:
                        if st.button("Import", key=f"log_import_{r['id']}", use_container_width=True):
                            try:
                                with st.spinner("Importing…"):
                                    new_call_id = _import_recording(
                                        cloudcall_recording_db_id=r["id"],
                                        user_id=target_user_id,
                                        metadata={
                                            "recruiter_name": r["recruiter_name"] or "",
                                            "subject_name": r["contact_name"] or "",
                                        },
                                    )
                                st.success(f"Imported as call #{new_call_id}.")
                                logger.info(
                                    "Manual import (Call Log): user_id=%d recording_id=%d call_id=%d",
                                    current_user_id, r["id"], new_call_id,
                                )
                                st.rerun()
                            except Exception as e:
                                logger.error(
                                    "Manual import failed (Call Log): user_id=%d recording_id=%d error=%s",
                                    current_user_id, r["id"], e,
                                )
                                st.error(f"Failed: {e}")
                    else:
                        st.write("—")


# -----------------------------
# Templates tab (admin-only reference view)
# -----------------------------
if templates_tab is not None:
    with templates_tab:
        st.subheader("Call Templates")
        st.caption(
            "Admin-only view of the templates the Analyze Call feature evaluates "
            "transcripts against. To edit a template, modify call_templates.py "
            "in the repo and redeploy — Streamlit picks up changes on the next "
            "container start."
        )

        # Default-open the first one so the page lands on something visible
        for i, (key, cfg) in enumerate(CALL_TEMPLATES.items()):
            with st.expander(cfg["label"], expanded=(i == 0)):
                st.markdown(cfg["template"])


# -----------------------------
# Admin tab
# -----------------------------
if admin_tab is not None:
    with admin_tab:
        st.subheader("Administration")

        # --- User Management ---
        st.markdown("### User Management")
        users = get_all_users()
        if users:
            from auth import hash_password as _hash_password
            for u in users:
                col_name, col_email, col_role, col_status, col_action = st.columns([2, 3, 1, 1, 3])
                with col_name:
                    st.write(u["display_name"])
                with col_email:
                    st.write(u["email"])
                with col_role:
                    st.write(u["role"])
                with col_status:
                    st.write("Active" if u["is_active"] else "Inactive")
                with col_action:
                    if u["id"] != current_user_id:
                        action_a, action_b = st.columns(2)
                        with action_a:
                            if u["is_active"]:
                                if st.button("Deactivate", key=f"deact_{u['id']}", use_container_width=True):
                                    update_user(u["id"], is_active=0)
                                    st.rerun()
                            else:
                                if st.button("Activate", key=f"act_{u['id']}", use_container_width=True):
                                    update_user(u["id"], is_active=1)
                                    st.rerun()
                        with action_b:
                            if st.button("Change Password", key=f"changepw_btn_{u['id']}", use_container_width=True):
                                # Toggle the inline form for this user
                                key = f"_pw_edit_{u['id']}"
                                st.session_state[key] = not st.session_state.get(key, False)
                                st.rerun()

                # Inline password reset form, shown only when toggled for this user
                if u["id"] != current_user_id and st.session_state.get(f"_pw_edit_{u['id']}", False):
                    with st.form(f"pw_form_{u['id']}"):
                        st.markdown(f"**Reset password for {u['email']}**")
                        new_pw = st.text_input(
                            "New password", type="password", key=f"newpw_input_{u['id']}"
                        )
                        confirm_pw = st.text_input(
                            "Confirm password", type="password", key=f"confirmpw_input_{u['id']}"
                        )
                        pw_btn_cols = st.columns([1, 1, 4])
                        with pw_btn_cols[0]:
                            save_pw = st.form_submit_button("Save", use_container_width=True)
                        with pw_btn_cols[1]:
                            cancel_pw = st.form_submit_button("Cancel", use_container_width=True)

                        if save_pw:
                            if not new_pw:
                                st.error("New password is required.")
                            elif len(new_pw) < 6:
                                st.error("Password must be at least 6 characters.")
                            elif new_pw != confirm_pw:
                                st.error("Passwords do not match.")
                            else:
                                update_user(u["id"], password_hash=_hash_password(new_pw))
                                logger.info(
                                    "Admin password reset: target_user_id=%d by user_id=%d",
                                    u["id"], current_user_id,
                                )
                                st.success(f"Password reset for {u['email']}.")
                                st.session_state[f"_pw_edit_{u['id']}"] = False
                                st.rerun()
                        elif cancel_pw:
                            st.session_state[f"_pw_edit_{u['id']}"] = False
                            st.rerun()

        st.markdown("---")
        st.markdown("### Create New User")
        with st.form("create_user_form"):
            new_email = st.text_input("Email")
            new_name = st.text_input("Display Name")
            new_password = st.text_input("Password", type="password")
            new_role = st.selectbox("Role", options=["recruiter", "admin"])
            create_submitted = st.form_submit_button("Create User")
            if create_submitted:
                if not new_email or not new_name or not new_password:
                    st.error("All fields are required.")
                elif len(new_password) < 6:
                    st.error("Password must be at least 6 characters.")
                else:
                    try:
                        create_user(new_email, new_name, new_password, new_role)
                        st.success(f"User {new_email} created.")
                        st.rerun()
                    except Exception as e:
                        if "UNIQUE constraint" in str(e):
                            st.error("A user with that email already exists.")
                        else:
                            st.error(f"Failed to create user: {e}")

        # --- Storage Usage ---
        st.markdown("---")
        st.markdown("### Storage Usage")
        st.caption(
            f"Files older than the retention window "
            f"({int(os.getenv('CLOUDCALL_RECORDING_RETENTION_HOURS', '72'))} hours) "
            "are auto-pruned every 15 minutes via the background poller and on each "
            "container restart. Use Force Cleanup Now to run immediately."
        )
        try:
            from cloudcall_service import get_storage_breakdown
            breakdown = get_storage_breakdown()
            import pandas as _pd
            sdf = _pd.DataFrame(breakdown)
            sdf["Size"] = sdf["size_bytes"].map(lambda b: f"{b / (1024 * 1024):.1f} MB")
            sdf["Files"] = sdf["files"]
            sdf["Path"] = sdf["path"]
            st.dataframe(sdf[["Path", "Files", "Size"]], use_container_width=True, hide_index=True)
            total_mb = sum(b["size_bytes"] for b in breakdown) / (1024 * 1024)
            st.caption(f"**Total app data: {total_mb:.1f} MB**")
        except Exception as e:
            st.error(f"Could not read storage breakdown: {e}")

        if st.button("Force Cleanup Now", key="force_cleanup_btn", help="Run cleanup immediately, bypassing the 15-minute throttle."):
            try:
                from cloudcall_service import maybe_cleanup_expired
                with st.spinner("Running cleanup..."):
                    result = maybe_cleanup_expired(force=True)
                files = result.get("files_removed", 0)
                rows = result.get("db_rows_deleted", 0)
                if "error" in result:
                    st.error(f"Cleanup ran with errors: {result['error']}")
                else:
                    st.success(
                        f"Cleanup complete — removed {files} file(s) and {rows} expired CloudCall recording row(s)."
                    )
                logger.info(
                    "Admin force cleanup: user_id=%d files=%d rows=%d",
                    current_user_id, files, rows,
                )
                st.rerun()
            except Exception as e:
                logger.error("Force cleanup failed: user_id=%d error=%s", current_user_id, e)
                st.error(f"Cleanup failed: {e}")

        # --- API Usage ---
        st.markdown("---")
        st.markdown("### API Usage (Last 30 Days)")
        usage_summary = get_usage_summary(days=30)
        if usage_summary:
            import pandas as pd
            df = pd.DataFrame([dict(row) for row in usage_summary])
            df = df.rename(columns={
                "display_name": "User",
                "email": "Email",
                "total_calls": "API Calls",
                "transcriptions": "Transcriptions",
                "summarizations": "Summaries",
                "total_cost_cents": "Est. Cost ($)",
            })
            if "Est. Cost ($)" in df.columns:
                df["Est. Cost ($)"] = (df["Est. Cost ($)"].fillna(0) / 100).map("${:.2f}".format)
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("No API usage recorded yet.")

        # --- CloudCall Ingest Stats ---
        if CLOUDCALL_ENABLED:
            from cloudcall_service import get_cloudcall_ingest_stats, reimport_unmapped_recordings

            st.markdown("---")
            st.markdown("### CloudCall Ingest Status")
            st.caption(
                "Diagnostics for what the poller has pulled in. The background "
                "poller runs continuously during business hours (configurable) — "
                "you do not need the app to be open for calls to be collected."
            )
            try:
                stats = get_cloudcall_ingest_stats()
                stat_cols = st.columns(3)
                with stat_cols[0]:
                    st.metric("Total recordings polled", stats["total_recordings"])
                with stat_cols[1]:
                    st.metric("Currently unmapped", stats["unmapped_available"])
                with stat_cols[2]:
                    by_status = stats["by_status"]
                    st.metric("Imported into Call Log", by_status.get("imported", 0))
                with st.expander("Breakdown by status"):
                    for s, c in sorted(by_status.items()):
                        st.write(f"- **{s}**: {c}")
            except Exception as e:
                st.error(f"Could not read CloudCall stats: {e}")

            st.caption(
                "**Unmapped** recordings are CloudCall calls whose user ID is not yet "
                "linked to an app user. They will NOT show up in any recruiter's Call Log. "
                "Add the missing mapping below, then click Re-import to backfill."
            )
            if st.button(
                "Re-import Unmapped Recordings",
                key="reimport_btn",
                help="Scan for unmapped recordings whose CloudCall user is now mapped, and import them.",
            ):
                try:
                    with st.spinner("Scanning and re-importing..."):
                        result = reimport_unmapped_recordings()
                    st.success(
                        f"Scanned {result['scanned']} unmapped recording(s); "
                        f"{result['mappable']} had a current mapping; "
                        f"{result['succeeded']} imported successfully; "
                        f"{result['failed']} failed."
                    )
                    logger.info(
                        "Admin reimport: user_id=%d result=%s",
                        current_user_id, result,
                    )
                    st.rerun()
                except Exception as e:
                    logger.error("Re-import failed: user_id=%d error=%s", current_user_id, e)
                    st.error(f"Re-import failed: {e}")

        # --- CloudCall User Mappings ---
        if CLOUDCALL_ENABLED:
            from cloudcall_db import (
                get_all_cloudcall_user_mappings,
                upsert_cloudcall_user_mapping,
                delete_cloudcall_user_mapping,
                get_unmapped_cloudcall_user_ids,
            )

            st.markdown("---")
            st.markdown("### CloudCall User Mappings")
            st.caption("Map CloudCall user IDs / extensions to app users so recordings auto-import to the right recruiter.")

            mappings = get_all_cloudcall_user_mappings()
            if mappings:
                for m in mappings:
                    mc1, mc2, mc3, mc4 = st.columns([2, 2, 3, 1])
                    with mc1:
                        st.write(m["cloudcall_user_id"])
                    with mc2:
                        st.write(m["cloudcall_display_name"] or "—")
                    with mc3:
                        st.write(f"{m['app_user_name']} ({m['app_user_email']})")
                    with mc4:
                        if st.button("Delete", key=f"del_ccmap_{m['id']}"):
                            delete_cloudcall_user_mapping(m["id"])
                            st.rerun()
            else:
                st.info("No CloudCall user mappings configured yet.")

            unmapped_ids = get_unmapped_cloudcall_user_ids()
            if unmapped_ids:
                st.warning(
                    f"**{len(unmapped_ids)} unmapped CloudCall user ID(s)** have recordings without a linked app user: "
                    f"{', '.join(unmapped_ids)}"
                )

            st.markdown("#### Add New Mapping")
            with st.form("add_cc_mapping"):
                all_users_for_map = get_all_users()
                cc_user_id = st.text_input("CloudCall User ID / Extension")
                cc_display_name = st.text_input("CloudCall Display Name (optional)")
                app_user_options = {f"{u['display_name']} ({u['email']})": u["id"] for u in all_users_for_map}
                selected_app_user_label = st.selectbox(
                    "App User",
                    options=list(app_user_options.keys()),
                )
                mapping_submitted = st.form_submit_button("Save Mapping")
                if mapping_submitted:
                    if not cc_user_id:
                        st.error("CloudCall User ID is required.")
                    else:
                        selected_app_user_id = app_user_options[selected_app_user_label]
                        upsert_cloudcall_user_mapping(
                            cc_user_id.strip(),
                            selected_app_user_id,
                            cc_display_name.strip(),
                        )
                        st.success(f"Mapping saved: {cc_user_id} -> {selected_app_user_label}")
                        st.rerun()

        # --- Manual Upload (admin backup) ---
        st.markdown("---")
        st.markdown("### Manual Upload (Backup)")
        st.caption("Backup ingest path for cases when CloudCall didn't capture a call. Files upload into the currently signed-in admin's call list.")

        with st.form("manual_upload_form"):
            uploaded_file = st.file_uploader(
                "Audio file",
                type=None,
                help="Any audio format accepted — ffmpeg converts to WAV automatically.",
            )
            up_col1, up_col2 = st.columns(2)
            with up_col1:
                up_recruiter = st.text_input("Recruiter Name", value=current_user["display_name"])
                up_subject = st.text_input("Contact Name")
            with up_col2:
                up_company = st.text_input("Company / Client")
                up_notes = st.text_area("Notes", placeholder="Optional context.")
            up_submitted = st.form_submit_button("Save Recording")
            if up_submitted:
                if not uploaded_file:
                    st.error("Please choose an audio file.")
                else:
                    try:
                        file_info = save_uploaded_file(uploaded_file)
                        record = {
                            "created_at": datetime.utcnow().isoformat(),
                            **file_info,
                            "recruiter_name": up_recruiter,
                            "subject_name": up_subject,
                            "company_name": up_company,
                            "notes": up_notes,
                            "call_type": "standard_call",
                            "status": "uploaded",
                            "transcript_text": None,
                            "summary_text": None,
                            "transcription_model": None,
                            "summary_model": None,
                            "user_id": current_user_id,
                        }
                        new_id = insert_call(record)
                        logger.info(
                            "Manual upload: user_id=%d call_id=%d file=%s",
                            current_user_id, new_id, file_info.get("original_filename", ""),
                        )
                        st.success(f"Recording saved. Call ID: {new_id}")
                    except Exception as e:
                        logger.error("Manual upload failed: user_id=%d error=%s", current_user_id, e)
                        st.error(f"Upload failed: {e}")
                        if not ffmpeg_available():
                            st.info("Install ffmpeg, then restart Streamlit. Windows: `winget install Gyan.FFmpeg`")

        # The full per-row CloudCall inbox now lives on the Call Log tab
        # (admins see all calls there). The high-level ingest counts above
        # cover what this Admin section used to show.
