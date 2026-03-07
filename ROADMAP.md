# Ops HQ / Leader-in-control roadmap

## In place

- **Delegation / assignment**
  - **Ticket Grooming** (nav): All Jira tickets in one view — who has what, status, assignee. Use for grooming and ownership.
  - **Pipeline**: Projects with owner (engineer) and **Recon specialist** per project. Add/edit project → set "Recon specialist".
- **Engineer status updates**
  - **Pipeline** → click a project → (today: toast; Phase 3: full detail panel with **Updates**). Backend already supports `POST /api/projects/<id>/updates` and check-ins. Engineers share status via project updates; weekly digest and brief pull from the same data.
- **End-of-month summary**
  - **My Docket** → "Monthly summary (last 30 days)": shipped (Done projects), issues resolved, issues logged, blockers from reflections.
- **Recon specialists**
  - Each project has optional **Recon specialist** (name) alongside owner (engineer). Shown on Pipeline and project list.
- **My Docket**
  - Single place: at-a-glance links to Brief + Pipeline, **Calendar** (placeholder), **Live account monitoring** (placeholder), and Monthly summary.
- **Placeholders**
  - **Calendar**: "Coming soon — your calendar in one view."
  - **Live account monitoring**: "Coming soon — catch issues on time for live accounts."

## Next (when you’re ready)

- **Project detail panel**: Full modal/page for a project (updates, Jira link, recon, edit) so engineers can post status updates from the dashboard (not only Slack).
- **Calendar**: Integrate calendar (e.g. Google Calendar) into My Docket.
- **Live account monitoring**: Alerts or checks for live accounts so you catch issues on time.
