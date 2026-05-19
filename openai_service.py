import os
from typing import Dict, Any

from openai import OpenAI

from logging_config import logger


def get_openai_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError(
            "OpenAI API key not found. Set the OPENAI_API_KEY environment variable or add it to your .env file."
        )
    return OpenAI(api_key=api_key)


def transcribe_audio(file_path: str, model: str = "gpt-4o-transcribe") -> str:
    logger.info("Transcription started: model=%s file=%s", model, file_path)
    client = get_openai_client()
    with open(file_path, "rb") as audio_file:
        transcript = client.audio.transcriptions.create(
            model=model,
            file=audio_file,
        )
    logger.info("Transcription complete: model=%s chars=%d", model, len(transcript.text))
    return transcript.text


def diarize_transcript(
    transcript_text: str,
    metadata: Dict[str, Any],
    model: str = "gpt-4.1",
) -> str:
    """Label speaker turns on a raw transcript using textual context.

    OpenAI's transcription API does not perform acoustic diarization.
    This pass uses an LLM to infer speaker boundaries from conversational
    cues and the call metadata (recruiter name, subject name).
    """
    logger.info("Diarization started: model=%s chars=%d", model, len(transcript_text))

    recruiter = (metadata.get("recruiter_name") or "").strip() or "Recruiter"
    subject = (metadata.get("subject_name") or "").strip() or "Contact"

    system_prompt = (
        "You are a transcript editor for a staffing recruiter tool. The user will give you "
        "a raw, single-stream transcript of a phone or video call. Your job is to label "
        "each speaker turn and break the transcript into readable lines. "
        "Preserve every word the speakers actually said — do not paraphrase, summarize, "
        "translate, correct grammar, or remove filler words. Add light punctuation only "
        "where it improves readability."
    )

    user_prompt = (
        "Known speakers (use these names when you can confidently identify them):\n"
        f"  - {recruiter} — the recruiter on the call\n"
        f"  - {subject} — the contact / candidate\n\n"
        "Reformat the transcript below using these rules:\n"
        "  1. Detect speaker turns from conversational context (questions vs. answers, "
        "tone shifts, name mentions, etc.).\n"
        "  2. Put each turn on its own line in the format: 'Name: spoken text'.\n"
        "  3. Use the known speaker names above when you are reasonably confident. If "
        "you cannot tell who is speaking, use 'Speaker 1', 'Speaker 2', etc. consistently.\n"
        "  4. If a third party joins, label them as 'Speaker 3' or by name if it is stated "
        "on the call.\n"
        "  5. Do NOT paraphrase, summarize, or remove any spoken content. Every word from "
        "the source transcript must appear in your output.\n"
        "  6. Do not add commentary, headers, or explanations — return only the labeled "
        "transcript.\n\n"
        f"Transcript:\n{transcript_text}"
    )

    client = get_openai_client()
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    result = response.choices[0].message.content.strip()
    logger.info("Diarization complete: model=%s chars=%d", model, len(result))
    return result
