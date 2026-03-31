from datetime import datetime
from pathlib import Path

import streamlit as st

from config import APP_DIR, LOGO_PATH
from styles import APP_CSS
from prompts import CALL_TYPE_CONFIG
from db import (
    init_db, insert_call, update_call, get_all_calls, get_call,
    insert_api_usage, get_all_users, get_usage_summary,
)
from openai_service import transcribe_audio, summarize_transcript
from file_service import save_uploaded_file, ffmpeg_available
from auth import (
    authenticate, login, logout, get_current_user, is_admin,
    ensure_admin_exists, create_user,
)
from db import update_user

# ============================================================
# Recruiter Call Review Tool
# Run with: streamlit run app.py
# ============================================================

st.set_page_config(page_title="Recruiter Call Review Tool", layout="wide")
st.markdown(APP_CSS, unsafe_allow_html=True)

init_db()
ensure_admin_exists()


def resolve_audio_path(stored_path: str) -> Path:
    """Resolve a stored path (relative or absolute) to an absolute path."""
    p = Path(stored_path)
    if p.is_absolute():
        return p
    return APP_DIR / p


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
                        st.rerun()
                    else:
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

# For user-scoped queries: admin sees all (user_id=None), recruiter sees own
query_user_id = None if user_is_admin else current_user_id


# -----------------------------
# Sidebar
# -----------------------------
with st.sidebar:
    st.markdown(f"**{current_user['display_name']}**")
    st.caption(current_user["email"])
    if user_is_admin:
        st.caption("Role: Admin")
    if st.button("Sign Out", use_container_width=True):
        logout()
        st.rerun()

    st.markdown("---")
    st.header("Settings")
    st.caption(f"ffmpeg detected: {'Yes' if ffmpeg_available() else 'No'}")

    transcription_model = st.selectbox(
        "Transcription Model",
        options=["gpt-4o-transcribe", "gpt-4o-mini-transcribe", "whisper-1"],
        index=0,
    )

    summary_model = st.selectbox(
        "Summary Model",
        options=["gpt-4.1", "gpt-4o", "gpt-4o-mini"],
        index=0,
    )


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
            <p>Upload audio, auto-convert to WAV, transcribe, generate dynamic call-type summaries, review, and copy ATS-ready notes.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

# Build tabs — admin gets an extra tab
tab_names = ["Call Queue", "Upload New Recording"]
if user_is_admin:
    tab_names.append("Admin")

tabs = st.tabs(tab_names)
queue_tab = tabs[0]
upload_tab = tabs[1]
admin_tab = tabs[2] if user_is_admin else None

# -----------------------------
# Upload tab
# -----------------------------
with upload_tab:
    st.subheader("Upload a New Recording")

    with st.form("upload_form"):
        uploaded_file = st.file_uploader(
            "Audio file (extensions optional)",
            type=None,
            help="Files without extensions are accepted. The app will attempt to convert anything uploaded into WAV using ffmpeg.",
        )

        col1, col2 = st.columns(2)
        with col1:
            recruiter_name = st.text_input("Recruiter Name", value=current_user["display_name"])
            subject_name = st.text_input("Candidate / Reference / Contact Name")
        with col2:
            company_name = st.text_input("Company / Client")
            call_type = st.selectbox(
                "Default Call Type",
                options=list(CALL_TYPE_CONFIG.keys()),
                format_func=lambda k: CALL_TYPE_CONFIG[k]["label"],
                index=0,
            )

        notes = st.text_area("Internal Notes / Context", placeholder="Optional context to help the summary.")
        submitted = st.form_submit_button("Save Recording")

        if submitted:
            if not uploaded_file:
                st.error("Please upload an audio file.")
            else:
                try:
                    file_info = save_uploaded_file(uploaded_file)
                    record = {
                        "created_at": datetime.utcnow().isoformat(),
                        **file_info,
                        "recruiter_name": recruiter_name,
                        "subject_name": subject_name,
                        "company_name": company_name,
                        "notes": notes,
                        "call_type": call_type,
                        "status": "uploaded",
                        "transcript_text": None,
                        "summary_text": None,
                        "transcription_model": None,
                        "summary_model": None,
                        "user_id": current_user_id,
                    }
                    new_id = insert_call(record)
                    st.success(f"Recording saved. Call ID: {new_id}")
                except Exception as e:
                    st.error(f"Upload failed: {e}")
                    if not ffmpeg_available():
                        st.info(
                            "Install ffmpeg, then restart Streamlit. On Windows, the easiest route is: winget install Gyan.FFmpeg"
                        )


