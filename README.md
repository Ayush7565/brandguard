# BrandGuard

Pre-publication brand governance. Content goes in, it is checked against the organisation's **approved** guidelines, facts, terminology, claims and palette, every issue is explained with the source it relied on, and a **person** makes the final decision.

Built for CodeNexus India 2026 (Inkloom AI problem statement). Team 24110lt358.

## What works now (Round 2)

1. **Brand knowledge base** (admin): guidelines, approved facts, terminology (preferred and banned), approved claims, colour palette. Paste text, or import `.txt` / `.md`.
2. **Text review** for tone, terminology, facts, promotional claims, visual palette and accessibility.
3. **Evidence-linked findings**: every finding shows its severity, a plain-language reason, the exact source passage it relied on, and a confidence level. If no approved source covers an issue it is labelled **"Needs human judgment"** instead of being stated as a violation.
4. **Intent-preserving suggestions**: a suggestion replaces only the flagged words and is shown as a diff. Nothing is applied automatically.
5. **Creator control**: accept, edit ("my way") or dispute (with a reason) each finding. Chosen changes are applied as a **new version**, and the text is re-checked.
6. **Review and approval workflow** with roles: creator, manager, reviewer, admin. Comments (general or on a finding), Approve / Request changes / Reject, and a creator cannot approve their own content.
7. **Revision history and audit trail**: versions with word-level tracked changes, and a log of every action.
8. **AI layer (optional)**: Claude API finds tone / messaging / fact / claim issues using only retrieved approved passages. Quotes are verified in code; unverifiable findings are downgraded. Emails and phone numbers are redacted before anything is sent. Rate limited. If the AI is down or not configured, the rule-based checks still run.
9. **Disclosure**: a visible AI notice on every page and an About page listing the services used.

Planned for Round 3: recurring-mistake insights dashboard, guideline-gap detection, image and document (PDF/DOCX) review.

## Try it in one minute

Open the app, click **Creator**, open "Spring drop on Instagram", respond to the findings, **Apply** your chosen changes, **Submit for approval**. Then log out, click **Manager**, and approve. Demo brand: Lumora (fictional).

## Architecture

```
Browser (server-rendered pages, no JS framework)
        |
Flask app (app.py): auth + roles + workflow + audit
        |-- engine.py: review engine
        |     1. deterministic checks (terms, claims, facts, palette, accessibility, tone)
        |     2. optional AI check (retrieval of approved passages -> Claude API -> citation verification)
        |     3. intent-preserving suggestions (replace only the flagged span)
        |-- db.py: SQLite (users, kb_items, submissions, versions, reviews, findings, comments, audit, ai_usage)
        `-- seed.py: fictional demo brand
```

Why this design: rule-based checks handle everything that can be computed, so reliable results never depend on a model. The AI is added only where judgment is needed, and its output must pass a source check before it is shown with a citation.

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env     # optional; set ANTHROPIC_API_KEY to enable the AI layer
python app.py            # http://127.0.0.1:5000
```

Load `.env` values into your shell, or just export them: `export ANTHROPIC_API_KEY=...`.

## Tests

```bash
python -m unittest discover -s tests -v
```

29 tests cover the rules, citation verification (including invented sources), redaction, the AI layer with mocked responses (success, HTTP error, timeout, rate limit), the full creator-to-manager workflow, permissions, XSS escaping and CSRF.

## Deploy (Render, free tier)

1. Push this repo to GitHub.
2. On render.com: **New → Blueprint**, pick the repo (it reads `render.yaml`). Or **New → Web Service** with build `pip install -r requirements.txt` and start `gunicorn app:app --workers 1 --threads 4 --timeout 60`.
3. In the Render dashboard set `ANTHROPIC_API_KEY` (optional; without it only rule-based checks run).
4. Open the URL in a private window and check it loads **without signing in** (demo logins are on the login page).

The free tier sleeps when idle (first load can take about 30 seconds) and its disk is not persistent, so the demo database resets on restart. That is acceptable for a demo brand; do not enter real confidential data.

## Configuration

| Variable | Purpose | Default |
|---|---|---|
| `SECRET_KEY` | Session signing | dev value (set your own in production) |
| `ANTHROPIC_API_KEY` | Enables the AI layer | empty (rule-based only) |
| `ANTHROPIC_MODEL` | Model name | `claude-sonnet-5-5` |
| `AI_USER_HOURLY_LIMIT` / `AI_DAILY_LIMIT` | Cost protection | 15 / 300 |
| `DEMO_MODE` | One-click demo logins | `1` |
| `DATABASE_PATH` | SQLite file | `instance/brandguard.db` |

## Responsible design

- **Human oversight:** AI and rules only suggest. Applying a fix and approving content are always a person's action. Disputes are visible to the approver.
- **Explainability:** findings cite an approved source or are labelled "Needs human judgment".
- **Privacy:** emails and phone numbers are redacted before external calls; nothing is sent when the AI layer is off; secrets live in environment variables.
- **Security:** hashed passwords, role checks on every route, CSRF tokens, output escaping, size limits, security headers. Demo logins can be switched off with `DEMO_MODE=0`.
- **Accessibility:** semantic HTML, skip link, visible focus, labelled form fields, colour is never the only signal.
- **Transparency:** AI notice on every page and an About page.

## Changes from the Round 1 plan

| Plan | Built | Reason |
|---|---|---|
| Next.js + FastAPI | One Flask app with server-rendered pages | One deployable service and faster, more reliable delivery by a solo developer |
| PostgreSQL + pgvector | SQLite + keyword retrieval | No external database to fail during judging; the knowledge base is small, so keyword retrieval is enough for now. Embeddings stay on the roadmap |
| Per-organisation isolation | Single demo organisation | Deferred; documented limitation |
| JWT auth | Server-side sessions | Simpler and safer for a server-rendered app |
| Image and document review, insights, guideline-gap detection | Round 3 | Kept as the named Round 3 improvements |

## Known limitations

- Text only in this version (image and document review come in Round 3).
- Fact checking looks for numbers near trigger keywords; unusual phrasing can be missed. AI findings can be wrong, which is why people approve everything.
- SQLite on a free host is not persistent; no multi-organisation isolation yet.

## AI and external services used

- Claude API (Anthropic), optional, model set by `ANTHROPIC_MODEL`.
- Python libraries: Flask, Requests, Gunicorn (all open source). No templates or boilerplate were used.
- AI-assisted development: Claude (Anthropic) was used to help write and test this code; the team member reviewed, ran and deployed it.
