"""
CS Bot — Command Center for Atieno
Slack Bolt + APScheduler

Run:
    python bot.py

Env vars required (put in .env):
    SLACK_BOT_TOKEN     xoxb-...
    SLACK_SIGNING_SECRET  ...
    CS_COMMAND_CHANNEL  #cs-command (channel ID, e.g. C0123ABC)
    CS_BOT_DB           cs_bot.db (optional, default)
"""
import os
import re
from datetime import datetime

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from apscheduler.schedulers.background import BackgroundScheduler

import database as db
import messages as msg

# ── APP INIT ──────────────────────────────────────────────────────────────────

app = App(
    token=os.environ["SLACK_BOT_TOKEN"],
    signing_secret=os.environ["SLACK_SIGNING_SECRET"],
)

COMMAND_CHANNEL = os.environ.get("CS_COMMAND_CHANNEL", "")  # Your private #cs-command channel ID


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _respond_blocks(respond, blocks, text="CS Bot"):
    respond(text=text, blocks=blocks)


def _post_blocks(channel, blocks, text="CS Bot"):
    app.client.chat_postMessage(channel=channel, text=text, blocks=blocks)


def _parse_quoted(text):
    """Extract first quoted string from text, return (quoted, remainder)."""
    match = re.match(r'"([^"]+)"\s*(.*)', text.strip())
    if match:
        return match.group(1).strip(), match.group(2).strip()
    # No quotes — treat whole thing as the first arg
    parts = text.strip().split(None, 1)
    return parts[0], parts[1] if len(parts) > 1 else ""


# ── /project ──────────────────────────────────────────────────────────────────

@app.command("/project")
def handle_project(ack, respond, command, body):
    ack()
    text = (command.get("text") or "").strip()

    if not text or text == "list":
        projects = db.all_projects()
        if not projects:
            respond("No active projects yet. Use `/project add \"Client\" \"Project name\"`")
            return
        lines = []
        for p in projects:
            h = msg.HEALTH_EMOJI.get(p["health"], "❓")
            s = msg.STAGE_EMOJI.get(p["stage"], "📌")
            owner = f"<@{p['owner_slack']}>" if p["owner_slack"] else p["owner_name"] or "—"
            lines.append(f"{h}{s} *{p['client']}* · {p['stage']} · {owner}")
        respond(text="Active Projects", blocks=[
            msg._header("📋 Active Projects"),
            msg._section("\n".join(lines)),
            msg._context(f"{len(projects)} projects · `/project status \"Client\"` for detail"),
        ])
        return

    if text.startswith("add"):
        rest = text[3:].strip()
        if not rest:
            respond("Usage: `/project add \"Client Name\" \"Project Name\"`\nExample: `/project add \"Equity Bank\" \"Recon Integration\"`")
            return
        client, name = _parse_quoted(rest)
        if not name:
            name = client  # fallback: use client as name too

        # Check for optional owner mention
        owner_slack = None
        owner_name = None
        mention = re.search(r"<@([A-Z0-9]+)(?:\|([^>]+))?>", rest)
        if mention:
            owner_slack = mention.group(1)
            owner_name = mention.group(2)

        pid = db.add_project(client=client, name=name, owner_slack=owner_slack, owner_name=owner_name)
        respond(text=f"Project added", blocks=[
            msg._section(f"✅ *{client}* added to pipeline\nStage: Discovery · ID: `{pid}`\n\nNext: `/stage \"{client}\" Config` when ready"),
        ])
        return

    if text.startswith("status"):
        query = text[6:].strip().strip('"')
        project = db.find_project(query)
        if not project:
            respond(f"Couldn't find a project matching `{query}`. Try `/project list`")
            return
        updates = db.recent_updates(project["id"])
        respond(text=project["client"], blocks=msg.project_detail(project, updates))
        return

    respond(f"Unknown subcommand `{text}`. Try `/project list`, `/project add`, `/project status`")


# ── /update ───────────────────────────────────────────────────────────────────

@app.command("/update")
def handle_update(ack, respond, command, body):
    ack()
    text = (command.get("text") or "").strip()
    if not text:
        respond("Usage: `/update \"Client Name\" your update note here`")
        return

    query, note = _parse_quoted(text)
    if not note:
        respond("Usage: `/update \"Client Name\" your update note here`\nExample: `/update \"Equity Bank\" UAT sign-off received, going live Friday`")
        return

    project = db.find_project(query)
    if not project:
        respond(f"No project found matching `{query}`. Check `/project list`")
        return

    user_id = body["user_id"]
    user_name = body.get("user_name", "")
    db.add_update(project["id"], note, author_slack=user_id, author_name=user_name)

    respond(text="Update logged", blocks=[
        msg._section(f"✅ Update logged on *{project['client']}*\n> {note}"),
        msg._context(f"By <@{user_id}> · {datetime.now().strftime('%H:%M')}"),
    ])


