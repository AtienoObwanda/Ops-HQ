"""
CS Bot — Command Center for Atieno
Phase 2: Jira integration, /assign, /clientupdate, /meetingprep, weekly digest

Env vars required:
    SLACK_BOT_TOKEN         xoxb-...
    SLACK_APP_TOKEN         xapp-...
    SLACK_SIGNING_SECRET    ...
    CS_COMMAND_CHANNEL      C0123ABC
    JIRA_BASE_URL           https://yourcompany.atlassian.net
    JIRA_EMAIL              your@email.com
    JIRA_API_TOKEN          your-jira-api-token
    JIRA_PROJECT_KEYS       CS,IMPL
    ANTHROPIC_API_KEY       sk-ant-...
    CS_BOT_DB               cs_bot.db (optional)
"""
import os
import re
from datetime import datetime

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv

load_dotenv()

import database as db
import messages as msg
import jira_client as jira
import ai_client as ai

app = App(
    token=os.environ["SLACK_BOT_TOKEN"],
    signing_secret=os.environ["SLACK_SIGNING_SECRET"],
)

COMMAND_CHANNEL = os.environ.get("CS_COMMAND_CHANNEL", "")


def _post_blocks(channel, blocks, text="CS Bot"):
    app.client.chat_postMessage(channel=channel, text=text, blocks=blocks)


def _parse_quoted(text):
    match = re.match(r'"([^"]+)"\s*(.*)', text.strip())
    if match:
        return match.group(1).strip(), match.group(2).strip()
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
            respond("No active projects. Use `/project add \"Client\" \"Project name\"`")
            return
        lines = []
        for p in projects:
            h = msg.HEALTH_EMOJI.get(p["health"], "❓")
            s = msg.STAGE_EMOJI.get(p["stage"], "📌")
            owner = f"<@{p['owner_slack']}>" if p["owner_slack"] else p["owner_name"] or "—"
            lines.append(f"{h}{s} *{p['client']}* · {p['stage']} · {owner}")
        respond(text="Projects", blocks=[
            msg._header("📋 Active Projects"),
            msg._section("\n".join(lines)),
            msg._context(f"{len(projects)} projects · `/project status \"Client\"` for detail"),
        ])
        return

    if text.startswith("add"):
        rest = text[3:].strip()
        if not rest:
            respond("Usage: `/project add \"Client Name\" \"Project Name\"`")
            return
        client, remainder = _parse_quoted(rest)
        name = _parse_quoted(remainder)[0] if remainder.strip() else client

        owner_slack, owner_name = None, None
        mention = re.search(r"<@([A-Z0-9]+)(?:\|([^>]+))?>", rest)
        if mention:
            owner_slack = mention.group(1)
            owner_name  = mention.group(2)

        pid = db.add_project(client=client, name=name, owner_slack=owner_slack, owner_name=owner_name)
        respond(text="Project added", blocks=[
            msg._section(f"✅ *{client}* added to pipeline · Stage: Discovery · ID: `{pid}`"),
        ])
        return

    if text.startswith("status"):
        query = text[6:].strip().strip('"')
        project = db.find_project(query)
        if not project:
            respond(f"No project matching `{query}`")
            return
        updates = db.recent_updates(project["id"])
        respond(text=project["client"], blocks=msg.project_detail(project, updates))
        return

    respond("Try `/project list`, `/project add`, or `/project status`")


# ── /update ───────────────────────────────────────────────────────────────────

@app.command("/update")
def handle_update(ack, respond, command, body):
    ack()
    text = (command.get("text") or "").strip()
    query, note = _parse_quoted(text)
    if not note:
        respond("Usage: `/update \"Client Name\" your note here`")
        return
    project = db.find_project(query)
    if not project:
        respond(f"No project matching `{query}`")
        return
    db.add_update(project["id"], note, author_slack=body["user_id"], author_name=body.get("user_name"))
    respond(text="Update logged", blocks=[
        msg._section(f"✅ Update on *{project['client']}*\n> {note}"),
        msg._context(f"By <@{body['user_id']}> · {datetime.now().strftime('%H:%M')}"),
    ])


# ── /stage ────────────────────────────────────────────────────────────────────

