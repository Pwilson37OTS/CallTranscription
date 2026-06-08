# ECHO — Recruiter Call Review

Upload recruiter call recordings, automatically transcribe them via OpenAI, and generate ATS-ready summaries with call-type-specific formatting.

## Prerequisites

- **Python 3.11+**
- **ffmpeg** — required for audio conversion
  - Windows: `winget install Gyan.FFmpeg`
  - macOS: `brew install ffmpeg`
  - Linux: `sudo apt install ffmpeg`
- **OpenAI API key** with access to transcription and chat models

## Setup

1. Clone the repository and install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

2. Create your environment file:

   ```bash
   cp .env.example .env
   ```

3. Edit `.env` and add your OpenAI API key:

   ```
   OPENAI_API_KEY=sk-...
   ```

4. Run the application:

   ```bash
   streamlit run app.py
   ```

## Workflow

1. **Upload** — Select an audio file and enter call metadata (recruiter, candidate, company, call type)
2. **Convert** — Audio is automatically converted to WAV via ffmpeg
3. **Transcribe** — Click "Transcribe" to send audio to OpenAI's transcription API
4. **Summarize** — Click "Generate ATS Summary" to create structured notes based on call type
5. **Review** — Edit the transcript or summary as needed
6. **Export** — Download as TXT or copy from the code block into your ATS

## Call Types

- **Screening Call** — Candidate screening with details, strengths, concerns, next steps
- **Reference Check** — Reference validation with strengths, development areas, rehire sentiment
- **Interview Prep** — Pre-interview coaching with prep topics, logistics, questions
- **Post-Interview Rundown** — Debrief with candidate feedback, concerns, interest level
- **Offer Extension** — Offer discussion with compensation, objections, requested changes
- **General Recruiter Call** — Fallback format for any other call type

## Project Structure

```
app.py              — Streamlit UI
config.py           — Environment-based configuration
prompts.py          — Call type prompt templates
db.py               — Database operations
openai_service.py   — OpenAI transcription and summarization
file_service.py     — File upload and audio conversion
styles.py           — CSS styling
```
