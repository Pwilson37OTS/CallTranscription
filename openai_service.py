import os
import re
from collections import Counter
from typing import Dict, Any, Optional

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

# Chunk any recording longer than this. The transcription models have a bounded
# output length, and they occasionally get stuck in a repetition loop that
# consumes the whole output budget before reaching the end of the audio — which
# both duplicates a passage AND drops the final minutes. Sending the call as
# many short segments bounds the damage: the tail is in its own request with its
# own budget, and a loop can only poison one segment (which we then retry).
_TRANSCRIBE_CHUNK_THRESHOLD_SECONDS = 360   # 6 minutes
_TRANSCRIBE_CHUNK_SECONDS = 300             # 5-minute segments

# The two transcription models we alternate between on failure. When one
# produces a bad result we retry with the *other* — retrying the same model
# rarely helps, and the two fail on different inputs.
_TRANSCRIBE_MODELS = ("gpt-4o-transcribe", "whisper-1")

# Below this words-per-minute, a transcript is almost certainly a failure:
# either the model hallucinated a few random phrases on silence/noise, or it
# returned almost nothing. Real speech runs ~120-150 wpm; even a sparse call is
# well above 20. Only applied to segments >=60s so short clips don't false-trip.
_MIN_WORDS_PER_MINUTE = 20


def _fallback_model_for(model: str) -> str:
    """Pick a transcription model different from the one that just failed."""
    return _TRANSCRIBE_MODELS[1] if model == _TRANSCRIBE_MODELS[0] else _TRANSCRIBE_MODELS[0]


def _transcribe_single_file(
    file_path: str,
    model: str,
    temperature: Optional[float] = None,
    prompt: Optional[str] = None,
) -> str:
    """Send one audio file to the OpenAI transcription endpoint."""
    client = get_openai_client()
    kwargs: Dict[str, Any] = {"model": model}
    if temperature is not None:
        kwargs["temperature"] = temperature
    if prompt:
        kwargs["prompt"] = prompt
    with open(file_path, "rb") as audio_file:
        transcript = client.audio.transcriptions.create(file=audio_file, **kwargs)
    return transcript.text


def _looks_repetitive(text: str) -> bool:
    """Heuristic detector for transcription repetition-loop failures.

    Flags output where the model got stuck repeating a passage. Uses only
    substantive sentences (>=15 chars) so normal short filler ("Yes, sir.")
    doesn't trip it. Three signals:
      - the same sentence repeated 4+ times back-to-back,
      - a single sentence appearing 6+ times overall,
      - more than half of the substantive sentences being exact duplicates.
    Real recruiter calls repeat some phrases, but not at these levels.
    """
    if not text:
        return False

    parts = [p.strip().lower() for p in re.split(r"[.!?\n]+", text) if len(p.strip()) >= 15]
    if len(parts) < 6:
        return False

    # Longest run of the same sentence appearing consecutively.
    longest_run = run = 1
    for i in range(1, len(parts)):
        run = run + 1 if parts[i] == parts[i - 1] else 1
        longest_run = max(longest_run, run)
    if longest_run >= 4:
        return True

    counts = Counter(parts)
    most_common_count = counts.most_common(1)[0][1]
    if most_common_count >= 6:
        return True

    duplicate_ratio = 1 - (len(counts) / len(parts))
    if len(parts) >= 10 and duplicate_ratio > 0.5:
        return True

    return False


def _is_thin_transcript(text: str, duration_seconds) -> bool:
    """True if the transcript has implausibly little text for the audio length.

    Catches the hallucination-on-silence failure: the model returns a handful of
    random (often multilingual) phrases for many minutes of audio. Needs a known
    duration of at least 60s to judge.
    """
    if not duration_seconds or duration_seconds < 60:
        return False
    words = len((text or "").split())
    minutes = duration_seconds / 60.0
    return words < minutes * _MIN_WORDS_PER_MINUTE


def _is_bad_transcript(text: str, duration_seconds) -> bool:
    """A transcript is bad if it loops (repetition) or is implausibly thin."""
    return _looks_repetitive(text) or _is_thin_transcript(text, duration_seconds)


def _transcribe_segment(file_path: str, model: str, duration_seconds=None) -> str:
    """Transcribe one file, retrying with the *other* model if the result is bad.

    "Bad" = a repetition loop or implausibly thin output (hallucination on
    silence). Returns the best available text; if both models produce bad output
    we keep the longer of the two so the pipeline still gets what was salvageable.
    """
    text = _transcribe_single_file(file_path, model)
    if not _is_bad_transcript(text, duration_seconds):
        return text

    fallback_model = _fallback_model_for(model)
    logger.warning(
        "Low-quality transcription (model=%s, file=%s); retrying with %s",
        model, file_path, fallback_model,
    )
    try:
        # temperature=0 stabilizes whisper-1; gpt-4o-transcribe ignores it.
        temp = 0.0 if fallback_model == "whisper-1" else None
        fallback = _transcribe_single_file(file_path, fallback_model, temperature=temp)
    except Exception as e:
        logger.warning("Fallback transcription failed for %s: %s", file_path, e)
        return text

    if _is_bad_transcript(fallback, duration_seconds):
        logger.warning(
            "Both models produced low-quality transcription (file=%s); "
            "keeping the longer result", file_path,
        )
        return fallback if len(fallback or "") > len(text or "") else text
    return fallback