@app.command("/stage")
def handle_stage(ack, respond, command, body):
    ack()
    text = (command.get("text") or "").strip()
    query, new_stage = _parse_quoted(text)
    if not new_stage:
        respond(f"Usage: `/stage \"Client\" <stage>`\nValid: {', '.join(db.VALID_STAGES)}")
        return
    matched = next((s for s in db.VALID_STAGES if s.lower() == new_stage.lower()), None) or \
              next((s for s in db.VALID_STAGES if new_stage.lower() in s.lower()), None)
    if not matched:
        respond(f"Unknown stage `{new_stage}`. Valid: {', '.join(db.VALID_STAGES)}")
        return
    project = db.find_project(query)
    if not project:
        respond(f"No project matching `{query}`")
        return
    old_stage = project["stage"]
    db.update_project(project["id"], stage=matched)
    db.add_update(project["id"], f"Stage: {old_stage} → {matched}", author_slack=body["user_id"], author_name=body.get("user_name"))
    respond(text="Stage updated", blocks=[
        msg._section(f"{msg.STAGE_EMOJI.get(matched, '📌')} *{project['client']}* → *{matched}*"),
    ])


# ── /risk ─────────────────────────────────────────────────────────────────────

@app.command("/risk")
def handle_risk(ack, respond, command, body):
    ack()
    text = (command.get("text") or "").strip()
    query, reason = _parse_quoted(text)
    project = db.find_project(query)
    if not project:
        respond(f"No project matching `{query}`")
        return
    db.update_project(project["id"], health="At Risk", notes=reason or "Flagged at risk")
    db.add_update(project["id"], f"⚠️ At risk: {reason}", author_slack=body["user_id"], author_name=body.get("user_name"))
    respond(text="Risk flagged", blocks=[
        msg._section(f"⚠️ *{project['client']}* flagged at risk\n> {reason or 'No reason given'}"),
        msg._context("Appears in morning brief. `/resolve` to clear."),
    ])


# ── /resolve ──────────────────────────────────────────────────────────────────

@app.command("/resolve")
def handle_resolve(ack, respond, command, body):
    ack()
    query = (command.get("text") or "").strip().strip('"')
    project = db.find_project(query)
    if not project:
        respond(f"No project matching `{query}`")
        return
    db.update_project(project["id"], health="On Track", notes="")
    db.add_update(project["id"], "✅ Back on track", author_slack=body["user_id"], author_name=body.get("user_name"))
    respond(text="Resolved", blocks=[msg._section(f"✅ *{project['client']}* is back on track")])


# ── /assign ───────────────────────────────────────────────────────────────────

@app.command("/assign")
def handle_assign(ack, respond, command, body):
    ack()
    text = (command.get("text") or "").strip()
    query, remainder = _parse_quoted(text)

    mention = re.search(r"<@([A-Z0-9]+)(?:\|([^>]+))?>", remainder)
    if not mention:
        respond("Usage: `/assign \"Client Name\" @engineer`")
        return

    new_owner_slack = mention.group(1)
    new_owner_name  = mention.group(2) or ""

    project = db.find_project(query)
    if not project:
        respond(f"No project matching `{query}`")
        return

    old_owner = project["owner_name"] or "unassigned"
    db.update_project(project["id"], owner_slack=new_owner_slack, owner_name=new_owner_name)
    db.add_update(
        project["id"],
        f"Reassigned: {old_owner} → {new_owner_name or new_owner_slack}",
        author_slack=body["user_id"],
        author_name=body.get("user_name")
    )
    respond(text="Assigned", blocks=[
        msg._section(f"👤 *{project['client']}* reassigned\n{old_owner} → <@{new_owner_slack}>"),
        msg._context("They'll get 4pm check-in DMs from now on."),
    ])


# ── /clientupdate ─────────────────────────────────────────────────────────────