# -----------------------------
# Queue tab
# -----------------------------
with queue_tab:
    st.subheader("Call Queue")
    calls = get_all_calls(user_id=query_user_id)

    if not calls:
        st.info("No calls saved yet. Upload a recording to get started.")
    else:

        def _call_label(row) -> str:
            candidate = row["subject_name"] or row["original_filename"]
            call_type_label = CALL_TYPE_CONFIG.get(row["call_type"] or "", {}).get(
                "label", row["call_type"] or ""
            )
            status = row["status"].replace("_", " ").title()
            return f"#{row['id']} | {candidate} | {call_type_label} | {status}"

        options = {_call_label(row): row["id"] for row in calls}
        selected_label = st.selectbox("Select a call", list(options.keys()))
        selected_id = options[selected_label]
        call = get_call(selected_id, user_id=query_user_id)

        if call:
            left, right = st.columns([1, 1])

            with left:
                st.markdown("### Call Details")
                st.write(f"**Call ID:** {call['id']}")
                try:
                    created_dt = datetime.fromisoformat(call["created_at"])
                    friendly_date = created_dt.strftime("%b %d, %Y at %I:%M %p UTC")
                except Exception:
                    friendly_date = call["created_at"]
                st.write(f"**Uploaded:** {friendly_date}")
                st.write(f"**Original File:** {call['original_filename']}")

                status_text = call["status"].replace("_", " ").title()
                st.write(f"**Status:** {status_text}")

                edit_recruiter_name = st.text_input(
                    "Recruiter Name",
                    value=call["recruiter_name"] or "",
                    key=f"recruiter_{call['id']}",
                )
                edit_subject_name = st.text_input(
                    "Candidate / Reference / Contact Name",
                    value=call["subject_name"] or "",
                    key=f"subject_{call['id']}",
                )
                edit_company_name = st.text_input(
                    "Company / Client",
                    value=call["company_name"] or "",
                    key=f"company_{call['id']}",
                )
                edit_notes = st.text_area(
                    "Internal Notes / Context",
                    value=call["notes"] or "",
                    key=f"notes_{call['id']}",
                )
                edit_call_type = st.selectbox(
                    "Call Type",
                    options=list(CALL_TYPE_CONFIG.keys()),
                    format_func=lambda k: CALL_TYPE_CONFIG[k]["label"],
                    index=(
                        list(CALL_TYPE_CONFIG.keys()).index(call["call_type"])
                        if call["call_type"] in CALL_TYPE_CONFIG
                        else 0
                    ),
                    key=f"type_{call['id']}",
                )

                col_a, col_b = st.columns(2)
                with col_a:
                    if st.button("Save Metadata", key=f"save_meta_{call['id']}"):
                        update_call(
                            call["id"],
                            recruiter_name=edit_recruiter_name,
                            subject_name=edit_subject_name,
                            company_name=edit_company_name,
                            notes=edit_notes,
                            call_type=edit_call_type,
                        )
                        st.success("Metadata saved.")
                        st.rerun()

                audio_path = resolve_audio_path(call["stored_path"])

                with col_b:
                    if audio_path.exists():
                        with open(audio_path, "rb") as audio_bytes:
                            st.download_button(
                                "Download Audio",
                                data=audio_bytes,
                                file_name=call["original_filename"],
                                mime=call["mime_type"] or "application/octet-stream",
                                key=f"download_{call['id']}",
                            )

                if audio_path.exists():
                    st.audio(str(audio_path))

                st.markdown("---")
                st.markdown("### Actions")
                if st.button("Transcribe", key=f"transcribe_{call['id']}"):
                    try:
                        with st.spinner("Transcribing audio..."):
                            transcript_text = transcribe_audio(
                                str(audio_path), model=transcription_model
                            )
                            update_call(
                                call["id"],
                                transcript_text=transcript_text,
                                status="transcribed",
                                transcription_model=transcription_model,
                                recruiter_name=edit_recruiter_name,
                                subject_name=edit_subject_name,
                                company_name=edit_company_name,
                                notes=edit_notes,
                                call_type=edit_call_type,
                            )
                        insert_api_usage({
                            "user_id": current_user_id,
                            "call_id": call["id"],
                            "operation": "transcription",
                            "model": transcription_model,
                            "estimated_cost_cents": 0,
                            "created_at": datetime.utcnow().isoformat(),
                        })
                        st.success("Transcription complete.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Transcription failed: {e}")

                if st.button("Generate ATS Summary", key=f"summarize_{call['id']}"):
                    refreshed_call = get_call(call["id"], user_id=query_user_id)
                    transcript_text = refreshed_call["transcript_text"]
                    if not transcript_text:
                        st.error("Please transcribe the call first.")
                    else:
                        try:
                            with st.spinner("Generating ATS-ready summary..."):
                                summary_text = summarize_transcript(
                                    transcript_text=transcript_text,
                                    call_type=edit_call_type,
                                    metadata={
                                        "recruiter_name": edit_recruiter_name,
                                        "subject_name": edit_subject_name,
                                        "company_name": edit_company_name,
                                        "notes": edit_notes,
                                    },
                                    model=summary_model,
                                )
                                update_call(
                                    call["id"],
                                    summary_text=summary_text,
                                    status="summarized",
                                    summary_model=summary_model,
                                    recruiter_name=edit_recruiter_name,
                                    subject_name=edit_subject_name,
                                    company_name=edit_company_name,
                                    notes=edit_notes,
                                    call_type=edit_call_type,
                                )
                            insert_api_usage({
                                "user_id": current_user_id,
                                "call_id": call["id"],
                                "operation": "summarization",
                                "model": summary_model,
                                "estimated_cost_cents": 0,
                                "created_at": datetime.utcnow().isoformat(),
                            })
                            st.success("Summary generated.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Summary failed: {e}")

                st.markdown("---")
                if st.button(
                    "Transcribe & Summarize",
                    key=f"process_{call['id']}",
                    help="Run transcription then immediately generate the ATS summary in one step.",
                ):
                    try:
                        with st.spinner("Transcribing audio..."):
                            transcript_text = transcribe_audio(
                                str(audio_path), model=transcription_model
                            )
                            update_call(
                                call["id"],
                                transcript_text=transcript_text,
                                status="transcribed",
                                transcription_model=transcription_model,
                                recruiter_name=edit_recruiter_name,
                                subject_name=edit_subject_name,
                                company_name=edit_company_name,
                                notes=edit_notes,
                                call_type=edit_call_type,
                            )
                        insert_api_usage({
                            "user_id": current_user_id,
                            "call_id": call["id"],
                            "operation": "transcription",
                            "model": transcription_model,
                            "estimated_cost_cents": 0,
                            "created_at": datetime.utcnow().isoformat(),
                        })
                        with st.spinner("Generating ATS-ready summary..."):
                            summary_text = summarize_transcript(
                                transcript_text=transcript_text,
                                call_type=edit_call_type,
                                metadata={
                                    "recruiter_name": edit_recruiter_name,
                                    "subject_name": edit_subject_name,
                                    "company_name": edit_company_name,
                                    "notes": edit_notes,
                                },
                                model=summary_model,
                            )
                            update_call(
                                call["id"],
                                summary_text=summary_text,
                                status="summarized",
                                summary_model=summary_model,
                            )
                        insert_api_usage({
                            "user_id": current_user_id,
                            "call_id": call["id"],
                            "operation": "summarization",
                            "model": summary_model,
                            "estimated_cost_cents": 0,
                            "created_at": datetime.utcnow().isoformat(),
                        })
                        st.success("Transcription and summary complete.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Processing failed: {e}")

            with right:
                st.markdown("### Transcript")
                transcript_value = st.text_area(
                    "Transcript Text",
                    value=call["transcript_text"] or "",
                    height=260,
                )
                if st.button(
                    "Save Edited Transcript", key=f"save_transcript_{call['id']}"
                ):
                    update_call(call["id"], transcript_text=transcript_value)
                    st.success("Transcript saved.")
                    st.rerun()

                st.markdown("### ATS Summary")
                summary_value = st.text_area(
                    "ATS-Ready Note",
                    value=call["summary_text"] or "",
                    height=360,
                )

                col_c, col_d = st.columns(2)
                with col_c:
                    if st.button(
                        "Save Edited Summary", key=f"save_summary_{call['id']}"
                    ):
                        update_call(
                            call["id"],
                            summary_text=summary_value,
                            status="reviewed",
                        )
                        st.success("Summary saved.")
                        st.rerun()
                with col_d:
                    st.download_button(
                        "Download Summary TXT",
                        data=(summary_value or "").encode("utf-8"),
                        file_name=f"call_{call['id']}_summary.txt",
                        mime="text/plain",
                        key=f"dl_summary_{call['id']}",
                    )

                st.caption(
                    "Tip: edit the transcript or summary above before copying into your ATS. Use the code block below to copy cleanly."
                )

                if call["summary_text"]:
                    st.markdown("### Copy to ATS")
                    st.code(call["summary_text"], language=None)

                with st.expander("Diagnostics"):
                    file_exists = audio_path.exists()
                    st.write(f"**File Present:** {'Yes' if file_exists else 'No'}")
                    if file_exists:
                        size_kb = audio_path.stat().st_size / 1024
                        st.write(f"**File Size:** {size_kb:.1f} KB")
                    st.write(
                        f"**ffmpeg Detected:** {'Yes' if ffmpeg_available() else 'No'}"
                    )
                    st.write(
                        f"**Transcription Model:** {call['transcription_model'] or transcription_model}"
                    )
                    st.write(
                        f"**Summary Model:** {call['summary_model'] or summary_model}"
                    )


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
