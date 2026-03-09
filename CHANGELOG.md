# Changelog

All notable changes to Ops Brain (Ops-HQ) are documented here. New entries are added at the top of each section; existing content is never removed.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

- Jira integration in morning brief (blocked/stale tickets, status changes)
- Weekly pattern digest (Friday 5pm) via AI client
- Morning brief and `/brief` accept optional `jira_data`; brain dumps (today + yesterday) and Jira section in same brief
- Optional `jira_client` and `ai_client` imports so bot runs if they are missing

---

## Added / changed (from existing commits)

### Rename & core features (Ops Brain)
- Renamed from CS Bot to Ops Brain (branding, messages, SETUP)
- Brain dump: `/braindump`, evening reminder Mon–Fri 6pm, today + yesterday in morning brief
- Week report (Saturday 9am) and month report (1st 9am) from brain dumps; `/weekreport`, `/monthreport`
- Recon/QA: `/askrecon` and Mon–Fri 10am DM with projects in Internal User Testing; `RECON_SLACK_IDS` env
- Project stages aligned to Jira board: Coming Soon → Requirement Gathering → Ticket Grooming → To Do → In Progress → Internal User Testing → Customer Testing → Done
- Morning brief can go to DM (`CS_BRIEF_SLACK_USER_ID`) or channel (`CS_COMMAND_CHANNEL`)

### Security & deploy
- `.gitignore` for `.env`, `*.db`; `.env.example` with placeholders only (no secrets in repo)
- Env check at startup: clear error if `SLACK_BOT_TOKEN` / `SLACK_SIGNING_SECRET` / `SLACK_APP_TOKEN` missing
- `load_dotenv()` for local `.env`; Railway uses dashboard Variables
- Deploy to Railway: Procfile, runtime.txt, DEPLOY-RAILWAY.md

### AI & tooling
- AI client for generating client updates and meeting prep (`/meetingprep`, draft client update)
- Jira client for brief data (stale, blocked, recent status changes)

### Initial implementation (already committed)
- Project structure and basic functionality (CS Bot)
- Slack Bolt + Socket Mode; slash commands: `/project`, `/update`, `/stage`, `/risk`, `/resolve`, `/brief`, `/report`, `/issue`, `/issues`, `/help`
- SQLite DB (projects, updates, issues, checkins)
- Morning brief 9am; engineer check-ins 4pm (DM with On Track / At Risk / Blocked buttons)
- Report and open-issues view

---

When you add new features or fixes, add a short line under `[Unreleased]` (or a new dated section) above this “Added / changed” block. Do not remove or rewrite the existing sections below.
