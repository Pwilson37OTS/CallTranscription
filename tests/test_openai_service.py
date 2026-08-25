from unittest.mock import patch, MagicMock

import pytest

from openai_service import (
    transcribe_audio,
    diarize_transcript,
    _looks_repetitive,
    _is_thin_transcript,
    _fallback_model_for,
)


# A looped transcription: one block repeated many times (the real failure mode).
REPETITIVE_TEXT = (
    "So this would be OPD Optimized Process Design working on oil and gas projects. "
    "When is the last time you used CADWorks in your designs recently. "
) * 8

NORMAL_TEXT = (
    "Hello Michael how are you doing today. I wanted to talk about a role. "
    "It is a senior structural designer position over in the Katy area. "
    "The pay is about seventy dollars an hour on a long term contract. "
    "Are you open to new opportunities at the moment. "
    "Great I will send over the job description by email shortly. "
    "Thanks so much for taking my call today Michael."
)


class TestRepetitionDetection:
    def test_flags_looped_transcript(self):
        assert _looks_repetitive(REPETITIVE_TEXT) is True

    def test_passes_normal_transcript(self):
        assert _looks_repetitive(NORMAL_TEXT) is False

    def test_empty_and_short_are_not_repetitive(self):
        assert _looks_repetitive("") is False
        assert _looks_repetitive("Hi. Yes. OK.") is False

    def test_flags_short_token_loop(self):
        # The silence-hallucination signature: one short word repeated forever.
        assert _looks_repetitive("Okay. " * 50) is True
        assert _looks_repetitive("Yeah. " * 40) is True

    def test_light_legitimate_repetition_passes(self):
        # A recruiter genuinely repeating one confirmation must not trip it.
        text = (
            "Are you still currently in Houston correct. Yes I am in Houston. "
            "Are you still currently in Houston correct. "
            "The role is in Katy about seventy an hour on contract. "
            "It is on site monday through friday with occasional saturdays. "
            "Would that schedule work for you long term. Yes that works for me. "
            "Great I will email over the full job description now."
        )
        assert _looks_repetitive(text) is False


class TestTranscribeHelpers:
    def test_fallback_model_alternates(self):
        assert _fallback_model_for("gpt-4o-transcribe") == "whisper-1"
        assert _fallback_model_for("whisper-1") == "gpt-4o-transcribe"

    def test_thin_transcript_flagged(self):
        # ~8 words over 37 minutes = the hallucination case → far below 20 wpm.
        assert _is_thin_transcript("Hello bonsoir a tous eight nine ten store", 37 * 60) is True

    def test_normal_density_not_thin(self):
        text = " ".join(["word"] * 300)  # 300 words over 5 min = 60 wpm
        assert _is_thin_transcript(text, 5 * 60) is False

    def test_thin_check_skipped_for_short_or_unknown_duration(self):
        assert _is_thin_transcript("hi", 30) is False       # under 60s
        assert _is_thin_transcript("hi", None) is False      # duration unknown


