# Round 2 submission text (copy into the form)

Team: 24110lt358 · Team leader: Ayush Dwivedi · G. L. Bajaj Institute of Technology and Management

## Working project link
[paste your deployed link here after deploying, and test it in a private window]

Judge quick start: open the link, click **Creator**, open "Spring drop on Instagram", respond to the findings, apply your changes, submit. Log out, click **Manager**, approve.

## Source-code repository link
[paste your public GitHub repo link here]

## List of completed features
- Brand knowledge base: guidelines, approved facts, terminology (preferred and banned), approved claims, colour palette; admin can add, edit, delete, and import .txt/.md guidelines
- Text review across tone, terminology, facts, promotional claims, palette and accessibility (alt text, link text, sentence length, reading grade)
- Evidence-linked findings: severity, reason, exact source passage, confidence; "Needs human judgment" when no approved source applies
- Intent-preserving suggestions shown as a diff; nothing applied automatically
- Creator control: accept, edit or dispute (with reason) each finding; chosen changes create a new version that is re-checked; responses carry over
- Roles and approval workflow: creator, manager, reviewer, admin; approve / request changes / reject; creator cannot approve own content; high-severity findings must be answered before submitting
- Comments (general or on a finding)
- Revision history with word-level tracked changes, and a full audit trail
- Optional AI layer (Claude API) grounded in retrieved approved passages, with code-verified citations, redaction of emails and phone numbers, per-user and daily rate limits, and graceful fallback to rule-based checks
- AI and external-service disclosure on every page and on an About page
- Security basics: hashed passwords, CSRF tokens, output escaping, role checks on every route
- 29 automated tests (rules, citation verification, AI layer with mocked responses, full workflow, permissions, XSS, CSRF)

## Technology and implementation details
- Python 3.12, Flask (server-rendered pages), SQLite, Gunicorn; deployed on [host]
- Review engine (engine.py): deterministic checks, optional AI check with retrieval of approved passages, citation verification, span-level replacement suggestions
- AI: Claude API (Anthropic), model claude-sonnet-5-5, called over HTTPS with the requests library; AI output must be valid JSON, the quoted content must exist in the submission, and the cited source quote must exist in the cited approved passage, otherwise the finding is downgraded to low confidence with no source
- Libraries: Flask, Requests, Gunicorn. No templates or boilerplate
- AI-assisted development: Claude (Anthropic) was used to help write and test the code; the team member reviewed, ran and deployed it
- Tests: `python -m unittest discover -s tests`

## Explanation of changes made to the original plan
- One Flask app replaces the planned Next.js + FastAPI split, for a single reliable deployment by a solo developer
- SQLite and keyword retrieval replace PostgreSQL + pgvector; the knowledge base is small, so this is enough for now and removes an external dependency during judging. Embedding search stays on the roadmap
- Server-side sessions replace JWT (simpler and safer for server-rendered pages)
- Per-organisation isolation is deferred (single demo organisation)
- Added since the plan: code-verified citations, a rule that high-severity findings must be answered before submission, version carry-over of responses, AI rate limits

## Three major improvements for Round 3
1. **Guideline-gap detection.** Use accept / edit / dispute data to find guidelines that people often dispute or interpret differently, flag them as unclear, and propose clearer wording for the admin to approve.
2. **Recurring-mistake insights dashboard.** Top issue types, trends over time, and patterns by team or channel, aggregated for training rather than blame.
3. **Image and document review.** PDF and Word upload with text extraction and review, plus image checks (palette, contrast, missing alt text, text readability, and a vision-model review of off-brand visuals).