@app.command("/clientupdate")
def handle_clientupdate(ack, respond, command, body):
    ack()
    query = (command.get("text") or "").strip().strip('"')
    if not query:
        respond("Usage: `/clientupdate \"Client Name\"`")
        return

    project = db.find_project(query)
    if not project:
        respond(f"No project matching `{query}`")
        return

    respond(text="Drafting...", blocks=[msg._section(f"✍️ Drafting update for *{project['client']}*...")])

    try:
        recent_updates = db.recent_updates(project["id"], limit=5)
        open_issues    = db.open_issues(limit=5)
        draft = ai.generate_client_update(
            dict(project),
            [dict(u) for u in recent_updates],
            [dict(i) for i in open_issues]
        )
        app.client.chat_postMessage(
            channel=body["channel_id"],
            text="Client Update Draft",
            blocks=[
                msg._header(f"📧 Client Update — {project['client']}"),
                msg._section("*Draft — review before sending:*"),
                msg._section(f"```{draft}```"),
                msg._divider(),
                msg._context("CS Bot · Edit as needed before sending"),
            ]
        )
    except Exception as e:
        app.client.chat_postMessage(channel=body["channel_id"], text=f"❌ Error: {e}")


# ── /meetingprep ──────────────────────────────────────────────────────────────

@app.command("/meetingprep")
def handle_meetingprep(ack, respond, command, body):
    ack()
    meeting_type = (command.get("text") or "").strip().lower().replace(" ", "_")
    valid = ["sales_sync", "product_eng", "client_call"]
    if meeting_type not in valid:
        respond("Usage: `/meetingprep <type>`\nTypes: `sales_sync` · `product_eng` · `client_call`")
        return

    respond(text="Prepping...", blocks=[msg._section(f"⏳ Generating talking points...")])

    try:
        projects = db.all_projects()
        issues   = db.open_issues()
        prep     = ai.generate_meeting_prep(meeting_type, [dict(p) for p in projects], [dict(i) for i in issues])
        label    = {"sales_sync": "Sales Sync", "product_eng": "Product & Eng", "client_call": "Client Call"}[meeting_type]
        app.client.chat_postMessage(
            channel=body["channel_id"],
            text="Meeting Prep",
            blocks=[
                msg._header(f"🎯 {label} — Talking Points"),
                msg._section(prep),
                msg._divider(),
                msg._context("CS Bot · Customize before your meeting"),
            ]
        )
    except Exception as e:
        app.client.chat_postMessage(channel=body["channel_id"], text=f"❌ Error: {e}")


# ── /jira (test connection) ────────────────────────────────────────────────────

@app.command("/jira")
def handle_jira(ack, respond):
    ack()
    if not jira:
        respond("Jira client not loaded (check `jira_client.py` is present).")
        return
    if not jira.is_configured():
        respond(
            "Jira *not configured*. Set in .env:\n"
            "`JIRA_BASE_URL` · `JIRA_EMAIL` · `JIRA_API_TOKEN` · `JIRA_PROJECT_KEYS`"
        )
        return
    try:
        data = jira.get_jira_brief_data()
        n_stale = len(data.get("stale", []))
        n_blocked = len(data.get("blocked", []))
        n_changes = len(data.get("changes", []))
        respond(
            f"✅ *Jira connected*\n"
            f"Brief data: {n_stale} stale · {n_blocked} blocked · {n_changes} recent status changes"
        )
    except Exception as e:
        respond(f"❌ Jira error: `{e}`")


# ── /brief ────────────────────────────────────────────────────────────────────

@app.command("/brief")
def handle_brief(ack, respond):
    ack()
    d = _get_morning_brief_data()
    respond(
        text="Morning Brief",
        blocks=msg.morning_brief(
            d["stale"], d["at_risk"], d["all_projects"], jira_data=d["jira_data"],
            all_with_done=d["all_with_done"], by_client=d["by_client"],
            go_live_this_week=d["go_live_this_week"], go_live_overdue=d["go_live_overdue"],
            recently_completed=d["recently_completed"], open_issues=d["open_issues"],
            pending_do_first=d["pending_do_first"],
        ),
    )


# ── /report ───────────────────────────────────────────────────────────────────

@app.command("/report")
def handle_report(ack, respond):
    ack()
    all_projects = db.all_projects(exclude_done=False)
    at_risk      = db.at_risk_projects()
    issues       = db.issue_patterns()
    respond(text="CS Report", blocks=msg.coo_report(all_projects, at_risk, issues))


