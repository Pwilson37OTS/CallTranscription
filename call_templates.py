"""Call coaching templates.

Each entry defines what a successful call of that type should cover.
When a recruiter selects a call type and clicks "Analyze Call" on the
Calls tab, the diarized transcript is graded against this template
by an LLM. The output is a three-section evaluation: what was covered
well, what was not covered well enough, and what was missed entirely.

Editing templates:
- Replace the PLACEHOLDER text below with the actual OakTree template
  for each call type. Plain text is fine; the LLM doesn't need any
  particular format.
- You can re-organize sections, use bullet points, prose, or a mix.
- After saving, the next "Analyze Call" click picks up the new template
  immediately. No app restart required (Streamlit auto-reloads on save).
"""

CALL_TEMPLATES = {
    "standard_call": {
        "label": "Standard Call",
        "template": """\
PLACEHOLDER — replace with the OakTree Standard Call template.

Suggested points to cover:
- Warm introduction and rapport-building
- Confirm purpose of the call
- Gather relevant information from the contact
- Communicate clear next steps
- Confirm timing/availability for any follow-up
""",
    },
    "followup": {
        "label": "Follow-up Call",
        "template": """\
PLACEHOLDER — replace with the OakTree Follow-up Call template.

Suggested points to cover:
- Reference the prior conversation
- Update on any progress since last contact
- Address any open questions from the previous call
- Confirm next milestones
- Schedule the next touchpoint
""",
    },
    "coldcall": {
        "label": "Cold Call",
        "template": """\
PLACEHOLDER — replace with the OakTree Cold Call template.

Suggested points to cover:
- Brief, compelling introduction (who you are, why you're calling)
- Establish credibility / value proposition
- Identify a pain point or need
- Qualify level of interest
- Either schedule a next step or close politely
""",
    },
    "screening_call": {
        "label": "Screening Call",
        "template": """\
PLACEHOLDER — replace with the OakTree Screening Call template.

Suggested points to cover:
- Candidate background and current role
- Location and work authorization
- Compensation expectations
- Availability and notice period
- Relevant experience for the role
- Career goals / motivators
- Next steps in the hiring process
""",
    },
    "interview_prep": {
        "label": "Interview Prep",
        "template": """\
PLACEHOLDER — replace with the OakTree Interview Prep template.

Suggested points to cover:
- Interview logistics (date, time, format, location or link)
- Who the candidate will be meeting and their roles
- Brief on company background and culture
- Likely interview questions to expect
- Strong questions the candidate should ask
- Compensation/expectations alignment
- Post-interview follow-up plan
""",
    },
}