def transcribe_audio(file_path: str, model: str = "gpt-4o-transcribe") -> str:
    """Transcribe an audio file, chunking longer calls into short segments.

    Recordings longer than ~6 minutes (or over the 24 MB single-request limit)
    are split with ffmpeg into ~5-minute MP3 segments and transcribed
    independently, then concatenated. Each segment is guarded against the
    models' repetition-loop failure mode (see `_transcribe_segment`). Speaker
    diarization runs afterward on the combined text.
    """
    from pathlib import Path
    from file_service import get_audio_duration_seconds, split_audio_for_transcription

    path_obj = Path(file_path)
    size_bytes = path_obj.stat().st_size
    duration = get_audio_duration_seconds(path_obj)

    logger.info(
        "Transcription started: model=%s file=%s size_mb=%.1f duration=%s",
        model, file_path, size_bytes / (1024 * 1024),
        f"{duration:.0f}s" if duration else "unknown",
    )

    # Decide whether to chunk. Chunk when the call is long, when it's over the
    # single-request size limit, OR when the duration is unknown — an unknown
    # duration means ffprobe couldn't read the file, so we can't assume it's
    # short, and a long call sent as one request is a known hallucination
    # trigger. Chunking a genuinely short file just yields a single segment.
    needs_chunking = (
        duration is None
        or duration > _TRANSCRIBE_CHUNK_THRESHOLD_SECONDS
        or size_bytes > _TRANSCRIBE_DIRECT_LIMIT_BYTES
    )

    if not needs_chunking:
        text = _transcribe_segment(file_path, model, duration_seconds=duration)
        logger.info("Transcription complete: model=%s chars=%d", model, len(text))
        return text

    logger.info("Chunking recording into %ds segments", _TRANSCRIBE_CHUNK_SECONDS)
    chunks = split_audio_for_transcription(
        path_obj, chunk_seconds=_TRANSCRIBE_CHUNK_SECONDS
    )
    chunks_dir = chunks[0].parent
    try:
        parts = []
        real_parts = 0  # chunks that produced actual transcript text (not a gap marker)
        for i, chunk_path in enumerate(chunks, start=1):
            logger.info(
                "Transcribing chunk %d/%d: %s (%.1f MB)",
                i, len(chunks), chunk_path.name,
                chunk_path.stat().st_size / (1024 * 1024),
            )
            chunk_duration = get_audio_duration_seconds(chunk_path)
            try:
                parts.append(_transcribe_segment(str(chunk_path), model, duration_seconds=chunk_duration))
                real_parts += 1
            except Exception as e:
                # One chunk failing (both models errored) must not throw away the
                # whole call — leave a labeled gap and keep the other segments.
                logger.error(
                    "Chunk %d/%d failed permanently: %s", i, len(chunks), e
                )
                parts.append(f"[Transcription unavailable for audio segment {i} of {len(chunks)}]")

        if real_parts == 0:
            # Nothing transcribed at all — surface as an error (retryable) rather
            # than storing a transcript that's only gap markers.
            raise RuntimeError("Transcription failed for every audio segment.")

        combined = "\n\n".join(parts)
        logger.info(
            "Transcription complete (chunked): chunks=%d ok=%d total_chars=%d",
            len(chunks), real_parts, len(combined),
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
            "Technical Screening Questions section, list each one in order. "
            "For each one, the candidate's answer MUST be quoted verbatim "
            "from the transcript — word-for-word, including every detail. "
            "Do not paraphrase, summarize, or abbreviate technical answers.)\n"
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

        "TECHNICAL SCREENING QUESTIONS section (special rules — different "
        "from the rest of the note):\n"
        "- Number each technical question as `1.`, `2.`, `3.`, etc., in the "
        "order the recruiter provided them.\n"
        "- The candidate's response under each question MUST be a VERBATIM "
        "QUOTE from the transcript, wrapped in quotation marks. Copy what "
        "the candidate actually said word-for-word — every word, every "
        "sentence, every specific fact. Do NOT paraphrase, summarize, "
        "abbreviate, clean up filler ('um', 'like', 'you know'), correct "
        "grammar, or rephrase for readability.\n"
        "- If the candidate's answer spans multiple turns in the transcript "
        "(because the recruiter interjected to clarify), concatenate all "
        "of the candidate's relevant turns into one continuous quoted "
        "response. Use `[...]` to indicate any place where recruiter speech "
        "or unrelated content was skipped.\n"
        "- Long quotes are expected and required — do not shorten. This "
        "section is the candidate's definitive technical record and will "
        "be read by hiring managers.\n"
        "- This rule overrides the general 'concise prose, no quotes' rule "
        "from section 3 above. For this section ONLY, quote everything "
        "verbatim.\n"
        "- If a specific technical question was not asked, write "
        "`Not discussed.` below it (no quotes needed).\n\n"

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