# ── /issue & /issues ──────────────────────────────────────────────────────────

@app.command("/issue")
def handle_issue(ack, respond, command, body):
    ack()
    text     = (command.get("text") or "").strip()
    title, rest = _parse_quoted(text)
    parts    = rest.split()
    category = parts[0].lower() if parts else "client"
    if category not in db.ISSUE_CATEGORIES:
        respond(f"Unknown category `{category}`.\nValid: {', '.join(db.ISSUE_CATEGORIES)}")
        return
    project_id = None
    pm = re.search(r'for\s+"([^"]+)"', rest, re.IGNORECASE)
    if pm:
        p = db.find_project(pm.group(1))
        if p:
            project_id = p["id"]
    iid = db.log_issue(title, category, project_id=project_id, reported_by=body.get("user_name"))
    respond(text="Issue logged", blocks=[
        msg._section(f"🐛 *{title}* · `{category}` · ID `{iid}`"),
        msg._context("Patterns in `/report` · All open issues: `/issues`"),
    ])


@app.command("/issues")
def handle_issues(ack, respond):
    ack()
    open_issues = db.open_issues()
    patterns    = db.issue_patterns()
    if not open_issues:
        respond("No open issues 🎉")
        return
    lines         = [f"• `#{i['id']}` [{i['category']}] *{i['title']}*" + (f" · {i['client']}" if i["client"] else "") for i in open_issues]
    pattern_lines = [f"• *{r['category']}*: {r['count']} total, {r['open_count']} open" for r in patterns]
    respond(text="Issues", blocks=[
        msg._header("🐛 Open Issues"),
        msg._section("\n".join(lines)),
        msg._divider(),
        msg._section("*Patterns*\n" + "\n".join(pattern_lines)),
    ])


# ── /help ─────────────────────────────────────────────────────────────────────

@app.command("/help")
def handle_help(ack, respond):
    ack()
    respond(text="CS Bot Help", blocks=msg.help_message())


# ── BUTTON ACTIONS ────────────────────────────────────────────────────────────

@app.action(re.compile(r"status_(ontrack|atrisk|blocked)_\d+"))
def handle_checkin_button(ack, body, action, respond):
    ack()
    project_id, health = action["value"].split("|")
    project   = db.get_project(int(project_id))
    if not project:
        respond("Project not found.")
        return
    user_id   = body["user"]["id"]
    user_name = body["user"].get("name", "")
    db.update_project(int(project_id), health=health)
    db.add_update(int(project_id), f"Check-in: {health}", author_slack=user_id, author_name=user_name)
    emoji = msg.HEALTH_EMOJI.get(health, "✅")
    respond(replace_original=True, text="Update logged", blocks=[
        msg._section(f"{emoji} *{project['client']}* — *{health}* · Thanks {user_name} ✌️")
    ])


# ── SCHEDULED JOBS ────────────────────────────────────────────────────────────