# ── /stage ────────────────────────────────────────────────────────────────────

@app.command("/stage")
def handle_stage(ack, respond, command, body):
    ack()
    text = (command.get("text") or "").strip()
    query, new_stage = _parse_quoted(text)

    if not new_stage:
        stages = " · ".join(db.VALID_STAGES)
        respond(f"Usage: `/stage \"Client\" <stage>`\nValid stages: {stages}")
        return

    # Fuzzy match stage
    matched = next((s for s in db.VALID_STAGES if s.lower() == new_stage.lower()), None)
    if not matched:
        matched = next((s for s in db.VALID_STAGES if new_stage.lower() in s.lower()), None)
    if not matched:
        respond(f"Unknown stage `{new_stage}`. Valid: {', '.join(db.VALID_STAGES)}")
        return

    project = db.find_project(query)
    if not project:
        respond(f"No project found matching `{query}`")
        return

    old_stage = project["stage"]
    db.update_project(project["id"], stage=matched)
    db.add_update(project["id"], f"Stage moved: {old_stage} → {matched}", author_slack=body["user_id"], author_name=body.get("user_name"))

    emoji = msg.STAGE_EMOJI.get(matched, "📌")
    respond(text="Stage updated", blocks=[
        msg._section(f"{emoji} *{project['client']}* moved to *{matched}*\n_{old_stage} → {matched}_"),
    ])


# ── /risk ─────────────────────────────────────────────────────────────────────

@app.command("/risk")
def handle_risk(ack, respond, command, body):
    ack()
    text = (command.get("text") or "").strip()
    query, reason = _parse_quoted(text)

    project = db.find_project(query)
    if not project:
        respond(f"No project found matching `{query}`")
        return

    db.update_project(project["id"], health="At Risk", notes=reason or "Flagged at risk")
    db.add_update(project["id"], f"⚠️ Flagged at risk: {reason}", author_slack=body["user_id"], author_name=body.get("user_name"))

    respond(text="Risk flagged", blocks=[
        msg._section(f"⚠️ *{project['client']}* flagged as *At Risk*\n> {reason or 'No reason provided'}"),
        msg._context("This will appear in tomorrow's morning brief. Use `/resolve` to clear."),
    ])


# ── /resolve ──────────────────────────────────────────────────────────────────

@app.command("/resolve")
def handle_resolve(ack, respond, command, body):
    ack()
    query = (command.get("text") or "").strip().strip('"')

    project = db.find_project(query)
    if not project:
        respond(f"No project found matching `{query}`")
        return

    db.update_project(project["id"], health="On Track", notes="")
    db.add_update(project["id"], "✅ Marked back on track", author_slack=body["user_id"], author_name=body.get("user_name"))

    respond(text="Resolved", blocks=[
        msg._section(f"✅ *{project['client']}* is back *On Track*"),
    ])


# ── /brief ────────────────────────────────────────────────────────────────────

@app.command("/brief")
def handle_brief(ack, respond, command):
    ack()
    stale = db.stale_projects(hours=24)
    at_risk = db.at_risk_projects()
    all_projects = db.all_projects()
    respond(text="Morning Brief", blocks=msg.morning_brief(stale, at_risk, all_projects))


# ── /report ───────────────────────────────────────────────────────────────────

@app.command("/report")
def handle_report(ack, respond, command):
    ack()
    all_projects = db.all_projects(exclude_done=False)
    at_risk = db.at_risk_projects()
    issues = db.issue_patterns()
    respond(text="CS Report", blocks=msg.coo_report(all_projects, at_risk, issues))


# ── /issue ────────────────────────────────────────────────────────────────────

@app.command("/issue")
def handle_issue(ack, respond, command, body):
    ack()
    text = (command.get("text") or "").strip()
    title, rest = _parse_quoted(text)

    # rest might be "category" or "category for Client"
    parts = rest.split()
    category = parts[0].lower() if parts else "client"

    if category not in db.ISSUE_CATEGORIES:
        respond(f"Unknown category `{category}`.\nValid: {', '.join(db.ISSUE_CATEGORIES)}")
        return

    # Optional: detect project mention
    project_id = None
    project_mention = re.search(r'for\s+"([^"]+)"', rest, re.IGNORECASE)
    if project_mention:
        p = db.find_project(project_mention.group(1))
        if p:
            project_id = p["id"]

    iid = db.log_issue(title, category, project_id=project_id, reported_by=body.get("user_name"))
    respond(text="Issue logged", blocks=[
        msg._section(f"🐛 Issue logged: *{title}*\nCategory: `{category}` · ID: `{iid}`"),
        msg._context("Patterns surface in `/report`. Use `/issues` to see open items."),
    ])


