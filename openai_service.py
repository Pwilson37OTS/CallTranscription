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
        "You are a recruiter call coach. The user will give you a call transcript "
        "and an evaluation template. Follow the template precisely — its sections, "
        "structure, and instructions all guide your output. For every point listed "
        "in the template, evaluate whether the recruiter addressed it based on the "
        "transcript. Cite or paraphrase the transcript briefly to support each "
        f"finding. Be specific, direct, and constructive — the recruiter ({recruiter}) "
        "will read this feedback to improve."
    )

    tech_block = ""
    if technical_questions and technical_questions.strip():
        tech_block = (
            "RECRUITER-PROVIDED TECHNICAL SCREENING QUESTIONS:\n"
            "(These are questions the recruiter intended to ask. Verify whether "
            "each was actually asked in the transcript and capture the "
            "candidate's verbatim response. Include this in the Technical "
            "Screening Questions section of your output.)\n"
            "----------\n"
            f"{technical_questions.strip()}\n"
            "----------\n\n"
        )

    user_prompt = (
        f"Call Type: {call_type_label}\n"
        f"Recruiter: {recruiter}\n"
        f"Contact / Candidate: {subject}\n\n"
        "EVALUATION TEMPLATE:\n"
        "----------\n"
        f"{template}\n"
        "----------\n\n"
        f"{tech_block}"
        "CALL TRANSCRIPT:\n"
        "----------\n"
        f"{transcript_text}\n"
        "----------\n\n"
        "Produce a markdown evaluation that mirrors the template's structure.\n\n"
        "1. Use the template's section headers verbatim as ## headings in your "
        "output (e.g. ## Work Status, ## Rate, ## Logistics).\n\n"
        "2. For each point or question listed under a section, output a bullet "
        "that includes ALL of the following:\n"
        "   - The template point (the question or topic).\n"
        "   - Status: **Covered**, **Partially Covered**, or **Missed**.\n"
        "   - **The candidate's exact verbatim response from the transcript, in "
        "quotes**, when status is Covered or Partially Covered. Copy what the "
        "candidate said word-for-word — do not paraphrase, summarize, or clean "
        "up filler. If the candidate gave a long response, use a representative "
        "verbatim sentence or two, then 'continues...' if more was said. If the "
        "recruiter asked the question but the candidate gave no substantive "
        "answer, note that. If status is Missed, no quote is needed.\n\n"
        "Example bullet format for a Covered item:\n"
        "   - **Why looking to move?** — **Covered**: \"I've been at my current "
        "place for four years and I'm just not learning anything new. The team "
        "is small and there's no path up.\"\n\n"
        "3. If the template has an 'OOPS Section' (or similar 'missed items' "
        "section), populate it by listing every point you marked **Missed** above. "
        "If nothing was missed, write '_All required points were covered._'\n\n"
        "4. If the template has a section asking you to list technical screening "
        "questions, coaching notes, or any other free-form addition not in the "
        "main template, fill those in based on what you observe in the transcript. "
        "Include verbatim quotes there as well.\n\n"
        "5. If the template is clearly a placeholder (e.g. it says PLACEHOLDER), "
        "evaluate against the suggested points it lists using three sections: "
        "## Covered Well, ## Not Covered Well Enough, ## Missed, still including "
        "verbatim quotes where applicable. Then add a note that the template is "
        "a placeholder and should be replaced with the real one.\n\n"
        "Be honest but constructive. Quote accurately."
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
