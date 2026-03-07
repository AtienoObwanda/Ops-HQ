"""
CS Bot - Message Formatter
All Slack Block Kit payloads live here. Keep bot.py clean.
"""
from datetime import datetime


HEALTH_EMOJI = {
    "On Track": "✅",
    "At Risk":  "⚠️",
    "Blocked":  "🔴",
}

STAGE_EMOJI = {
    "Discovery":   "🔍",
    "Config":      "⚙️",
    "Integration": "🔗",
    "UAT":         "🧪",
    "Go-Live":     "🚀",
    "Hypercare":   "🛡️",
    "Done":        "✅",
}


def _divider():
    return {"type": "divider"}


def _section(text):
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _header(text):
    return {"type": "header", "text": {"type": "plain_text", "text": text, "emoji": True}}


def _context(text):
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}


# ── MORNING BRIEF ─────────────────────────────────────────────────────────────

def morning_brief(stale, at_risk, all_projects):
    today = datetime.now().strftime("%A, %d %b %Y")
    blocks = [
        _header(f"☀️  CS Morning Brief — {today}"),
        _divider(),
        _section(f"*{len(all_projects)} active projects* · *{len(at_risk)} at risk* · *{len(stale)} stale*"),
        _divider(),
    ]

    # At risk section
    if at_risk:
        blocks.append(_section("*🔴 Needs Your Attention*"))
        for p in at_risk:
            emoji = HEALTH_EMOJI.get(p["health"], "❓")
            owner = f"<@{p['owner_slack']}>" if p["owner_slack"] else p["owner_name"] or "unassigned"
            notes = f"\n> {p['notes']}" if p["notes"] else ""
            blocks.append(_section(
                f"{emoji} *{p['client']}* — {p['stage']}\n"
                f"Owner: {owner} · Updated: {_time_ago(p['updated_at'])}{notes}"
            ))
        blocks.append(_divider())

    # Stale section
    if stale:
        blocks.append(_section("*⏰ No Update in 24h*"))
        stale_lines = []
        for p in stale:
            owner = f"<@{p['owner_slack']}>" if p["owner_slack"] else p["owner_name"] or "unassigned"
            stale_lines.append(f"• *{p['client']}* ({p['stage']}) — {owner} — last: {_time_ago(p['updated_at'])}")
        blocks.append(_section("\n".join(stale_lines)))
        blocks.append(_divider())

    # Pipeline snapshot
    if all_projects:
        blocks.append(_section("*📋 Full Pipeline*"))
        lines = []
        for p in all_projects:
            h_emoji = HEALTH_EMOJI.get(p["health"], "❓")
            s_emoji = STAGE_EMOJI.get(p["stage"], "📌")
            owner = f"<@{p['owner_slack']}>" if p["owner_slack"] else p["owner_name"] or "—"
            lines.append(f"{h_emoji} {s_emoji} *{p['client']}* · {p['stage']} · {owner}")
        blocks.append(_section("\n".join(lines)))

    blocks.append(_divider())
    blocks.append(_context("Reply with `/brief` anytime for a fresh view · `/report` for COO summary"))

    return blocks


# ── ENGINEER CHECK-IN DM ──────────────────────────────────────────────────────

def engineer_checkin_dm(engineer_name, projects):
    """DM sent to engineer at 4pm asking for updates."""
    blocks = [
        _header("📬 Daily Project Check-in"),
        _section(
            f"Hey {engineer_name} 👋 Quick end-of-day check-in on your projects. "
            f"Reply to each with a status update — takes 2 mins."
        ),
        _divider(),
    ]
    for p in projects:
        blocks.append(_section(
            f"*{p['client']}* — currently: *{p['stage']}* {STAGE_EMOJI.get(p['stage'], '')}\n"
            f"What's the status? Any blockers?"
        ))
        blocks.append({
            "type": "actions",
            "block_id": f"checkin_{p['id']}",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "✅ On Track"},
                    "style": "primary",
                    "value": f"{p['id']}|On Track",
                    "action_id": f"status_ontrack_{p['id']}"
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "⚠️ At Risk"},
                    "style": "danger",
                    "value": f"{p['id']}|At Risk",
                    "action_id": f"status_atrisk_{p['id']}"
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "🔴 Blocked"},
                    "style": "danger",
                    "value": f"{p['id']}|Blocked",
                    "action_id": f"status_blocked_{p['id']}"
                },
            ]
        })
        blocks.append(_divider())

    blocks.append(_context("Your updates go straight to Atieno's brief. No Slack thread needed."))
    return blocks


# ── PROJECT DETAIL ────────────────────────────────────────────────────────────

