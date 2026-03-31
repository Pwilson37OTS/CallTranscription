from unittest.mock import patch, MagicMock

import pytest

from openai_service import build_summary_messages, transcribe_audio, summarize_transcript


class TestBuildSummaryMessages:
    def test_returns_three_messages(self):
        messages = build_summary_messages(
            transcript_text="Hello, this is a test call.",
            call_type="screening_call",
            metadata={
                "recruiter_name": "Jane",
                "subject_name": "John",
                "company_name": "Acme",
                "notes": "Test",
            },
        )
        assert len(messages) == 3
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "developer"
        assert messages[2]["role"] == "user"

    def test_user_message_contains_transcript(self):
        messages = build_summary_messages(
            transcript_text="The candidate discussed Python experience.",
            call_type="screening_call",
            metadata={"recruiter_name": "Jane", "subject_name": "John", "company_name": "Acme", "notes": ""},
        )
        assert "Python experience" in messages[2]["content"]

    def test_user_message_contains_metadata(self):
        messages = build_summary_messages(
            transcript_text="Hello",
            call_type="reference_check",
            metadata={"recruiter_name": "Jane", "subject_name": "John", "company_name": "Acme Corp", "notes": "Good ref"},
        )
        content = messages[2]["content"]
        assert "Acme Corp" in content
        assert "Jane" in content

    def test_invalid_call_type_raises(self):
        with pytest.raises(KeyError):
            build_summary_messages("text", "nonexistent_type", {})


class TestTranscribeAudio:
    @patch("openai_service.get_openai_client")
    def test_calls_openai_and_returns_text(self, mock_get_client, tmp_path):
        # Create a fake audio file
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


class TestSummarizeTranscript:
    @patch("openai_service.get_openai_client")
    def test_calls_openai_and_returns_summary(self, mock_get_client):
        mock_client = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "  Summary of the call.  "
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        result = summarize_transcript(
            transcript_text="The candidate has 5 years of Python.",
            call_type="screening_call",
            metadata={"recruiter_name": "Jane", "subject_name": "John", "company_name": "Acme", "notes": ""},
            model="gpt-4.1",
        )

        assert result == "Summary of the call."  # stripped
        mock_client.chat.completions.create.assert_called_once()

    @patch("openai_service.get_openai_client")
    def test_normalizes_developer_role_to_system(self, mock_get_client):
        mock_client = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "Summary"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        summarize_transcript(
            transcript_text="Test",
            call_type="screening_call",
            metadata={"recruiter_name": "J", "subject_name": "J", "company_name": "C", "notes": ""},
        )

        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        # All roles should be "system" or "user" (developer normalized to system)
        roles = {m["role"] for m in messages}
        assert "developer" not in roles
