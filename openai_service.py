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


# OpenAI's transcription API caps a single request at 25 MB. We use a slightly
# lower ceiling for the "do we need to chunk?" check so we don't get caught by
# off-by-one rounding or content-length overhead.
_TRANSCRIBE_DIRECT_LIMIT_BYTES = 24 * 1024 * 1024

# How long each chunk should be when we have to split. 10 minutes at 32kbps mono
# MP3 is ~2.4 MB, well under the limit, and short enough that a stuck request
# can't waste a huge amount of time.
_TRANSCRIBE_CHUNK_SECONDS = 600


def _transcribe_single_file(file_path: str, model: str) -> str:
    """Send one audio file to the OpenAI transcription endpoint."""
    client = get_openai_client()
    with open(file_path, "rb") as audio_file:
        transcript = client.audio.transcriptions.create(
            model=model,
            file=audio_file,
        )
    return transcript.text


def transcribe_audio(file_path: str, model: str = "gpt-4o-transcribe") -> str:
    """Transcribe an audio file. Auto-chunks for files larger than the API limit.

    For typical CloudCall recordings under ~50 minutes / 24 MB this is a single
    request. For longer calls we split with ffmpeg into ~10 minute MP3 chunks
    and concatenate the transcripts. Speaker diarization runs after, on the
    combined text.
    """
    from pathlib import Path
    path_obj = Path(file_path)
    size_bytes = path_obj.stat().st_size

    logger.info(
        "Transcription started: model=%s file=%s size_mb=%.1f",
        model, file_path, size_bytes / (1024 * 1024),
    )

    # Small enough → single request, original behavior.
    if size_bytes <= _TRANSCRIBE_DIRECT_LIMIT_BYTES:
        text = _transcribe_single_file(file_path, model)
        logger.info("Transcription complete: model=%s chars=%d", model, len(text))
        return text

    # Over the per-request limit → chunk and stitch.
    from file_service import split_audio_for_transcription
    logger.info(
        "File exceeds %d MB single-request limit; chunking into %ds segments",
        _TRANSCRIBE_DIRECT_LIMIT_BYTES // (1024 * 1024),
        _TRANSCRIBE_CHUNK_SECONDS,
    )

    chunks = split_audio_for_transcription(
        path_obj, chunk_seconds=_TRANSCRIBE_CHUNK_SECONDS
    )
    chunks_dir = chunks[0].parent
    try:
        parts = []
        for i, chunk_path in enumerate(chunks, start=1):
            logger.info(
                "Transcribing chunk %d/%d: %s (%.1f MB)",
                i, len(chunks), chunk_path.name,
                chunk_path.stat().st_size / (1024 * 1024),
            )
            part_text = _transcribe_single_file(str(chunk_path), model)
            parts.append(part_text)

        combined = "\n\n".join(parts)
        logger.info(
            "Transcription complete (chunked): chunks=%d total_chars=%d",
            len(chunks), len(combined),
        )
        return combined
    finally:
        # Always clean up chunk files + their directory, even on partial failure.
        for chunk_path in chunks:
            try:
                chunk_path.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            chunks_dir.rmdir()
        except OSError:
            pass


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