def _get_morning_brief_data():
    """Shared data for morning brief (scheduled and /brief command)."""
    stale = db.stale_projects(hours=24)
    at_risk = db.at_risk_projects()
    all_projects = db.all_projects()
    all_with_done = db.all_projects(exclude_done=False)
    jira_data = jira.get_jira_brief_data()
    go_live_week = db.projects_go_live_this_week()
    go_live_overdue = db.projects_go_live_overdue()
    recently_completed = db.recently_completed_projects(days=14)
    open_issues = db.open_issues(limit=15)
    clients = db.all_clients()
    at_risk_ids = {p["id"] for p in at_risk}
    stale_ids = {p["id"] for p in stale}
    by_client = []
    for c in clients:
        projs = [p for p in all_with_done if p.get("client_id") == c["id"] or (p.get("client") or "").strip() == c["name"]]
        active = [p for p in projs if p.get("stage") != "Done"]
        issues_c = sum(1 for i in open_issues if (i.get("client") or "").strip() == c["name"])
        by_client.append({
            "client_name": c["name"],
            "project_count": len(active),
            "at_risk_count": sum(1 for p in active if p["id"] in at_risk_ids),
            "stale_count": sum(1 for p in active if p["id"] in stale_ids),
            "open_issues": issues_c,
        })
    pending = []
    for p in at_risk:
        pending.append({"type": "at_risk", "label": f"At risk: {p['client']} — {p['stage']}"})
    for p in stale:
        pending.append({"type": "stale", "label": f"Stale: {p['client']} — {p.get('owner_name') or '—'}"})
    for p in go_live_overdue:
        pending.append({"type": "overdue", "label": f"Go-live overdue: {p['client']} (was {p.get('go_live')})"})
    for t in (jira_data.get("blocked") or [])[:5]:
        pending.append({"type": "jira_blocked", "label": f"Jira blocked: {t.get('key')} — {(t.get('summary') or '')[:40]}"})
    return {
        "stale": [dict(p) for p in stale],
        "at_risk": [dict(p) for p in at_risk],
        "all_projects": [dict(p) for p in all_projects],
        "all_with_done": [dict(p) for p in all_with_done],
        "jira_data": jira_data,
        "by_client": by_client,
        "go_live_this_week": [dict(p) for p in go_live_week],
        "go_live_overdue": [dict(p) for p in go_live_overdue],
        "recently_completed": [dict(p) for p in recently_completed],
        "open_issues": [dict(i) for i in open_issues],
        "pending_do_first": pending[:15],
    }


def send_morning_brief():
    if not COMMAND_CHANNEL:
        return
    d = _get_morning_brief_data()
    blocks = msg.morning_brief(
        d["stale"], d["at_risk"], d["all_projects"], jira_data=d["jira_data"],
        all_with_done=d["all_with_done"], by_client=d["by_client"],
        go_live_this_week=d["go_live_this_week"], go_live_overdue=d["go_live_overdue"],
        recently_completed=d["recently_completed"], open_issues=d["open_issues"],
        pending_do_first=d["pending_do_first"],
    )
    _post_blocks(COMMAND_CHANNEL, blocks, text="☀️ CS Morning Brief")
    print(f"✅ Morning brief posted")


def send_engineer_checkins():
    projects = db.all_projects()
    if not projects:
        return
    by_owner = {}
    for p in projects:
        if p["owner_slack"]:
            by_owner.setdefault(p["owner_slack"], {"name": p["owner_name"] or "there", "projects": []})
            by_owner[p["owner_slack"]]["projects"].append(p)
    for slack_id, data in by_owner.items():
        try:
            blocks = msg.engineer_checkin_dm(data["name"], data["projects"])
            app.client.chat_postMessage(channel=slack_id, text="📬 Daily check-in", blocks=blocks)
            print(f"✅ Check-in DM → {slack_id}")
        except Exception as e:
            print(f"❌ DM failed for {slack_id}: {e}")


def send_weekly_digest():
    if not COMMAND_CHANNEL:
        return
    try:
        patterns    = db.issue_patterns()
        reflections = db.reflections_this_week()
        stale       = db.stale_projects(hours=48)
        at_risk     = db.at_risk_projects()
        digest      = ai.generate_pattern_digest(
            [dict(r) for r in patterns],
            [dict(r) for r in reflections],
            [dict(p) for p in stale],
            [dict(p) for p in at_risk],
        )
        _post_blocks(COMMAND_CHANNEL, [
            msg._header("📊 Weekly Pattern Digest"),
            msg._section(digest),
            msg._divider(),
            msg._context("Every Friday · CS Bot"),
        ], text="📊 Weekly Digest")
        print("✅ Weekly digest posted")
    except Exception as e:
        print(f"❌ Weekly digest failed: {e}")


# ── MAIN ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    db.init_db()

    scheduler = BackgroundScheduler()
    scheduler.add_job(send_morning_brief,     "cron", hour=9,  minute=0)
    scheduler.add_job(send_engineer_checkins, "cron", hour=16, minute=0)
    scheduler.add_job(send_weekly_digest,     "cron", day_of_week="fri", hour=17, minute=0)
    scheduler.start()
    print("⏰ Scheduler: briefs 9am · check-ins 4pm · digest Fridays 5pm")

    print("🤖 CS Bot Phase 2 starting...")
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    handler.start()
