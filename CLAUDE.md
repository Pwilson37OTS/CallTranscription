# ECHO — Claude Code Context

This file is auto-loaded by Claude Code at the start of every session. It's
the persistent handoff between sessions. Read it before making changes so
you don't undo decisions made deliberately.

## What ECHO is

Recruiter call review and coaching tool for OakTree Staffing. Ingests call
recordings from CloudCall (VoIP), transcribes them via OpenAI, diarizes
speaker turns, produces a candidate-information note structured against
per-call-type templates, and gives recruiters a copy-paste path into
Bullhorn (their ATS).

Rebranded from "Recruiter Call Review Tool" to ECHO. Only the visible
labels changed — the underlying file / DB / service names still say
"calltranscription". Don't rename those, it's not worth the migration.

## Architecture at a glance

Runtime shape (production, on Railway): one Docker container running four
processes under supervisord:

- **streamlit** on `127.0.0.1:8501` — the UI
- **uvicorn** on `127.0.0.1:8081` — FastAPI webhook receiver (installed but
  idle; no public ingress registered with CloudCall)
- **cloudcall_poller.py** — long-running polling loop, 5-min interval,
  business hours 6 AM–6 PM CT
- **nginx** on `0.0.0.0:8080` — routes `/webhooks/*` and `/health` to
  FastAPI, everything else to Streamlit

Storage: SQLite at `data/calls.db` (WAL mode was removed — Railway's
network-mounted volume was choking on WAL sidecars; DELETE mode is the
production choice, see `db.py`). Audio files live under `data/converted/`.

Data flow:

```
CloudCall API
   │
   ▼  (poll_recent_recordings — 5 min cadence, 60+ min lookback)
cloudcall_recordings table (raw ingest)
   │
   ▼  (auto-import if CloudCall user_id is mapped to an app user)
calls table (imported, ready to transcribe)
   │
   ▼  (ensure_transcript — auto-fires on Calls-tab selection)
transcript_text + speaker labels (diarize_transcript)
   │
   ▼  (Analyze Call button)
summary_text — candidate information note
   │
   ▼  (Copy for Bullhorn button — JS clipboard, not a real API call yet)
Bullhorn record (manual paste)
```

Retention: cleanup / display / manual-pull lookback all share
`CLOUDCALL_RECORDING_RETENTION_HOURS` (default 96h = 4 days). Bumping or
shrinking the env var moves all three windows together.

## Roles & permissions

Three roles: `admin`, `manager`, `recruiter`. Managers have team scoping
that recently split into two axes:

| Feature | Admin | Manager | Recruiter |
|---|---|---|---|
| Calls dropdown / Call Log visibility | all | **all** | own |
| User Management list | all | own team | — |
| Create user | full | recruiter on own team | — |
| Edit role / team | ✓ | — | — |
| Deactivate + Change Password | any | own team | — |
| API Usage analytics | all | own team | — |
| CloudCall Mappings | all | own team | — |
| Manual Upload | ✓ | — | — |
| CloudCall Inspector + Try Ingest | ✓ | — | — |
| Force Cleanup Now | ✓ | — | — |

The team column on `users` drives everything except call visibility.
Managers see all calls (across teams) because they cover for each other,
but user management + analytics stay team-scoped for accountability.
Implementation split: `scoped_user_ids` (team scope) vs
`calls_scoped_user_ids` (call visibility) in `app.py`.

## Key files

- `app.py` — Streamlit UI, auth gate, all tabs (Calls, Call Log,
  Templates only for admin, Admin/Team). Single file — don't refactor
  into modules unless there's a real reason.
- `db.py` — SQLite schema + CRUD for users, calls, api_usage. Idempotent
  ALTER TABLE migrations at the top of `init_db()`. `journal_mode = DELETE`
  (do not switch to WAL, it broke Railway).
- `auth.py` — bcrypt passwords, session-state login, `is_admin()` /
  `is_manager()` / `get_user_team()`.
- `cloudcall_service.py` — CloudCall API client + poller logic +
  diagnostic tools. This is the file with the most subtle behavior;
  read the whole thing before changing it.
- `cloudcall_db.py` — schema + CRUD for `cloudcall_recordings` and
  `cloudcall_user_mapping`.
- `cloudcall_config.py` — env-driven settings, especially
  `CLOUDCALL_RECORDING_RETENTION_HOURS` (used in 3+ places).
