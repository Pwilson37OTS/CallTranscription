"""CloudCall VoIP integration configuration.

Reads CloudCall-specific settings from environment variables.
The integration is automatically enabled when CLOUDCALL_API_KEY is set.
"""

import os

from config import DATA_DIR

# CloudCall API settings
CLOUDCALL_API_BASE_URL = os.getenv("CLOUDCALL_API_BASE_URL", "https://api.cloudcall.com/v1")
CLOUDCALL_API_KEY = os.getenv("CLOUDCALL_API_KEY", "")
CLOUDCALL_API_SECRET = os.getenv("CLOUDCALL_API_SECRET", "")

# Webhook validation
CLOUDCALL_WEBHOOK_SECRET = os.getenv("CLOUDCALL_WEBHOOK_SECRET", "")

# Recording retention (hours) — recordings older than this are auto-purged
CLOUDCALL_RECORDING_RETENTION_HOURS = int(os.getenv("CLOUDCALL_RECORDING_RETENTION_HOURS", "48"))

# Feature flag: enabled when API key is configured
CLOUDCALL_ENABLED = bool(CLOUDCALL_API_KEY)

# Temp directory for downloaded recordings before conversion
CLOUDCALL_DOWNLOAD_DIR = DATA_DIR / "cloudcall_downloads"
CLOUDCALL_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