def project_detail(project, updates):
    h_emoji = HEALTH_EMOJI.get(project["health"], "❓")
    s_emoji = STAGE_EMOJI.get(project["stage"], "📌")
    owner = f"<@{project['owner_slack']}>" if project["owner_slack"] else project["owner_name"] or "Unassigned"
    go_live = project["go_live"] or "TBD"

    blocks = [
        _header(f"{project['client']}"),
        _section(
            f"{h_emoji} *{project['health']}*  ·  {s_emoji} *{project['stage']}*\n"
            f"Owner: {owner}  ·  Go-live: *{go_live}*\n"
            f"Last updated: {_time_ago(project['updated_at'])}"
        ),
    ]

    if project["notes"]:
        blocks.append(_section(f"> {project['notes']}"))

    if updates:
        blocks.append(_divider())
        blocks.append(_section("*Recent Updates*"))
        for u in updates[:3]:
            author = u["author_name"] or "Unknown"
            blocks.append(_section(f"• `{_short_time(u['created_at'])}` *{author}*: {u['content']}"))

    blocks.append(_divider())
    blocks.append(_context(
        f"ID: {project['id']} · "
        f"`/update {project['client']} <note>` · "
        f"`/risk {project['client']} <reason>` · "
        f"`/stage {project['client']} <stage>`"
    ))
    return blocks


# ── COO REPORT ────────────────────────────────────────────────────────────────

def coo_report(all_projects, at_risk, issues_by_cat):
    today = datetime.now().strftime("%d %b %Y")
    active = [p for p in all_projects if p["stage"] != "Done"]
    done = [p for p in all_projects if p["stage"] == "Done"]

    blocks = [
        _header(f"📊 CS Weekly Report — {today}"),
        _divider(),
        _section(
            f"*Summary*\n"
            f"• Active projects: *{len(active)}*\n"
            f"• At risk / blocked: *{len(at_risk)}*\n"
            f"• Completed this cycle: *{len(done)}*"
        ),
        _divider(),
    ]

    # Pipeline by stage
    stages = {}
    for p in active:
        stages.setdefault(p["stage"], []).append(p["client"])

    stage_lines = []
    for stage in ["Discovery", "Config", "Integration", "UAT", "Go-Live", "Hypercare"]:
        clients = stages.get(stage, [])
        if clients:
            stage_lines.append(f"{STAGE_EMOJI.get(stage, '📌')} *{stage}*: {', '.join(clients)}")

    if stage_lines:
        blocks.append(_section("*Pipeline*\n" + "\n".join(stage_lines)))
        blocks.append(_divider())

    # Risks
    if at_risk:
        risk_lines = []
        for p in at_risk:
            emoji = HEALTH_EMOJI[p["health"]]
            note = f" — {p['notes']}" if p["notes"] else ""
            risk_lines.append(f"{emoji} *{p['client']}* ({p['stage']}){note}")
        blocks.append(_section("*Risks & Blockers*\n" + "\n".join(risk_lines)))
        blocks.append(_divider())

    # Issue patterns
    if issues_by_cat:
        issue_lines = [f"• *{r['category']}*: {r['count']} total, {r['open_count']} open" for r in issues_by_cat]
        blocks.append(_section("*Recurring Issues*\n" + "\n".join(issue_lines)))
        blocks.append(_divider())

    blocks.append(_context("Generated by CS Bot · /report to regenerate"))
    return blocks


# ── HELP ──────────────────────────────────────────────────────────────────────

def help_message():
    return [
        _header("🤖 CS Bot — Command Reference"),
        _divider(),
        _section(
            "*Projects*\n"
            "`/project add \"Client\" \"Project name\"` — add new project\n"
            "`/project list` — all active projects\n"
            "`/project status \"Client\"` — project detail\n"
        ),
        _section(
            "*Updates*\n"
            "`/update \"Client\" <note>` — log a project update\n"
            "`/stage \"Client\" <stage>` — move to new stage\n"
            "`/risk \"Client\" <reason>` — flag as at risk\n"
            "`/resolve \"Client\"` — mark back to On Track\n"
        ),
        _section(
            "*Reporting*\n"
            "`/brief` — your morning brief on demand\n"
            "`/report` — COO-ready summary\n"
            "`/issues` — open issue log\n"
        ),
        _section(
            "*Issues*\n"
            "`/issue \"title\" <category>` — log a client issue\n"
            "Categories: `integration` `reconciliation` `client` `system` `process` `data`\n"
        ),
        _divider(),
        _context("Built for Atieno · CS Command Center"),
    ]


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _time_ago(iso_str):
    if not iso_str:
        return "never"
    try:
        dt = datetime.fromisoformat(iso_str)
        diff = datetime.utcnow() - dt
        hours = int(diff.total_seconds() / 3600)
        if hours < 1:
            return "just now"
        if hours < 24:
            return f"{hours}h ago"
        days = hours // 24
        return f"{days}d ago"
    except Exception:
        return iso_str


def _short_time(iso_str):
    try:
        return datetime.fromisoformat(iso_str).strftime("%d %b %H:%M")
    except Exception:
        return iso_str
