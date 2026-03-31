import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

APP_DIR = Path(__file__).parent
DATA_DIR = APP_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
CONVERTED_DIR = DATA_DIR / "converted"
DB_PATH = DATA_DIR / "calls.db"
LOGO_PATH = APP_DIR / "logo.jpg"

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
CONVERTED_DIR.mkdir(parents=True, exist_ok=True)

SUPPORTED_AUDIO_EXTENSIONS = {
    ".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".wav", ".webm", ".ogg", ".flac"
}

MAX_UPLOAD_SIZE_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", "500"))

DEFAULT_TRANSCRIPTION_MODEL = os.getenv("TRANSCRIPTION_MODEL", "gpt-4o-transcribe")
DEFAULT_SUMMARY_MODEL = os.getenv("SUMMARY_MODEL", "gpt-4.1")

# Auth settings
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@oaktreestaffing.com")
ADMIN_DEFAULT_PASSWORD = os.getenv("ADMIN_DEFAULT_PASSWORD", "changeme123")

# Rate limiting: max API calls per user per hour
RATE_LIMIT_PER_HOUR = int(os.getenv("RATE_LIMIT_PER_HOUR", "50"))

# Approximate OpenAI pricing (cents per unit) for cost tracking
# Transcription: cents per minute of audio
COST_PER_MINUTE_TRANSCRIPTION = {
    "gpt-4o-transcribe": 0.6,
    "gpt-4o-mini-transcribe": 0.3,
    "whisper-1": 0.6,
}
# Summarization: cents per 1K tokens (input + output blended estimate)
COST_PER_1K_TOKENS_SUMMARY = {
    "gpt-4.1": 1.0,
    "gpt-4o": 0.5,
    "gpt-4o-mini": 0.015,
}
