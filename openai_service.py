import os
import json
from typing import Dict, Any, List

from openai import OpenAI

from prompts import CALL_TYPE_CONFIG
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


def build_summary_messages(transcript_text: str, call_type: str, metadata: Dict[str, Any]) -> List[Dict[str, str]]:
    cfg = CALL_TYPE_CONFIG[call_type]

    metadata_block = json.dumps(
        {
            "recruiter_name": metadata.get("recruiter_name"),
            "subject_name": metadata.get("subject_name"),
            "company_name": metadata.get("company_name"),
            "notes": metadata.get("notes"),
            "call_type": call_type,
            "call_type_label": cfg["label"],
        },
        indent=2,
    )

    user_prompt = (
        "Use the transcript and metadata below to generate the ATS-ready note.\n\n"
        f"Metadata:\n{metadata_block}\n\n"
        f"Transcript:\n{transcript_text}"
    )

    return [
        {"role": "system", "content": cfg["system_prompt"]},
        {"role": "developer", "content": cfg["developer_prompt"]},
        {"role": "user", "content": user_prompt},
    ]


def summarize_transcript(
    transcript_text: str,
    call_type: str,
    metadata: Dict[str, Any],
    model: str = "gpt-4.1",
) -> str:
    logger.info("Summarization started: model=%s call_type=%s", model, call_type)
    client = get_openai_client()
    messages = build_summary_messages(transcript_text, call_type, metadata)

    normalized = [
        {**m, "role": "system"} if m["role"] == "developer" else m
        for m in messages
    ]

    response = client.chat.completions.create(
        model=model,
        messages=normalized,
    )
    result = response.choices[0].message.content.strip()
    logger.info("Summarization complete: model=%s chars=%d", model, len(result))
    return result
