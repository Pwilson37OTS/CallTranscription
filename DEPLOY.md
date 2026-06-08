# Deployment Runbook

Internal-network deployment of ECHO (the recruiter call review tool) using Docker
Compose. The image runs four supervised processes (Streamlit, FastAPI
webhook receiver, CloudCall poller, nginx front-door) inside one container.

This setup assumes:

- Recruiters access the app over the corporate network or VPN — no public
  internet exposure required.
- One Docker host (Windows or Linux) with restart-on-boot and unattended
  updates available.
- A network fileshare reachable from the host for nightly DB backups.

For external/public deployment or multi-instance scaling, see "Future
production hardening" at the bottom.

---

## Prerequisites

- Docker Engine 24+ and Docker Compose v2 on the host.
- Outbound HTTPS access from the host to `api.openai.com`, `apio1.cloudcall.com`,
  and `auth.cloudcall.com`.
- A populated `.env` file in the project root. Start from `.env.example`.
- Valid `OPENAI_API_KEY`.
- Valid `CLOUDCALL_REFRESH_TOKEN` if you want CloudCall ingest.

## First deployment

1. Clone the repo onto the host and `cd` into it.
2. Create `.env` from the template and fill in real values:
   ```bash
   cp .env.example .env
   # then edit .env in your editor of choice
   ```
   At minimum set `OPENAI_API_KEY`, `ADMIN_EMAIL`, `ADMIN_DEFAULT_PASSWORD`.
   For CloudCall, also set `CLOUDCALL_REFRESH_TOKEN` and recommended:
   ```
   CLOUDCALL_POLL_INTERVAL_MINUTES=2
   CLOUDCALL_RECORDING_RETENTION_HOURS=72
   ```
3. Build the image and start the stack:
   ```bash
   docker compose up -d --build
   ```
4. Tail the logs to confirm a clean boot:
   ```bash
   docker compose logs -f
   ```
   Look for: Streamlit reporting "You can now view your Streamlit app", the
   poller logging "CloudCall poller started", and the webhook receiver logging
   "Uvicorn running on http://127.0.0.1:8081".
5. From any machine on the network, browse to `http://<host>:8080`.
6. Sign in with the admin email and the password from `ADMIN_DEFAULT_PASSWORD`.
7. **Immediately** create real user accounts via the Admin tab and change the
   admin password (re-deploy with a new `ADMIN_DEFAULT_PASSWORD` or reset via
   the SQL one-liner in the README).

## Day-to-day operations

| Action | Command |
|---|---|
| Tail all logs | `docker compose logs -f` |
| Tail one process | `docker compose exec app tail -f /dev/stdout` |
| Restart everything | `docker compose restart` |
| Stop the stack | `docker compose down` (keeps the data volume) |
| Update to latest code | `git pull && docker compose up -d --build` |
| Shell into the container | `docker compose exec app bash` |
| Inspect the database | `docker compose exec app sqlite3 data/calls.db` |

## Backups

The SQLite database lives in the `app-data` Docker volume at `/app/data/calls.db`.
Audio files are purged automatically after `CLOUDCALL_RECORDING_RETENTION_HOURS`,
so backups only need to cover the DB.

Run the included script from the host on a schedule:

```bash
docker compose exec -T app python scripts/backup_db.py --dest /app/data/backups
```

Or, for backups landing outside the container, mount a host directory into
`/app/data/backups` and run the same command — or run the script directly on
the host (it operates on a SQLite file path, so as long as you can reach the
volume, the script can back it up).

Recommended cadence: nightly, with 30 days retention. Set up Task Scheduler
on Windows or a cron job on Linux to invoke `docker compose exec ...` once a
day at low-traffic hours.

## Network and access model

- The container exposes port **8080** to the host.
- nginx (inside the container) is the single entry point and routes:
  - `/webhooks/*` and `/health` -> FastAPI webhook receiver on `127.0.0.1:8081`
  - everything else -> Streamlit on `127.0.0.1:8501`
- For an internal-only deployment, do **not** publish port 8080 to the public
  internet. Bind to a private interface or front it with an internal-only DNS
  record.
- The webhook server runs but receives no traffic unless you set up a public
  ingress and register the URL with CloudCall. Leaving it idle is fine — it's
  ready when you want to flip to webhook-driven ingest.

## Monitoring

The Docker healthcheck pings `http://localhost:8080/health` every 30 seconds.
`docker compose ps` will show `(healthy)` once boot is complete and the FastAPI
`/health` endpoint is responding.

For a more serious monitoring story, point any uptime tool (UptimeRobot, a
PowerShell scheduled task, Datadog/New Relic) at `http://<host>:8080/health`
and alert on non-200 responses.

## Common issues

- **`OPENAI_API_KEY environment variable is not set`** — `.env` missing or not
  read. Confirm `.env` exists in the same directory as `docker-compose.yml`,
  and recreate the stack with `docker compose up -d --force-recreate`.
- **CloudCall token refresh fails with `invalid_grant`** — refresh token is
  stale or has been rotated by another process. Reset by putting a fresh
  token in `.env` and restarting (`docker compose restart`).
- **Streamlit shows "calls table is empty" after pulling from CloudCall** —
  user mapping not configured. Open Admin tab and add a CloudCall User
  Mapping for any user IDs surfaced in the unmapped warning.
- **Auto-import fails for recordings** — usually CloudCall's recording URL
  expired before download. Re-poll with the "Pull from CloudCall" button; the
  poller fetches a fresh URL each cycle.
- **Disk filling up** — check `data/cloudcall_downloads` and `data/converted`
  inside the volume. The retention sweep should keep these bounded; if it's
  not, check the poller logs for cleanup errors.

## Future production hardening

When you outgrow the single-host model:

1. **Switch to webhooks.** Expose `/webhooks/cloudcall` on a public HTTPS
   endpoint (with a real cert), register it with CloudCall, and drop the
   poll interval to 30 or 60 minutes as a safety net. Webhook server code
   is already in `webhook_server.py`.
2. **Move SQLite to Postgres.** Required if you want multiple app containers
   for HA. The current schema is small enough to port in an afternoon.
3. **Move audio storage to object storage** (S3 or Azure Blob). Required
   for multi-host or for keeping audio durable across container rebuilds.
4. **Pipe analytics to Snowflake.** Nightly export of `calls`, `api_usage`,
   and `cloudcall_recordings` from SQLite/Postgres into Snowflake. Use
   Snowflake for BI dashboards and long-term archive — never for
   operational reads from the app.
5. **Add Bullhorn integration.** Phase 2 of the application roadmap. The
   stub Submit-to-Bullhorn button in the UI is the integration point.
