# Ops HQ

**Command center for delivery ops** — Slack bot, web dashboard, Jira integration, and AI-powered workflows in one stack.

Ops HQ gives you a single place to run projects, clients, and tickets: morning briefs and check-ins in Slack, and a full dashboard for pipeline, grooming, oncall, workload, issues, documents, and AI tools. One SQLite database, one repo.

---

## What it does (end to end)

1. **Slack** — Slash commands and scheduled messages: add projects, log updates, move stages, flag risk, get a morning brief, run Jira checks, save brain dumps, log client issues, request recon status, and more. Engineers get 4pm check-ins with one-click health updates; recon gets IUT status requests.
2. **Web dashboard** — Log in with email/password; see Brief, Pipeline (drag projects between stages), Clients, Ticket Grooming, Oncall tracker, Team Workload (with AI performance analysis), Docket, Eisenhower matrix (drag tasks between quadrants), Escalations, Scope & UAT, Issues, Documents, and AI tools (client updates, meeting prep, document generation, product scope, digest).
3. **Jira** — Optional. When configured, the brief and dashboard show stale/blocked tickets, oncall (CS-*) summary, grooming lists, pipeline tickets, and engineer performance stats. You can link Jira tickets to clients and request updates from assignees via Slack.
4. **AI (Anthropic Claude)** — Client update drafts, meeting prep, weekly digest, document generation from templates, product scope from escalations, and engineer performance analysis. Used from both Slack and the dashboard.

Everything reads and writes the same SQLite database, so Slack and the dashboard stay in sync.

---

## Architecture

| Layer        | Role |
|-------------|------|
| **Slack bot** (`bot.py`) | Bolt app, Socket Mode. Handles slash commands, scheduled jobs (9am brief, 4pm check-ins, recon IUT, week/month reports), and DMs. |
| **API** (`api.py`) | Flask app. Auth (token), REST for projects, clients, brief, Jira, issues, documents, escalations, Eisenhower, workload, AI. Serves the dashboard static files and returns JSON. All API errors return JSON (no HTML). |
| **Frontend** (`frontend/index.html`) | Single-page app. Vanilla JS, no build step. Calls API with bearer token; handles login and 401. |
| **Database** (`database.py`) | SQLite (WAL). Tables: projects, updates, issues, clients, reflections, checkins, eisenhower_tasks, product_escalations, documents, jira_ticket_client. |
| **Jira** (`jira_client.py`) | JQL search, grooming/pipeline/oncall tickets, engineer performance stats, Jira ↔ Slack mapping. |
| **AI** (`ai_client.py`) | Anthropic API. Client updates, meeting prep, digest, document templates, product scope, engineer performance analysis. |
| **PDF** (`pdf_export.py`) | FPDF2. Scope/UAT PDFs, oncall report PDF, ad‑hoc PDF from dashboard. |

**Run model:** Two processes. **Web** runs the Flask API (and serves the dashboard). **Worker** runs the Slack bot. On Railway they are separate services (Procfile: `web`, `worker`); locally run both (e.g. `python api.py` and `python bot.py`).

---

## Tech stack

- **Python 3.9+**
- **Slack**: Bolt, Socket Mode
- **Backend**: Flask, python-dotenv
- **DB**: SQLite (no extra server)
- **AI**: Anthropic (Claude)
- **Jira**: REST API (optional)
- **PDF**: FPDF2
- **Frontend**: HTML/CSS/JS (single file), no framework

---

## Getting started

### 1. Clone and install

```bash
git clone <repo-url>
cd "Ops HQ"
pip install -r requirements.txt
```

### 2. Environment

```bash
cp .env.example .env
# Edit .env with your values (see below and SETUP.md)
```

**Required for Slack bot**

- `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `SLACK_SIGNING_SECRET`
- `CS_BRIEF_SLACK_USER_ID` (your Slack user ID for DMs) or `CS_COMMAND_CHANNEL` (channel ID)

**Required for dashboard**

- `ADMIN_EMAIL`, `ADMIN_PASSWORD` (and optionally `COO_EMAIL`, `COO_PASSWORD` for read-only)

**Optional but recommended**

- **Jira**: `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, `JIRA_PROJECT_KEYS`; optionally `JIRA_TO_SLACK`, `JIRA_STALE_HOURS`, `JIRA_CREATED_SINCE`
- **AI**: `ANTHROPIC_API_KEY` (for client updates, meeting prep, digest, documents, scope, engineer performance)

