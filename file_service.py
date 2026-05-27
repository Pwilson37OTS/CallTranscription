import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

import streamlit as st

from config import UPLOAD_DIR, CONVERTED_DIR, SUPPORTED_AUDIO_EXTENSIONS, MAX_UPLOAD_SIZE_MB


@st.cache_data(ttl=60)
def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def convert_file_to_wav(input_path: Path, allowed_source_dir: Path) -> Path:
    """Convert any audio file to mono 16kHz WAV using ffmpeg.

    Args:
        input_path: Path to the source audio file.
        allowed_source_dir: Directory the input_path must reside in (path traversal guard).

    Returns:
        Path to the converted WAV file in CONVERTED_DIR.
    """
    if not ffmpeg_available():
        raise RuntimeError(
            "ffmpeg is not installed or not on PATH. Install ffmpeg first so the app can convert recordings to WAV automatically."
        )

    # Validate input path is within allowed_source_dir to prevent path traversal
    resolved = input_path.resolve()
    if not str(resolved).startswith(str(allowed_source_dir.resolve())):
        raise ValueError("Invalid file path.")

    output_path = CONVERTED_DIR / f"{input_path.stem}.wav"
    command = [
        "ffmpeg", "-y", "-i", str(input_path),
        "-ac", "1", "-ar", "16000",
        str(output_path),
    ]

    result = subprocess.run(command, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg conversion failed: {result.stderr}")

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError("ffmpeg conversion failed: WAV file was not created.")

    return output_path


def convert_audio_to_wav(input_path: Path) -> Path:
    """Convert an uploaded audio file to WAV. Wrapper for backward compatibility."""
    return convert_file_to_wav(input_path, UPLOAD_DIR)


def split_audio_for_transcription(
    input_path: Path,
    chunk_seconds: int = 600,
) -> list:
    """Split an audio file into fixed-duration MP3 chunks for OpenAI transcription.

    OpenAI's transcription endpoints cap at 25 MB per request. For long
    recordings (typically over ~50 minutes at VoIP bitrates), we split the
    audio into smaller chunks, transcribe each, and concatenate the text.

    Re-encodes to mono 16kHz 32kbps MP3 — well below the size limit while
    preserving enough quality for transcription. The original file is not
    modified.

    Returns a sorted list of chunk Paths (in <input>_chunks/ directory).
    Caller is responsible for deleting the chunks and the directory.
    """
    if not ffmpeg_available():
        raise RuntimeError("ffmpeg not available for audio chunking")

    output_dir = input_path.parent / f"{input_path.stem}_chunks"
    output_dir.mkdir(exist_ok=True)
    output_pattern = output_dir / "chunk_%03d.mp3"

    command = [
        "ffmpeg", "-y", "-i", str(input_path),
        "-f", "segment",
        "-segment_time", str(chunk_seconds),
        "-c:a", "libmp3lame",
        "-b:a", "32k",
        "-ar", "16000",
        "-ac", "1",
        str(output_pattern),
    ]

    result = subprocess.run(command, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        # Clean up partial output before raising
        for f in output_dir.glob("chunk_*.mp3"):
            f.unlink(missing_ok=True)
        try:
            output_dir.rmdir()
        except OSError:
            pass
        raise RuntimeError(
            f"ffmpeg chunking failed: {result.stderr[-500:] if result.stderr else 'unknown error'}"
        )

    chunks = sorted(output_dir.glob("chunk_*.mp3"))
    if not chunks:
        try:
            output_dir.rmdir()
        except OSError:
            pass
        raise RuntimeError("ffmpeg produced no chunks")

    return chunks


def save_uploaded_file(uploaded_file) -> Dict[str, Any]:
    original_name = uploaded_file.name
    ext = Path(original_name).suffix.lower()
    if ext and ext not in SUPPORTED_AUDIO_EXTENSIONS:
        st.warning(f"Unknown extension {ext}. Attempting conversion with ffmpeg anyway.")

    file_bytes = uploaded_file.getbuffer()
    file_size_mb = len(file_bytes) / (1024 * 1024)
    if file_size_mb > MAX_UPLOAD_SIZE_MB:
        raise ValueError(
            f"File is too large ({file_size_mb:.1f} MB). Maximum allowed size is {MAX_UPLOAD_SIZE_MB} MB."
        )

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
    safe_name = f"{timestamp}_{original_name}"
    uploaded_destination = UPLOAD_DIR / safe_name

    with open(uploaded_destination, "wb") as f:
        f.write(file_bytes)

    wav_path = convert_audio_to_wav(uploaded_destination)

    # Store relative path from project root for portability
    relative_path = wav_path.relative_to(Path(__file__).parent)

    return {
        "original_filename": original_name,
        "stored_filename": wav_path.name,
        "stored_path": str(relative_path),
        "mime_type": "audio/wav",
    }