- `openai_service.py` — `transcribe_audio`, `diarize_transcript`,
  `analyze_call` (the coaching note prompt).
- `call_templates.py` — the two active templates (screening_call,
  post_interview_rundown). Standard Call was intentionally removed. To
  edit templates: modify this file, commit, redeploy. No UI editor.
- `cloudcall_poller.py` — the 24/7 poller process. Runs
  `poll_recent_recordings()` on a schedule.
- `webhook_server.py` — FastAPI receiver. Installed but not currently
  wired to a public ingress; polling is the active ingest path.
- `Dockerfile` — multi-stage: static ffmpeg from `mwader/static-ffmpeg`,
  BuildKit cache mounts with Railway-specific `id=s/<service>-<path>`
  format (Railway rejects generic ids).
- `supervisord.conf` — the four-process launcher inside the container.
- `nginx.conf` — routes `/webhooks/*` + `/health` to uvicorn, everything
  else to Streamlit.
- `DEPLOY.md` — the deployment runbook.

## Deployment

- **GitHub**: `Pwilson37OTS/CallTranscription`, branch `cloudcall-integration`.
- **Railway**: watches `cloudcall-integration`, auto-deploys on push.
- **Service ID** in cache mount format: `5da4782c-fcad-4ba0-a287-0107b4bdb9fb`
  (referenced in `Dockerfile` — DON'T change this unless you're moving
  to a different Railway service).
- **Env vars set in Railway**: `OPENAI_API_KEY`, `CLOUDCALL_REFRESH_TOKEN`,
  `ADMIN_EMAIL`, `ADMIN_DEFAULT_PASSWORD`, and any override of the retention
  hour value.
- **Volume**: 10 GB, mounted at `/app/data`. Default is 500 MB on the free
  tier — that was overrun once due to accumulated WAVs (see next section);
  the user later bumped to 10 GB.

## Hard-won gotchas — DO NOT re-fight these

1. **CloudCall refresh token rotates on every use.** Only one process
   can refresh at a time. If two environments share the same token, one
   kills the other on the next refresh. The token also dies for other
   reasons (revoked, expired). Auto-fallback tries stored-token then
   env-var-token; if both fail with `invalid_grant`, the token needs to
   be regenerated in CloudCall Workspace → Company Settings → CloudCall
   API, updated in Railway Variables, and the service restarted. This has
   happened MULTIPLE times.

2. **CloudCall's `call_logs` API pages at 100 rows.** With multiple
   recruiters making calls, a wide window can easily exceed one page.
   `fetch_call_logs` now paginates through up to 50 pages. Don't
   revert to page-1-only.

3. **CloudCall recording URLs are slow to finalize for long calls.**
   A 21-min call may not have its URL ready when we poll 5 min later.
   Old behavior: silent skip → call lost forever once the lookback
   window slid past it. Current fix: insert as `status='pending_url'`,
   retry on every subsequent poll, age out to `no_audio` after 24 h.
   See `retry_pending_url_recordings()`.

4. **CloudCall sometimes returns "no audio" for calls their own UI
   confirms have no recording.** We handle three flavors:
   - URL endpoint 404 or empty response → insert as `status='no_audio'`
   - Download returns HTML error page or <1024 bytes → import treats
     as no_audio via `download_recording`'s content-type + size checks
   - Legacy `error` rows with matching error message strings are shown
     as no_audio in the Call Log via a fallback pattern-match.

5. **Auto-poll lookback used to be 10 minutes.** Now 60 minutes minimum.
   Manual Pull uses the retention window (currently 4 days). Don't
   drop the auto-poll lookback back below 60 min — long calls will
   slip through.

6. **Recording files are kept in the format CloudCall sends** (usually
   MP3). We used to convert everything to WAV via ffmpeg; that inflated
   storage ~5×. OpenAI accepts MP3 directly. Manual uploads (admin
   backup path only) still go through `convert_file_to_wav` because
   they arrive in unknown formats.

7. **SQLite `journal_mode = DELETE` is deliberate.** WAL was failing on
   Railway's network volume with "disk I/O error." Don't switch back.

8. **Retention drives three windows in lockstep**: cleanup sweep, Call
   Log display, manual Pull lookback. All read
   `CLOUDCALL_RECORDING_RETENTION_HOURS` from `cloudcall_config.py`.
   Set in code + Railway env var together.