def summarize_call(
    transcript_text: str,
    metadata: Dict[str, Any],
    model: str = "gpt-4.1",
) -> str:
    """Generate a concise, Bullhorn-ready summary of the call.

    The output is designed to be pasted directly into Bullhorn as a note.
    Three short sections: Call Summary, Key Points, Next Steps. Facts only —
    no speculation, no invention.
    """
    logger.info("Call summary started: model=%s chars=%d", model, len(transcript_text))

    recruiter = (metadata.get("recruiter_name") or "").strip() or "the recruiter"
    subject = (metadata.get("subject_name") or "").strip() or "the contact"

    system_prompt = (
        "You are a staffing recruiter assistant. The user will give you a "
        "transcript of a phone call. Your job is to produce a concise, "
        "factual summary that the recruiter can paste into Bullhorn as a "
        "note. Stay grounded in the transcript — do not invent details, "
        "speculate, or pad. Keep it tight."
    )

    user_prompt = (
        f"Recruiter: {recruiter}\n"
        f"Contact: {subject}\n\n"
        "Generate a Bullhorn-ready note using this exact structure. "
        "Omit any section that genuinely wasn't discussed:\n\n"
        "**Call Summary**\n"
        "[2-3 sentence overview of who spoke, why, and the overall outcome.]\n\n"
        "**Key Points**\n"
        "- [Most important facts the contact shared — role, availability, comp expectations, concerns, etc.]\n"
        "- [Use as many bullets as needed; keep each one to a single line.]\n\n"
        "**Next Steps**\n"
        "- [Concrete follow-ups agreed to on the call, with owner if mentioned.]\n\n"
        "Write in plain prose — no preamble, no apology, no recap of the instructions. "
        "Start directly with the **Call Summary** header.\n\n"
        f"TRANSCRIPT:\n{transcript_text}"
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
    logger.info("Call summary complete: model=%s chars=%d", model, len(result))
    return result


def analyze_call(
    transcript_text: str,
    call_type_label: str,
    template: str,
    metadata: Dict[str, Any],
    model: str = "gpt-4.1",
    technical_questions: str = "",
) -> str:
    """Produce a candidate information note from the call transcript.

    The output is a structured note about the candidate, organized by the
    sections in the template. Each question is followed by the candidate's
    response (summarized for readability, comprehensive for technical
    screening questions). It's a record FOR the candidate's file, not a
    recruiter coaching evaluation.
    """
    logger.info(
        "Call analysis started: model=%s call_type=%s chars=%d",
        model, call_type_label, len(transcript_text),
    )

    recruiter = (metadata.get("recruiter_name") or "").strip() or "the recruiter"
    subject = (metadata.get("subject_name") or "").strip() or "the contact"
    call_timestamp = (metadata.get("call_timestamp") or "").strip()

    system_prompt = (
        "You are a recruiting assistant for OakTree Staffing. The user will "
        "give you a transcript of a recruiter screening call plus an "
        "evaluation template listing the questions and topics the recruiter "
        "should have covered. Your job is to produce a clean, structured "
        "candidate information note suitable for pasting into the candidate's "
        "record in the ATS (Bullhorn). This is an informational document "
        "ABOUT THE CANDIDATE — not a recruiter performance review."
    )

    tech_block = ""
    if technical_questions and technical_questions.strip():
        tech_block = (
            "TECHNICAL SCREENING QUESTIONS PROVIDED BY THE RECRUITER:\n"
            "(These are the questions the recruiter intended to ask. In your "
            "Technical Screening Questions section, list each one in order "
            "and capture the candidate's COMPLETE answer from the transcript. "
            "Do not truncate, abbreviate, or omit details. Include every "
            "specific fact the candidate mentioned: years of experience, "
            "project names, dollar amounts, durations, technologies, tools, "
            "company names, dates, team sizes, etc.)\n"
            "----------\n"
            f"{technical_questions.strip()}\n"
            "----------\n\n"
        )

    user_prompt = (
        f"Call Type: {call_type_label}\n"
        f"Recruiter: {recruiter}\n"
        f"Candidate: {subject}\n"
        f"Call Date/Time: {call_timestamp or 'unknown'}\n\n"

        "EVALUATION TEMPLATE:\n"
        "----------\n"
        f"{template}\n"
        "----------\n\n"

        f"{tech_block}"

        "CALL TRANSCRIPT:\n"
        "----------\n"
        f"{transcript_text}\n"
        "----------\n\n"

        "Produce a candidate information note in markdown following this format.\n\n"

        "HEADER (top of the note):\n"
        f"# Interview with {subject}\n"
        f"**Recruiter:** {recruiter}\n"
        f"**Date:** {call_timestamp or 'unknown'}\n\n"

        "SECTIONS — for each section of the EVALUATION TEMPLATE above:\n"
        "1. Use the template's section name verbatim as a `## section heading`.\n"
        "2. For each question/topic in the section, output a bullet that begins "
        "with the question text, then the candidate's response in clean, "
        "factual prose based on what they actually said in the transcript.\n"
        "3. Write the response as informative prose — NOT verbatim quotes in "
        "quotation marks. Capture every specific fact: names, numbers, dates, "
        "locations, dollar amounts, durations, company names, technologies. "
        "Use semicolons to separate distinct facts in a single bullet when needed.\n"
        "4. If a question was not asked, OR the candidate did not give a "
        "substantive answer, write `Not discussed.` after the bullet.\n"
        "5. Do NOT include 'Covered' / 'Partially Covered' / 'Missed' labels. "
        "This is a candidate information document, not a coaching report.\n"
        "6. Do NOT add commentary about the recruiter's performance or technique.\n\n"

        "TECHNICAL SCREENING QUESTIONS section (special rules):\n"
        "- Number each technical question as `1.`, `2.`, `3.`, etc.\n"
        "- Provide the candidate's COMPLETE answer for every technical "
        "question — do not truncate, abbreviate, or drop details. "
        "Long, multi-fact answers are expected and welcome. This section "
        "is the recruiter's definitive record of the candidate's technical "
        "depth and will be read by hiring managers.\n"
        "- If a specific technical question was not asked, write "
        "`Not discussed.` below it.\n\n"

        "OOPS SECTION (if the template has one):\n"
        "List every question or topic from the previous sections that was "
        "answered `Not discussed.` or otherwise not substantively covered. "
        "One bullet per item, just the question/topic. No commentary.\n\n"

        "FINAL RULES:\n"
        "- Use markdown formatting throughout (headings, bullets, bold).\n"
        "- Stop after the last template section. Do NOT append a wrap-up "
        "Summary, Coaching Note, Overall Assessment, or closing paragraph.\n"
        "- Be factual and direct — this is reference material that goes "
        "into the candidate's ATS record.\n"
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
