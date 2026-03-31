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
