"""CloudCall VoIP integration configuration.

Reads CloudCall-specific settings from environment variables.
The integration is automatically enabled when CLOUDCALL_REFRESH_TOKEN is set.

CloudCall uses OAuth2 refresh token auth:
  - POST https://auth.cloudcall.com/connect/token with refresh_token
  - Returns access_token (24hr) + new refresh_token
  - API calls use Authorization: Bearer <access_token>
"""

import os

from config import DATA_DIR

# CloudCall API settings
CLOUDCALL_API_BASE_URL = os.getenv("CLOUDCALL_API_BASE_URL", "https://apio1.cloudcall.com")
CLOUDCALL_AUTH_URL = os.getenv("CLOUDCALL_AUTH_URL", "https://auth.cloudcall.com/connect/token")
CLOUDCALL_CLIENT_ID = os.getenv("CLOUDCALL_CLIENT_ID", "o1-public-api")
CLOUDCALL_REFRESH_TOKEN = os.getenv("CLOUDCALL_REFRESH_TOKEN", "")

# Webhook signature validation — signing_key returned when creating a webhook subscription
CLOUDCALL_WEBHOOK_SIGNING_KEY = os.getenv("CLOUDCALL_WEBHOOK_SIGNING_KEY", "")

# Recording retention (hours) — recordings older than this are auto-purged
CLOUDCALL_RECORDING_RETENTION_HOURS = int(os.getenv("CLOUDCALL_RECORDING_RETENTION_HOURS", "72"))

# Polling settings
CLOUDCALL_POLL_INTERVAL_MINUTES = int(os.getenv("CLOUDCALL_POLL_INTERVAL_MINUTES", "5"))
CLOUDCALL_POLL_START_HOUR_CT = int(os.getenv("CLOUDCALL_POLL_START_HOUR_CT", "6"))   # 6 AM Central
CLOUDCALL_POLL_END_HOUR_CT = int(os.getenv("CLOUDCALL_POLL_END_HOUR_CT", "18"))      # 6 PM Central

# Feature flag: enabled when refresh token is configured
CLOUDCALL_ENABLED = bool(CLOUDCALL_REFRESH_TOKEN)

# Temp directory for downloaded recordings before conversion
CLOUDCALL_DOWNLOAD_DIR = DATA_DIR / "cloudcall_downloads"
CLOUDCALL_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
