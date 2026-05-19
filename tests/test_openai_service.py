from unittest.mock import patch, MagicMock

from openai_service import transcribe_audio, diarize_transcript


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