Slack app setup (scopes, commands, Socket Mode) is documented in **SETUP.md**.

### 3. Run locally

**Terminal 1 — API (dashboard)**

```bash
python api.py
# Default port 5001; open http://localhost:5001
```

**Terminal 2 — Slack bot**

```bash
python bot.py
```

Or run both in one go: `python api.py & python bot.py` (as in Railway’s start command).

### 4. Deploy (e.g. Railway)

- Connect repo to Railway; use **Procfile** (`web: python api.py`, `worker: python bot.py`).
- Set all env vars in the dashboard; use a volume for `OPS_HQ_DB` / `CS_BOT_DB` if you want persistent SQLite.
- See **DEPLOY-RAILWAY.md** for details.

---

## Project structure

```
Ops HQ/
├── api.py              # Flask API + auth + all /api/* routes + serve frontend
├── bot.py              # Slack Bolt app, commands, scheduled jobs
├── database.py         # SQLite schema, init, CRUD
├── jira_client.py      # Jira search, grooming, oncall, performance stats
├── ai_client.py        # Anthropic client: updates, prep, digest, docs, scope, performance
├── pdf_export.py       # PDF generation (scope, UAT, oncall report)
├── messages.py         # Slack block kit helpers
├── frontend/
│   └── index.html      # Single-page dashboard (Brief, Pipeline, Clients, …)
├── requirements.txt
├── Procfile            # web + worker for Railway
├── railway.toml
├── .env.example        # Env template
├── SETUP.md            # Slack app setup (step-by-step)
├── DEPLOY-RAILWAY.md   # Railway deploy
├── CHANGELOG.md
└── ROADMAP.md
```

---

## Dashboard sections (summary)

| Section       | Purpose |
|---------------|--------|
| **Brief**     | Morning-style summary: pending, at risk, stale, go-live, completed, by client, open issues, Jira. |
| **Pipeline**  | Kanban by stage; drag projects between columns; project detail with updates, scope, UAT. |
| **Clients**   | List clients; add/edit/delete; log issues; draft client updates (AI). |
| **Grooming**  | Jira tickets (with oncall filter); link tickets to clients; request update. |
| **Oncall**    | Oncall (CS-*) summary, open tickets table, monthly report PDF. |
| **Workload**  | Team workload from Jira + pipeline; “Generate AI analysis” for performance trends. |
| **Docket**    | Do first, at risk, stale, go-live, issues, Jira blocked; brain dump + monthly report. |
| **Eisenhower**| 4 quadrants; drag tasks; add/edit/delete. |
| **Escalations** | Product escalations; draft product scope from Jira (AI). |
| **Scope & UAT** | Projects with scope/UAT; edit, download PDFs. |
| **Issues**    | Open issue log; add/edit/resolve/delete. |
| **Documents** | Repository of saved AI-generated documents; view/edit/PDF/delete. |
| **AI**        | Ad-hoc ask, client update drafter, meeting prep, digest, document from template. |

---

## API overview

- **Auth**: `POST /api/auth/login`, `GET /api/auth/me`
- **Projects**: CRUD, updates, generate scope/UAT, scope/UAT PDFs
- **Clients**: CRUD, report
- **Brief**: `GET /api/brief`
- **Jira**: grooming, pipeline-tickets, oncall, oncall summary, monthly report (JSON/PDF), client links, link-client, engineers, request-update, engineer-performance
- **Issues**: CRUD, resolve
- **AI**: clientupdate (project/client), meetingprep, monthly-report, ask, document (templates, generate), engineer-performance
- **Documents**: CRUD, PDF download
- **Escalations**: CRUD, product-scope (from tickets / by escalation)
- **Eisenhower**: CRUD
- **Workload**: `GET /api/team/workload`
- **Reflections**: GET, POST  
All API errors return JSON (e.g. `{"error": "..."}`); the frontend parses safely and can show `res.error`.

---

## Docs

- **SETUP.md** — Slack app creation, OAuth, Socket Mode, slash commands, env, run, test.
- **DEPLOY-RAILWAY.md** — Deploying web + worker to Railway.
- **.env.example** — All supported env vars with short comments.

---

## License

Use and modify as needed for your team.
