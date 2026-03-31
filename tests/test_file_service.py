from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from config import UPLOAD_DIR, CONVERTED_DIR, MAX_UPLOAD_SIZE_MB


class TestConvertAudioToWav:
    def test_rejects_path_outside_upload_dir(self, tmp_path):
        from file_service import convert_audio_to_wav

        evil_path = tmp_path / "evil.mp3"
        evil_path.write_bytes(b"fake audio")

        with pytest.raises(ValueError, match="Invalid file path"):
            convert_audio_to_wav(evil_path)

    def test_raises_when_ffmpeg_missing(self, monkeypatch):
        from file_service import convert_audio_to_wav

        monkeypatch.setattr("file_service.ffmpeg_available", lambda: False)
        fake_path = UPLOAD_DIR / "test.mp3"
        with pytest.raises(RuntimeError, match="ffmpeg"):
            convert_audio_to_wav(fake_path)

    @patch("file_service.subprocess.run")
    def test_raises_on_ffmpeg_failure(self, mock_run, monkeypatch):
        monkeypatch.setattr("file_service.ffmpeg_available", lambda: True)

        # Create a real file in UPLOAD_DIR
        test_file = UPLOAD_DIR / "test_convert.mp3"
        test_file.write_bytes(b"fake audio data")

        mock_run.return_value = MagicMock(returncode=1, stderr="conversion error")

        with pytest.raises(RuntimeError, match="ffmpeg conversion failed"):
            from file_service import convert_audio_to_wav
            convert_audio_to_wav(test_file)

        # Cleanup
        if test_file.exists():
            test_file.unlink()


class TestSaveUploadedFile:
    def test_rejects_oversized_file(self, monkeypatch):
        from file_service import save_uploaded_file

        fake_file = MagicMock()
        fake_file.name = "big_file.mp3"
        # Create buffer larger than limit
        fake_file.getbuffer.return_value = b"x" * ((MAX_UPLOAD_SIZE_MB + 1) * 1024 * 1024)

        with pytest.raises(ValueError, match="too large"):
            save_uploaded_file(fake_file)
