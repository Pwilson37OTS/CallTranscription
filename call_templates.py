"""Call coaching templates.

Each entry defines what a successful call of that type should cover. When a
recruiter selects a call type and clicks "Analyze Call" on the Calls tab, the
diarized transcript is graded against this template by an LLM, which mirrors
the template's structure in its response and includes the candidate's
verbatim quotes from the transcript.

Standard Call is included as a category but doesn't get a coaching evaluation —
the transcript and Bullhorn-ready summary are sufficient for that call type.

Editing templates:
- Just edit the `template` string below — plain text or markdown both work.
- The LLM mirrors the template's structure in its output.
- You can name custom sections like "OOPS Section" with their own instructions.
- Streamlit auto-reloads on save; the next Analyze Call click picks up changes.
"""

CALL_TEMPLATES = {
    "screening_call": {
        "label": "Screening Call",
        "template": """\
Work Status
the recruiter should ask (list the question within the response):
• Candidate's U.S. work status

Screening Information
the recruiter should ask (list the question within the response):
• Why is the candidate looking to make a move?
• How far along are they in their job search? Are they considering any other positions right now?
• Go through the last 7 years or 5 jobs on their resume (whichever is fewer) and ask why they left - layoffs, contract completed, etc. Be specific.
• If they would be going from FTE to contract, or contract to FTE, make sure they understand all the differences and are okay with the change.
• Have they ever been submitted to/interviewed with this client previously?
• Is the candidate able to pass a full background check and drug test (including a hair follicle test for COP jobs)?

Rate
the recruiter should ask (list the question within the response):
• What are they making now? (If they are not currently employed, what did they make in their most recent role?)
• What are they wanting to make? – Make sure they understand this is an all-inclusive rate that has to cover any kind of travel expenses they may have.
• What is the final rate you agreed on with the candidate?
• How are they wanting to be paid (W2, C2C)? If C2C, do they have the required insurances?
• Make sure they understand they will only be paid for the hours they work – no paid sick days, holidays, vacation, etc.
• Go over insurance information with them.

Logistics
the recruiter should ask (list the question within the response):
• Where are they currently located?
• What is their preferred location?
• Make sure they are okay with the work hours for this job.
• Discuss in-person expectations – Onsite, Remote, Hybrid, Etc.
• Commute – How long will their drive be? Are they okay with that commute time long-term?

Next Steps
the recruiter should ask (list the question within the response):
• What is their availability for a phone interview?
• What is their availability for an in-person interview?
• What is their availability for a virtual interview?
• How much notice do they need to give before they could start? – Make sure they include notice to their current employer and time to relocate, if needed.
• Do they have any time off planned in the next couple of months?

Submittal Information
the recruiter should ask (list the question within the response):
• Birthday month and day?
• Last four digits of SSN?
• Recruiter should make sure all required skills are listed on their resume.
• Recruiter should discuss a Right to Represent.
• Recruiter should discuss needing 2-3 supervisor references.

OOPS Section
• List any questions from the previous sections that the recruiter forgot to ask the candidate.

Technical Screening Questions
• List the question and answer for any technical questions the recruiter asks during the call that are not written above.
""",
    },
    "post_interview_rundown": {
        "label": "Post Interview Rundown",
        "template": """\
Interview Participants
the recruiter should ask (list the question within the response):
• Who was in the interview and what were their roles?

Interview Dynamics
the recruiter should ask (list the question within the response):
• What was the mood of the interview?
• Who did most of the talking?

Interview Structure
the recruiter should ask (list the question within the response):
• Was the interview a pre-determined list of questions or a free-flow conversation around resume and skills?
• Did the interviewers ask behavioral or situational questions? If so, what were they?

Role-Specific Questions
the recruiter should ask (list the question within the response):
• Did the interviewers ask anything specifically about the role?
• Technical questions about the role
• Personality / fit questions
• Team environment questions

Process & Timeline
the recruiter should ask (list the question within the response):
• Was there discussion of next steps?
• Did the interviewers mention other candidates they are considering?
• Was a timeline discussed for a decision?

Candidate's Pipeline
the recruiter should ask (list the question within the response):
• Does the candidate have any other roles they are pursuing? If so, where are they in that process?

Offer Readiness
the recruiter should ask (list the question within the response):
• If the client offers the candidate this role, are they ready to accept?

Follow-up Coaching
the recruiter should advise (list the item within the response):
• Advise the candidate to send a thank-you email that the recruiter can forward to the account manager.

OOPS Section
• List any questions or items from the previous sections that the recruiter forgot to cover with the candidate.
""",
    },
}