9. **Startup work (init_db + ensure_admin_exists + cleanup_old_files)
   is wrapped in `@st.cache_resource`.** Runs once per Streamlit server
   process. Running it on every rerun was making the app slow enough
   that WebSocket reconnects were kicking users to the login page mid-
   session. Don't move these back to module top-level.

10. **`Copy for Bullhorn` button uses a `<script>` block, not inline
    `onclick`.** An earlier version used `onclick='...'`, but if the
    call analysis contained an apostrophe (very common — "didn't",
    "you'll") the HTML attribute terminated early and the JS spilled
    onto the page as button text. The script-block version is not
    subject to HTML attribute escaping.

11. **`Analyze Call` output format has two distinct styles** in one
    document: general sections use clean factual prose (no quotes,
    summarized), Technical Screening Questions section uses verbatim
    quotes from the transcript. This is deliberate. The prompt in
    `analyze_call` calls out the exception explicitly.

12. **Templates are not editable in the UI.** Admin edits
    `call_templates.py` in the repo and redeploys. The user asked for
    a Templates tab and then asked for it to be removed and made
    admin-only, then asked for it removed entirely. It's gone.
    The tab exists in Git history if you need to revive it.

13. **Docker Build on Railway needs specific cache-mount format.**
    `id=s/<service-id>-<target-path>` in every `--mount=type=cache`.
    Generic ids get rejected by their validator. Don't touch the
    Dockerfile without preserving this.

14. **Retention default is 96 hours (4 days).** Was 72, was 168, now
    96. All changes were user-requested. Don't tune without asking.

## Diagnostic tools already built (Admin tab)

- **Storage Usage** — per-directory + DB file sizes.
- **Force Cleanup Now** — bypasses the 15-min throttle.
- **CloudCall Ingest Status** — total polled, unmapped, imported,
  breakdown by status. First place to check when calls seem missing.
- **Re-import Unmapped Recordings** — for calls that came in before
  a mapping existed.
- **Re-validate Stored Recordings** — audits imported calls, fixes
  those whose on-disk audio is missing/tiny.
- **CloudCall Call Inspector** — pulls raw CloudCall API data over an
  arbitrary time window, annotates each row with ECHO's ingestion
  status (`NOT IN DB` / `available` / `no_audio` / `pending_url` /
  etc.). This is the single most useful debug tool.
- **Try Ingest Missing Calls** — force-runs the ingest logic on any
  calls the Inspector flagged as `NOT IN DB`, reports per-call
  outcome (success, `url_fetch_error` with details, etc.).

## Bullhorn integration

Not built. The "Copy for Bullhorn" button is the current UX. Version
2.0 will add real API submission — OAuth2 setup, candidate lookup,
note-creation call. Placeholder text in the code refers to "v2.0"
but the user asked for that language removed from the UI, so it's
only in code comments now.

## Current state

- **Branch**: `cloudcall-integration` on `Pwilson37OTS/CallTranscription`
- **Latest deployed commit**: `a4b2b48` — Speed up app loading; drop
  retention to 4 days
- **Volume**: 10 GB on Railway (bumped from 500 MB after a fill-up).
- **Retention**: 96 h (4 days) default. Env-var override exists.
- **Auth model**: three roles, team scoping split into team-scope vs
  call-scope.
- **Known follow-ups the user has mentioned but not built**:
  - Poller Health UI in Admin tab (last successful poll timestamp,
    recent errors, red banner if last successful poll >1h ago). User
    asked for this after realizing a dead CloudCall token had silently
    stopped ingestion for days. Not yet implemented.
  - Real Bullhorn API integration.

## Working style with this user

- User is technically comfortable but not a coder — walk through
  changes and consequences, don't just dump diffs.
- User pushes to GitHub via me — they say "commit" and I run the git
  workflow. They typically approve changes first.
- Test suite lives under `tests/`; run `python -m pytest tests/ -q`
  after non-trivial changes. Should stay at all-passing.
- Commit messages use a `Co-Authored-By: Claude Opus 4.7 (1M context)
  <noreply@anthropic.com>` trailer.
- User's timezone thinking is Central. UI shows Central Time via
  `zoneinfo.ZoneInfo("America/Chicago")`. Internal DB timestamps
  are UTC ISO strings.
