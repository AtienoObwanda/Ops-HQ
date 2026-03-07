"""
Ops Brain — Command Center for Atieno
Slack Bolt + APScheduler

Run:
    python bot.py

Env vars required (put in .env):
    SLACK_BOT_TOKEN       xoxb-...
    SLACK_SIGNING_SECRET  ...
    CS_COMMAND_CHANNEL    channel ID for brief (optional)
    CS_BRIEF_SLACK_USER_ID  your Slack user ID — if set, brief is DMed to you instead of a channel
    RECON_SLACK_IDS         comma-separated Slack user IDs for recon/QAs (get IUT status requests)
    CS_BOT_DB               cs_bot.db (optional, default)
"""
import os
import re
from datetime import datetime

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from apscheduler.schedulers.background import BackgroundScheduler

import database as db
import messages as msg

# ── ENV CHECK (fail fast with a clear message on Railway / production) ─────────
_REQUIRED = ["SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET", "SLACK_APP_TOKEN"]
_missing = [k for k in _REQUIRED if not os.environ.get(k)]
if _missing:
    print("Missing required env vars:", ", ".join(_missing))
    print("Set them in Railway → your service → Variables (or in .env locally).")
    raise SystemExit(1)

# ── APP INIT ──────────────────────────────────────────────────────────────────

app = App(
    token=os.environ["SLACK_BOT_TOKEN"],
    signing_secret=os.environ["SLACK_SIGNING_SECRET"],
)

COMMAND_CHANNEL = os.environ.get("CS_COMMAND_CHANNEL", "")  # Channel ID for morning brief (optional)
BRIEF_USER_ID = os.environ.get("CS_BRIEF_SLACK_USER_ID", "")  # If set, morning brief is sent to your DM instead
RECON_SLACK_IDS = [x.strip() for x in os.environ.get("RECON_SLACK_IDS", "").split(",") if x.strip()]


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _respond_blocks(respond, blocks, text="Ops Brain"):
    respond(text=text, blocks=blocks)


def _post_blocks(channel, blocks, text="Ops Brain"):
    app.client.chat_postMessage(channel=channel, text=text, blocks=blocks)


def _dm_channel_for_user(user_id):
    """Open or get DM channel with user; return channel ID for posting."""
    r = app.client.conversations_open(users=[user_id])
    return r["channel"]["id"]


def _brief_destination():
    """Return channel ID where morning brief should be sent (DM or channel)."""
    if BRIEF_USER_ID:
        return _dm_channel_for_user(BRIEF_USER_ID)
    return COMMAND_CHANNEL or None


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
            msg._section(f"✅ *{client}* added to pipeline\nStage: Coming Soon · ID: `{pid}`\n\nNext: `/stage \"{client}\" Requirement Gathering` when ready"),
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
    yesterday_dumps = db.brain_dumps_yesterday()
    today_dumps = db.brain_dumps_today()
    respond(text="Morning Brief", blocks=msg.morning_brief(stale, at_risk, all_projects, brain_dumps=yesterday_dumps, brain_dumps_today=today_dumps))


# ── /report ───────────────────────────────────────────────────────────────────

@app.command("/report")
def handle_report(ack, respond, command):
    ack()
    all_projects = db.all_projects(exclude_done=False)
    at_risk = db.at_risk_projects()
    issues = db.issue_patterns()
    respond(text="Ops Brain Report", blocks=msg.coo_report(all_projects, at_risk, issues))


@app.command("/weekreport")
def handle_weekreport(ack, respond, command):
    ack()
    dumps = db.brain_dumps_last_week()
    respond(text="Week in Review", blocks=msg.week_report(dumps))


@app.command("/monthreport")
def handle_monthreport(ack, respond, command):
    ack()
    dumps = db.brain_dumps_last_month()
    respond(text="Month in Review", blocks=msg.month_report(dumps))


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


# ── /askrecon ─────────────────────────────────────────────────────────────────

def _send_iut_status_to_recon():
    """DM all recon specialists with the list of projects in Internal User Testing."""
    iut = db.projects_in_stage("Internal User Testing")
    blocks = msg.recon_iut_status_request(iut)
    for slack_id in RECON_SLACK_IDS:
        try:
            channel = _dm_channel_for_user(slack_id)
            _post_blocks(channel, blocks, text="Internal User Testing — status request")
            print(f"✅ IUT status request sent to {slack_id}")
        except Exception as e:
            print(f"❌ Failed to DM recon {slack_id}: {e}")