class TestTranscribeAudio:
    @patch("file_service.get_audio_duration_seconds", return_value=30.0)
    @patch("openai_service.get_openai_client")
    def test_calls_openai_and_returns_text(self, mock_get_client, mock_dur, tmp_path):
        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"fake audio data")

        mock_client = MagicMock()
        mock_transcript = MagicMock()
        mock_transcript.text = "This is the transcribed text."
        mock_client.audio.transcriptions.create.return_value = mock_transcript
        mock_get_client.return_value = mock_client

        result = transcribe_audio(str(audio_file), model="gpt-4o-transcribe")

        assert result == "This is the transcribed text."
        mock_client.audio.transcriptions.create.assert_called_once()

    @patch("file_service.get_audio_duration_seconds", return_value=30.0)
    @patch("openai_service.get_openai_client")
    def test_retries_with_fallback_when_repetitive(self, mock_get_client, mock_dur, tmp_path):
        audio_file = tmp_path / "loop.wav"
        audio_file.write_bytes(b"fake audio data")

        looped = MagicMock(); looped.text = REPETITIVE_TEXT
        clean = MagicMock(); clean.text = "A clean short transcript of the call about the role."
        mock_client = MagicMock()
        # First (primary model) loops; second (fallback) returns clean text.
        mock_client.audio.transcriptions.create.side_effect = [looped, clean]
        mock_get_client.return_value = mock_client

        result = transcribe_audio(str(audio_file), model="gpt-4o-transcribe")

        assert result == "A clean short transcript of the call about the role."
        assert mock_client.audio.transcriptions.create.call_count == 2
        # gpt-4o-transcribe failed → fallback uses the OTHER model, whisper-1.
        assert mock_client.audio.transcriptions.create.call_args_list[1].kwargs["model"] == "whisper-1"

    @patch("file_service.get_audio_duration_seconds", return_value=120.0)
    @patch("openai_service.get_openai_client")
    def test_retries_with_fallback_when_thin(self, mock_get_client, mock_dur, tmp_path):
        # Hallucination case: a few words for 2 minutes of audio → thin → retry.
        audio_file = tmp_path / "thin.wav"
        audio_file.write_bytes(b"fake audio data")

        thin = MagicMock(); thin.text = "Hello. Bonsoir a tous. Das ist gut."
        # A realistic dense transcript (varied words, so it isn't flagged as a loop).
        good = MagicMock(); good.text = " ".join(f"word{i}" for i in range(400))
        mock_client = MagicMock()
        mock_client.audio.transcriptions.create.side_effect = [thin, good]
        mock_get_client.return_value = mock_client

        result = transcribe_audio(str(audio_file), model="whisper-1")

        assert result == good.text
        assert mock_client.audio.transcriptions.create.call_count == 2
        # whisper-1 failed → fallback uses the OTHER model, gpt-4o-transcribe.
        assert mock_client.audio.transcriptions.create.call_args_list[1].kwargs["model"] == "gpt-4o-transcribe"

    @patch("file_service.split_audio_for_transcription")
    @patch("file_service.get_audio_duration_seconds", return_value=2000.0)  # forces chunking
    @patch("openai_service._transcribe_segment")
    def test_chunk_failure_leaves_gap_marker(self, mock_seg, mock_dur, mock_split, tmp_path):
        audio_file = tmp_path / "long.wav"
        audio_file.write_bytes(b"fake audio data")
        cdir = tmp_path / "long_chunks"; cdir.mkdir()
        c0 = cdir / "chunk_000.mp3"; c0.write_bytes(b"a")
        c1 = cdir / "chunk_001.mp3"; c1.write_bytes(b"b")
        mock_split.return_value = [c0, c1]
        # First chunk transcribes; second fails permanently (both models errored).
        mock_seg.side_effect = ["Real transcript for the first segment.", Exception("boom")]

        result = transcribe_audio(str(audio_file), model="whisper-1")

        assert "Real transcript for the first segment." in result
        assert "[Transcription unavailable for audio segment 2 of 2]" in result

    @patch("file_service.split_audio_for_transcription")
    @patch("file_service.get_audio_duration_seconds", return_value=2000.0)
    @patch("openai_service._transcribe_segment")
    def test_all_chunks_failing_raises(self, mock_seg, mock_dur, mock_split, tmp_path):
        audio_file = tmp_path / "bad.wav"
        audio_file.write_bytes(b"fake audio data")
        cdir = tmp_path / "bad_chunks"; cdir.mkdir()
        c0 = cdir / "chunk_000.mp3"; c0.write_bytes(b"a")
        mock_split.return_value = [c0]
        mock_seg.side_effect = [Exception("boom")]

        with pytest.raises(RuntimeError, match="No usable speech"):
            transcribe_audio(str(audio_file), model="whisper-1")

    @patch("file_service.get_audio_duration_seconds", return_value=120.0)
    @patch("openai_service.get_openai_client")
    def test_raises_when_both_models_loop(self, mock_get_client, mock_dur, tmp_path):
        # Silence-induced loop that both models produce → dropped → surfaced as
        # a retryable error rather than storing "Okay. Okay. Okay." garbage.
        audio_file = tmp_path / "silence.wav"
        audio_file.write_bytes(b"fake audio data")

        loop = MagicMock(); loop.text = "Okay. " * 60
        mock_client = MagicMock()
        mock_client.audio.transcriptions.create.side_effect = [loop, loop]
        mock_get_client.return_value = mock_client

        with pytest.raises(RuntimeError, match="No usable speech"):
            transcribe_audio(str(audio_file), model="whisper-1")


class TestDiarizeTranscript:
    @patch("openai_service.get_openai_client")
    def test_calls_openai_and_returns_labeled_text(self, mock_get_client):
        mock_client = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "  Jane: Hello.\nJohn: Hi.  "
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        result = diarize_transcript(
            transcript_text="Hello hi",
            metadata={"recruiter_name": "Jane", "subject_name": "John"},
            model="gpt-4.1",
        )

        assert result == "Jane: Hello.\nJohn: Hi."  # stripped
        mock_client.chat.completions.create.assert_called_once()

    @patch("openai_service.get_openai_client")
    def test_includes_speaker_names_in_prompt(self, mock_get_client):
        mock_client = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "labeled"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        diarize_transcript(
            transcript_text="Test",
            metadata={"recruiter_name": "Alice", "subject_name": "Bob"},
        )

        call_args = mock_client.chat.completions.create.call_args
        user_msg = next(m for m in call_args.kwargs["messages"] if m["role"] == "user")
        assert "Alice" in user_msg["content"]
        assert "Bob" in user_msg["content"]
