from unittest.mock import patch, MagicMock

from openai_service import transcribe_audio, diarize_transcript, _looks_repetitive


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


class TestTranscribeAudio:
    @patch("openai_service.get_openai_client")
    def test_calls_openai_and_returns_text(self, mock_get_client, tmp_path):
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

    @patch("openai_service.get_openai_client")
    def test_retries_with_fallback_when_repetitive(self, mock_get_client, tmp_path):
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
        # Fallback call used whisper-1.
        second_call = mock_client.audio.transcriptions.create.call_args_list[1]
        assert second_call.kwargs["model"] == "whisper-1"


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
