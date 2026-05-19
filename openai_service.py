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


def analyze_call(
    transcript_text: str,
    call_type_label: str,
    template: str,
    metadata: Dict[str, Any],
    model: str = "gpt-4.1",
) -> str:
    """Evaluate a call transcript against a coaching template.

    Returns a markdown evaluation with three sections:
    Covered Well, Not Covered Well Enough, Missed.
    """
    logger.info(
        "Call analysis started: model=%s call_type=%s chars=%d",
        model, call_type_label, len(transcript_text),
    )

    recruiter = (metadata.get("recruiter_name") or "").strip() or "the recruiter"
    subject = (metadata.get("subject_name") or "").strip() or "the contact"

    system_prompt = (
        "You are a recruiter call coach. The user will give you a transcript of a "
        f"{call_type_label.lower()} along with a template describing what should "
        "have been covered. Your job is to evaluate how well the recruiter "
        f"({recruiter}) executed the call against that template. Be specific, "
        "direct, and cite the transcript when it helps. Be constructive but honest "
        "— this feedback is for the recruiter to improve."
    )

    user_prompt = (
        f"Call Type: {call_type_label}\n"
        f"Recruiter: {recruiter}\n"
        f"Contact: {subject}\n\n"
        "TEMPLATE — what the call should have covered:\n"
        "----------\n"
        f"{template}\n"
        "----------\n\n"
        "TRANSCRIPT:\n"
        "----------\n"
        f"{transcript_text}\n"
        "----------\n\n"
        "Return your evaluation as markdown with exactly these three sections, "
        "each followed by a bulleted list. If a section has no items, write "
        "_None._ on its own line beneath the heading.\n\n"
        "## Covered Well\n"
        "(Points clearly and effectively addressed in the conversation.)\n\n"
        "## Not Covered Well Enough\n"
        "(Points that were touched on but only superficially, partially, or "
        "without enough depth.)\n\n"
        "## Missed\n"
        "(Points the recruiter did not address at all.)\n\n"
        "For each bullet, briefly quote or paraphrase the relevant moment in the "
        "transcript (or note its absence). Keep bullets short and specific.\n\n"
        "If the template above is clearly a placeholder, evaluate against the "
        "suggested points it lists and add a final note that the template is a "
        "placeholder and should be replaced with the real one for sharper feedback."
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
    logger.info("Call analysis complete: model=%s chars=%d", model, len(result))
    return result