# ── /issues ───────────────────────────────────────────────────────────────────

@app.command("/issues")
def handle_issues(ack, respond, command):
    ack()
    open_issues = db.open_issues()
    patterns = db.issue_patterns()

    if not open_issues:
        respond("No open issues. 🎉")
        return

    lines = []
    for i in open_issues:
        client = f" · {i['client']}" if i["client"] else ""
        lines.append(f"• `#{i['id']}` [{i['category']}] *{i['title']}*{client}")

    pattern_lines = [f"• *{r['category']}*: {r['count']} total ({r['open_count']} open)" for r in patterns]

    respond(text="Open Issues", blocks=[
        msg._header("🐛 Open Issues"),
        msg._section("\n".join(lines)),
        msg._divider(),
        msg._section("*Patterns by Category*\n" + "\n".join(pattern_lines)),
        msg._context("Use `/issue \"title\" <category>` to log · Issues auto-surface in `/report`"),
    ])


# ── /help ─────────────────────────────────────────────────────────────────────

@app.command("/help")
def handle_help(ack, respond):
    ack()
    respond(text="CS Bot Help", blocks=msg.help_message())


# ── BUTTON ACTIONS (engineer check-in responses) ───────────────────────────────

@app.action(re.compile(r"status_(ontrack|atrisk|blocked)_\d+"))
def handle_checkin_button(ack, body, action, respond):
    ack()
    value = action["value"]  # "project_id|Health Status"
    project_id, health = value.split("|")
    project = db.get_project(int(project_id))

    if not project:
        respond("Project not found.")
        return

    user_id = body["user"]["id"]
    user_name = body["user"].get("name", "")

    db.update_project(int(project_id), health=health)
    db.add_update(int(project_id), f"Check-in: {health}", author_slack=user_id, author_name=user_name)

    emoji = msg.HEALTH_EMOJI.get(health, "✅")
    respond(
        replace_original=True,
        text=f"Update logged",
        blocks=[
            msg._section(f"{emoji} *{project['client']}* — logged as *{health}*\nThanks {user_name} ✌️"),
        ]
    )


# ── SCHEDULED JOBS ────────────────────────────────────────────────────────────

def send_morning_brief():
    """9am daily — post brief to #cs-command."""
    if not COMMAND_CHANNEL:
        print("⚠️  CS_COMMAND_CHANNEL not set — skipping morning brief")
        return
    stale = db.stale_projects(hours=24)
    at_risk = db.at_risk_projects()
    all_projects = db.all_projects()
    blocks = msg.morning_brief(stale, at_risk, all_projects)
    _post_blocks(COMMAND_CHANNEL, blocks, text="☀️ CS Morning Brief")
    print(f"✅ Morning brief posted to {COMMAND_CHANNEL}")


def send_engineer_checkins():
    """4pm daily — DM each engineer about their projects."""
    projects = db.all_projects()
    if not projects:
        return

    # Group by owner
    by_owner = {}
    for p in projects:
        if p["owner_slack"]:
            by_owner.setdefault(p["owner_slack"], {"name": p["owner_name"] or "there", "projects": []})
            by_owner[p["owner_slack"]]["projects"].append(p)

    for slack_id, data in by_owner.items():
        blocks = msg.engineer_checkin_dm(data["name"], data["projects"])
        try:
            app.client.chat_postMessage(
                channel=slack_id,  # DM by user ID
                text="📬 Daily check-in",
                blocks=blocks
            )
            print(f"✅ Check-in DM sent to {slack_id}")
        except Exception as e:
            print(f"❌ Failed to DM {slack_id}: {e}")


# ── MAIN ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    db.init_db()

    # Scheduler
    scheduler = BackgroundScheduler()
    scheduler.add_job(send_morning_brief, "cron", hour=9, minute=0)
    scheduler.add_job(send_engineer_checkins, "cron", hour=16, minute=0)
    scheduler.start()
    print("⏰ Scheduler started — briefs at 9am, check-ins at 4pm")

    # Start bot
    print("🤖 CS Bot starting...")
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    handler.start()
