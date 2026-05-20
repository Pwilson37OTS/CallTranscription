"""Call coaching templates.

Each entry defines what a successful call of that type should cover. When a
recruiter selects a call type and clicks "Analyze Call" on the Calls tab, the
diarized transcript is graded against this template by an LLM, which mirrors
the template's structure in its response.

Editing templates:
- Just edit the `template` string below — plain text or markdown both work.
- The LLM follows the template's structure: if you use section headers (like
  "Rate", "Logistics"), it uses those exact headers in its evaluation.
- You can name custom sections like "OOPS Section" or "Technical Screening
  Questions" with their own instructions; the LLM follows them.
- After saving, the next "Analyze Call" click picks up the new template
  immediately. No app restart required.
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
    "interview_prep": {
        "label": "Interview Prep",
        "template": """\
Interview Logistics
the recruiter should confirm (list the item within the response):
• Date and time of the interview, including time zone
• Total duration the candidate should plan for
• Interview format – phone, video, or on-site
• Platform and meeting link if virtual (Zoom, Teams, Google Meet, etc.)
• Address and parking/check-in instructions if on-site
• Dress code expectations for the format

Interviewer Information
the recruiter should review (list the item within the response):
• Full name and job title of each interviewer the candidate will meet
• Each interviewer's role on the team and likely areas of focus
• Background or specialty of each interviewer (where known)
• Encourage the candidate to review each interviewer's LinkedIn profile beforehand

Company & Role Context
the recruiter should cover (list the item within the response):
• Brief company overview – what the company does and any recent news the candidate should know
• Company culture, values, and work environment
• The specific team the candidate would be joining and the reporting structure
• Why this role exists and what success looks like in the first 30/60/90 days
• Any context about the hiring manager's priorities for this hire

Likely Interview Questions
the recruiter should prepare the candidate for (list the item within the response):
• Common behavioral questions to expect – walk through the STAR method (Situation, Task, Action, Result)
• Common technical or role-specific questions likely for this position
• "Why are you leaving?" / "Why this role?" – help the candidate articulate clearly
• Have the candidate think through 2-3 strong STAR examples that highlight relevant accomplishments
• Anticipate questions about resume gaps, transitions, or unusual aspects of their work history

Questions for the Candidate to Ask
the recruiter should suggest (list the item within the response):
• At least 3 strong, role-specific questions the candidate should be ready to ask
• A question about the team and day-to-day culture
• A question about success metrics or what a strong first 90 days looks like
• A question about next steps in the hiring process and timeline

Compensation & Expectations Alignment
the recruiter should reconfirm (list the item within the response):
• Reconfirm the agreed rate or salary range with the candidate
• Confirm benefits expectations are aligned (insurance, PTO, remote flexibility, etc.)
• Confirm preferred start date and any notice the candidate would need to give
• Flag any open items that could come up (relocation, equipment, travel)
• Reaffirm the candidate's interest level – are they fully committed to this opportunity?

Logistics Confirmation
the recruiter should confirm (list the item within the response):
• Candidate's availability at the scheduled time – any conflicts or PTO planned?
• Technology check for virtual interviews – camera, microphone, internet, distraction-free environment
• Travel arrangements for on-site interviews – does the candidate have what they need?
• Backup plan if something goes wrong (alternate phone number, who to contact if late)

Next Steps / Follow-up
the recruiter should outline (list the item within the response):
• Timeline for hearing back after the interview
• Encourage the candidate to send a thank-you note or email within 24 hours
• When the recruiter will follow up with the candidate after the interview
• What the next round of the process looks like (additional interviews, references, offer)
• Any additional materials needed from the candidate (work samples, references, etc.)

OOPS Section
• List any questions or items from the previous sections that the recruiter forgot to cover with the candidate.

Coaching Notes
• Highlight any additional points the recruiter made that were not in the template above.
• Flag any moments where the candidate seemed unsure, hesitant, or under-prepared that the recruiter could revisit in a follow-up.
• Note tone, rapport, and confidence observations briefly.
""",
    },
}
