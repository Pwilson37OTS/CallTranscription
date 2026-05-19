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


# -----------------------------
# Tabs
# -----------------------------
tab_names = ["Calls", "Call Log"]
if user_is_admin:
    tab_names.append("Admin")

tabs = st.tabs(tab_names)
calls_tab = tabs[0]
log_tab = tabs[1]
admin_tab = tabs[2] if user_is_admin else None


# -----------------------------
# Calls tab (main workflow)
# -----------------------------
with calls_tab:
    # Header row with refresh button (when CloudCall enabled)
    if CLOUDCALL_ENABLED:
        col_title, col_refresh = st.columns([4, 1])
        with col_title:
            st.subheader("Process a Call")
        with col_refresh:
            if st.button(
                "Pull from CloudCall",
                key="main_refresh",
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
        st.subheader("Process a Call")

    calls = get_all_calls(user_id=query_user_id)

    if not calls:
        if CLOUDCALL_ENABLED:
            st.info("No calls available yet. Click **Pull from CloudCall** above to fetch recent recordings.")
        else:
            st.info("No calls available yet.")
    else:
        # Dropdown selector
        def _call_label(row) -> str:
            who = row["subject_name"] or row["original_filename"] or "Unknown"
            try:
                dt = datetime.fromisoformat(row["created_at"]).strftime("%b %d, %I:%M %p")
            except Exception:
                dt = (row["created_at"] or "")[:16]
            return f"#{row['id']} | {who} | {dt}"

        options = {_call_label(row): row["id"] for row in calls}
        selected_label = st.selectbox("Select a call", list(options.keys()))
        selected_id = options[selected_label]
        call = get_call(selected_id, user_id=query_user_id)

        if call:
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
            # Call Coaching section
            # -------------------------
            st.markdown("---")
            st.markdown("### Call Coaching")
            st.caption(
                "Pick the type of call and click Analyze to evaluate how well the "
                "transcript matched the template for that call type."
            )

            coach_options = list(CALL_TEMPLATES.keys())
            default_idx = (
                coach_options.index(call["call_type"])
                if call["call_type"] in coach_options
                else 0
            )

            coach_left, coach_right = st.columns([3, 1])
            with coach_left:
                selected_call_type = st.selectbox(
                    "Call Type",
                    options=coach_options,
                    format_func=lambda k: CALL_TEMPLATES[k]["label"],
                    index=default_idx,
                    key=f"coach_type_{call['id']}",
                )
            with coach_right:
                st.markdown("<div style='height: 28px'></div>", unsafe_allow_html=True)
                analyze_clicked = st.button(
                    "Analyze Call",
                    key=f"analyze_{call['id']}",
                    use_container_width=True,
                    disabled=not call["transcript_text"],
                    help="Compare the transcript against the template for the selected call type.",
                )

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
                        # Tag the saved analysis with which call type it was run against
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

            if call["summary_text"]:
                st.markdown(call["summary_text"])
            else:
                st.caption(
                    "_No analysis yet for this call. Pick a call type above and "
                    "click Analyze Call._"
                )


# -----------------------------
# Call Log tab (history view, no actions)
# -----------------------------
with log_tab:
    st.subheader("Call Log")
    st.caption("History of all your calls with audio and transcripts. No actions — use the Calls tab to process or submit.")

    log_calls = get_all_calls(user_id=query_user_id)
    if not log_calls:
        st.info("No calls in your log yet.")
    else:
        for row in log_calls:
            try:
                dt_short = datetime.fromisoformat(row["created_at"]).strftime("%b %d, %Y %I:%M %p")
            except Exception:
                dt_short = (row["created_at"] or "")[:16]
            who = row["subject_name"] or row["original_filename"] or "Unknown"
            status_pretty = (row["status"] or "").replace("_", " ").title()

            with st.expander(f"#{row['id']} | {who} | {dt_short} | {status_pretty}"):
                cols = st.columns([1, 2])
                with cols[0]:
                    st.write(f"**Contact:** {row['subject_name'] or '—'}")
                    st.write(f"**Recruiter:** {row['recruiter_name'] or '—'}")
                    st.write(f"**Status:** {status_pretty}")
                    ap = storage.resolve(row["stored_path"])
                    if ap.exists():
                        st.audio(str(ap))
                    else:
                        st.caption("_Audio file not found in storage._")
                with cols[1]:
                    if row["transcript_text"]:
                        st.text_area(
                            "Transcript",
                            value=row["transcript_text"],
                            height=260,
                            disabled=True,
                            key=f"log_t_{row['id']}",
                            label_visibility="collapsed",
                        )
                    else:
                        st.caption("_Transcript not yet generated. Open this call on the Calls tab to transcribe it._")

                if row["summary_text"]:
                    st.markdown("**Coaching Analysis**")
                    st.markdown(row["summary_text"])


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
            for u in users:
                col_name, col_email, col_role, col_status, col_action = st.columns([2, 3, 1, 1, 2])
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
                        if u["is_active"]:
                            if st.button("Deactivate", key=f"deact_{u['id']}"):
                                update_user(u["id"], is_active=0)
                                st.rerun()
                        else:
                            if st.button("Activate", key=f"act_{u['id']}"):
                                update_user(u["id"], is_active=1)
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
                            "call_type": "general_recruiter_call",
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

        # --- CloudCall Raw Inbox (admin troubleshooting) ---
        if CLOUDCALL_ENABLED:
            from cloudcall_db import (
                get_cloudcall_recordings,
                get_cloudcall_recording,
                update_cloudcall_recording,
            )
            from cloudcall_service import import_recording_to_pipeline, maybe_cleanup_expired

            maybe_cleanup_expired()

            st.markdown("---")
            st.markdown("### CloudCall Raw Inbox")
            st.caption(
                "Recordings polled from CloudCall in the last 72 hours. Mapped recordings are auto-imported. "
                "Use this section to troubleshoot unmapped or failed imports."
            )

            inbox = get_cloudcall_recordings(user_id=None, hours=72)
            if not inbox:
                st.info("No CloudCall recordings in the last 72 hours.")
            else:
                import pandas as pd
                rows = []
                for r in inbox:
                    try:
                        ts = datetime.fromisoformat((r["call_timestamp"] or "").replace(" ", "T")).strftime("%b %d %I:%M %p")
                    except Exception:
                        ts = (r["call_timestamp"] or "")[:16]
                    rows.append({
                        "Date": ts,
                        "CC User": r["cloudcall_user_id"] or "—",
                        "Recruiter": r["recruiter_name"] or "—",
                        "Contact": r["contact_name"] or r["callee_number"] or "—",
                        "Direction": r["direction"] or "—",
                        "Status": r["status"],
                        "Call ID": r["imported_call_id"] if "imported_call_id" in r.keys() else None,
                        "Error": (r["error_message"] or "") if "error_message" in r.keys() else "",
                    })
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