@app.command("/askrecon")
def handle_askrecon(ack, respond, command, body):
    ack()
    try:
        if not RECON_SLACK_IDS:
            respond("No recon specialists configured. Set RECON_SLACK_IDS in .env (comma-separated Slack user IDs).")
            return
        _send_iut_status_to_recon()
        iut = db.projects_in_stage("Internal User Testing")
        respond(
            text="Recon requested",
            blocks=[msg._section(f"✅ Status request sent to {len(RECON_SLACK_IDS)} recon/QA(s) for *{len(iut)}* project(s) in Internal User Testing.")],
        )
    except Exception as e:
        respond(f"`/askrecon` failed: {e}")
        print(f"❌ /askrecon error: {e}")


# ── /help ─────────────────────────────────────────────────────────────────────

@app.command("/braindump")
def handle_braindump(ack, respond, command, body):
    ack()
    text = (command.get("text") or "").strip()
    if not text:
        respond("Usage: `/braindump your notes here` — I’ll save it and show it back in your morning brief.")
        return
    user_id = body["user_id"]
    user_name = body.get("user_name", "")
    db.add_brain_dump(user_id, text, author_name=user_name)
    respond(text="Brain dump saved", blocks=[
        msg._section("🧠 *Brain dump saved*\nI’ll surface this in your morning brief."),
        msg._context(f"At {datetime.now().strftime('%H:%M')}"),
    ])


@app.command("/help")
def handle_help(ack, respond):
    ack()
    respond(text="Ops Brain Help", blocks=msg.help_message())


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
    """9am daily — post brief to your DM (if CS_BRIEF_SLACK_USER_ID set) or to channel."""
    dest = _brief_destination()
    if not dest:
        print("⚠️  Set CS_BRIEF_SLACK_USER_ID (your Slack user ID) or CS_COMMAND_CHANNEL — skipping morning brief")
        return
    stale = db.stale_projects(hours=24)
    at_risk = db.at_risk_projects()
    all_projects = db.all_projects()
    yesterday_dumps = db.brain_dumps_yesterday()
    today_dumps = db.brain_dumps_today()
    blocks = msg.morning_brief(stale, at_risk, all_projects, brain_dumps=yesterday_dumps, brain_dumps_today=today_dumps)
    _post_blocks(dest, blocks, text="☀️ Ops Brain Morning Brief")
    print(f"✅ Morning brief posted to {'your DM' if BRIEF_USER_ID else dest}")


def send_evening_braindump_reminder():
    """6pm Mon–Fri — DM user to prompt evening brain dump."""
    if not BRIEF_USER_ID:
        return
    try:
        channel = _dm_channel_for_user(BRIEF_USER_ID)
        _post_blocks(
            channel,
            [msg._section("🌙 *Evening brain dump*\nHow did today go? Reply with:\n`/braindump <your notes>`\nI’ll save it and show it in your morning brief.")],
            text="Evening brain dump",
        )
        print("✅ Evening brain-dump reminder sent")
    except Exception as e:
        print(f"❌ Evening reminder failed: {e}")


def send_week_report():
    """Saturday 9am — DM user with week report from Mon–Fri brain dumps."""
    if not BRIEF_USER_ID:
        return
    try:
        channel = _dm_channel_for_user(BRIEF_USER_ID)
        dumps = db.brain_dumps_last_week()
        blocks = msg.week_report(dumps)
        _post_blocks(channel, blocks, text="Week in Review")
        print("✅ Week report sent")
    except Exception as e:
        print(f"❌ Week report failed: {e}")


def send_month_report():
    """1st of month 9am — DM user with previous month's brain dumps."""
    if not BRIEF_USER_ID:
        return
    try:
        channel = _dm_channel_for_user(BRIEF_USER_ID)
        dumps = db.brain_dumps_last_month()
        blocks = msg.month_report(dumps)
        _post_blocks(channel, blocks, text="Month in Review")
        print("✅ Month report sent")
    except Exception as e:
        print(f"❌ Month report failed: {e}")


def send_recon_iut_reminder():
    """10am Mon–Fri — DM recon specialists with IUT projects and ask for status."""
    if not RECON_SLACK_IDS:
        return
    _send_iut_status_to_recon()
    print("✅ Recon IUT reminder sent")


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
    scheduler.add_job(send_evening_braindump_reminder, "cron", hour=18, minute=0, day_of_week="mon-fri")
    scheduler.add_job(send_week_report, "cron", day_of_week="sat", hour=9, minute=0)
    scheduler.add_job(send_month_report, "cron", day=1, hour=9, minute=0)
    scheduler.add_job(send_recon_iut_reminder, "cron", hour=10, minute=0, day_of_week="mon-fri")
    scheduler.start()
    print("⏰ Scheduler started — briefs 9am, check-ins 4pm, brain-dump Mon–Fri 6pm, recon IUT Mon–Fri 10am, week Sat 9am, month 1st 9am")

    # Start bot
    print("🤖 Ops Brain starting...")
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    handler.start()
