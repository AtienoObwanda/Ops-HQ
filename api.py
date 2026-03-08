"""
CS Dashboard — Flask API
Serves data from the same SQLite database the Slack bot uses.
Runs alongside bot.py on Railway.

Start: python api.py
Port:  5001 (bot uses nothing, so no conflict)
"""
import os
import json
import hashlib
import secrets
from collections import Counter
from datetime import datetime, timedelta
from functools import wraps

from io import BytesIO
from flask import Flask, request, jsonify, send_from_directory, send_file
from dotenv import load_dotenv

load_dotenv()

import database as db
import ai_client as ai
import jira_client as jira
import pdf_export as pdf_export

# frontend/ must sit next to api.py (repo root/frontend/index.html)
_frontend = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")
app = Flask(__name__, static_folder=_frontend, static_url_path="")

# ── AUTH ──────────────────────────────────────────────────────────────────────
# Simple token-based auth. No OAuth complexity.
# Tokens stored in memory — survive restarts via env var seeds.

SESSIONS = {}  # token -> {user, role, expires}

USERS = {
    os.environ.get("ADMIN_EMAIL", "atieno@credrails.com"): {
        "password": os.environ.get("ADMIN_PASSWORD", "changeme"),
        "role": "admin",
        "name": "Atieno",
    },
    os.environ.get("COO_EMAIL", "coo@credrails.com"): {
        "password": os.environ.get("COO_PASSWORD", "changeme2"),
        "role": "readonly",
        "name": "COO",
    },
}

# Engineers loaded from env: ENGINEER_EMAILS=name:email:pass,name2:email2:pass2
def _load_engineers():
    raw = os.environ.get("ENGINEER_EMAILS", "")
    for entry in raw.split(","):
        parts = entry.strip().split(":")
        if len(parts) == 3:
            name, email, password = parts
            USERS[email] = {"password": password, "role": "engineer", "name": name}

_load_engineers()


def _require_auth(roles=None):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            token = request.headers.get("Authorization", "").replace("Bearer ", "")
            session = SESSIONS.get(token)
            if not session:
                return jsonify({"error": "Unauthorized"}), 401
            if datetime.fromisoformat(session["expires"]) < datetime.utcnow():
                del SESSIONS[token]
                return jsonify({"error": "Session expired"}), 401
            if roles and session["role"] not in roles:
                return jsonify({"error": "Forbidden"}), 403
            request.user = session
            return f(*args, **kwargs)
        return wrapped
    return decorator


@app.route("/health")
def health():
    return "ok", 200


@app.route("/api/auth/login", methods=["POST"])
def login():
    data = request.json or {}
    email = data.get("email", "").lower().strip()
    password = data.get("password", "")
    user = USERS.get(email)
    if not user or user["password"] != password:
        return jsonify({"error": "Invalid credentials"}), 401
    token = secrets.token_hex(32)
    SESSIONS[token] = {
        "user": email,
        "name": user["name"],
        "role": user["role"],
        "expires": (datetime.utcnow() + timedelta(hours=12)).isoformat(),
    }
    return jsonify({"token": token, "name": user["name"], "role": user["role"]})


@app.route("/api/auth/me", methods=["GET"])
@_require_auth()
def me():
    return jsonify(request.user)


# ── PROJECTS ──────────────────────────────────────────────────────────────────

@app.route("/api/projects", methods=["GET"])
@_require_auth()
def get_projects():
    include_done = request.args.get("include_done") == "true"
    projects = db.all_projects(exclude_done=not include_done)
    return jsonify([dict(p) for p in projects])


@app.route("/api/projects", methods=["POST"])
@_require_auth(roles=["admin", "engineer"])
def create_project():
    data = request.json or {}
    client_id = data.get("client_id")
    client = data.get("client")
    if client_id and not client:
        c = db.get_client(client_id)
        client = c["name"] if c else None
    if not client:
        return jsonify({"error": "client or client_id required"}), 400
    pid = db.add_project(
        client=client,
        name=data.get("name", client),
        owner_slack=data.get("owner_slack"),
        owner_name=data.get("owner_name"),
        recon_slack=data.get("recon_slack"),
        recon_name=data.get("recon_name"),
        go_live=data.get("go_live"),
        stage=data.get("stage", "Discovery"),
        client_id=client_id,
    )
    return jsonify({"id": pid}), 201


@app.route("/api/projects/<int:pid>", methods=["GET"])
@_require_auth()
def get_project(pid):
    project = db.get_project(pid)
    if not project:
        return jsonify({"error": "Not found"}), 404
    updates = db.recent_updates(pid, limit=10)
    return jsonify({
        "project": dict(project),
        "updates": [dict(u) for u in updates],
    })


@app.route("/api/projects/<int:pid>", methods=["PATCH"])
@_require_auth(roles=["admin", "engineer"])
def update_project(pid):
    data = request.json or {}
    db.update_project(pid, **data)
    if data.get("notes") or data.get("stage") or data.get("health"):
        db.add_update(
            pid,
            f"Updated: {', '.join(f'{k}={v}' for k, v in data.items())}",
            author_name=request.user.get("name"),
        )
    return jsonify({"ok": True})


@app.route("/api/projects/<int:pid>/updates", methods=["POST"])
@_require_auth(roles=["admin", "engineer"])
def add_update(pid):
    data = request.json or {}
    content = data.get("content", "").strip()
    if not content:
        return jsonify({"error": "content required"}), 400
    db.add_update(pid, content, author_name=request.user.get("name"))
    return jsonify({"ok": True}), 201


@app.route("/api/projects/<int:pid>/generate-scope", methods=["POST"])
@_require_auth(roles=["admin", "engineer"])
def generate_project_scope(pid):
    """Generate delivery/project scope with AI from project + updates + issues; save to project."""
    project = db.get_project(pid)
    if not project:
        return jsonify({"error": "Project not found"}), 404
    project = dict(project)
    updates = db.recent_updates(pid, limit=10)
    issues = db.open_issues(limit=50)
    project_issues = [dict(i) for i in issues if i.get("project_id") == pid]
    try:
        scope = ai.generate_delivery_scope(project, [dict(u) for u in updates], project_issues)
        now = datetime.utcnow().isoformat()
        db.update_project(pid, project_scope_content=scope, project_scope_generated_at=now)
        return jsonify({"scope": scope, "generated_at": now})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/projects/<int:pid>/generate-uat-signoff", methods=["POST"])
@_require_auth(roles=["admin", "engineer"])
def generate_uat_signoff(pid):
    """Generate UAT signoff from project's delivery scope; save to project. Requires scope to exist."""
    project = db.get_project(pid)
    if not project:
        return jsonify({"error": "Project not found"}), 404
    project = dict(project)
    scope = (project.get("project_scope_content") or "").strip()
    if not scope:
        return jsonify({"error": "Generate or enter Delivery Scope first; UAT signoff is derived from it."}), 400
    try:
        uat = ai.generate_uat_signoff_from_scope(
            scope, project.get("name") or "Project", project.get("client") or "Client"
        )
        now = datetime.utcnow().isoformat()
        db.update_project(pid, uat_signoff_content=uat, uat_signoff_generated_at=now)
        return jsonify({"uat_signoff": uat, "generated_at": now})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/projects/<int:pid>/scope.pdf", methods=["GET"])
@_require_auth()
def download_scope_pdf(pid):
    """Download delivery scope as PDF (on request)."""
    project = db.get_project(pid)
    if not project:
        return jsonify({"error": "Not found"}), 404
    project = dict(project)
    content = (project.get("project_scope_content") or "").strip()
    if not content:
        return jsonify({"error": "No delivery scope. Generate or enter scope first."}), 404
    safe_name = "".join(c for c in (project.get("client") or "Project") if c.isalnum() or c in " -_")[:40]
    filename = f"delivery-scope-{safe_name}.pdf"
    try:
        pdf_bytes = pdf_export.delivery_scope_pdf(project, content)
        return send_file(
            BytesIO(pdf_bytes),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=filename,
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/projects/<int:pid>/uat-signoff.pdf", methods=["GET"])
@_require_auth()
def download_uat_signoff_pdf(pid):
    """Download UAT signoff as PDF (on request)."""
    project = db.get_project(pid)
    if not project:
        return jsonify({"error": "Not found"}), 404
    project = dict(project)
    content = (project.get("uat_signoff_content") or "").strip()
    if not content:
        return jsonify({"error": "No UAT signoff. Generate from scope first."}), 404
    safe_name = "".join(c for c in (project.get("client") or "Project") if c.isalnum() or c in " -_")[:40]
    filename = f"uat-signoff-{safe_name}.pdf"
    try:
        pdf_bytes = pdf_export.uat_signoff_pdf(project, content)
        return send_file(
            BytesIO(pdf_bytes),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=filename,
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── CLIENTS ───────────────────────────────────────────────────────────────────

@app.route("/api/clients", methods=["GET"])
@_require_auth()
def get_clients():
    clients = db.all_clients()
    return jsonify([dict(c) for c in clients])


@app.route("/api/clients", methods=["POST"])
@_require_auth(roles=["admin", "engineer"])
def create_client():
    data = request.json or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    try:
        cid = db.add_client(name=name, notes=(data.get("notes") or "").strip() or None)
        return jsonify({"id": cid}), 201
    except Exception as e:
        if "UNIQUE" in str(e):
            return jsonify({"error": "Client name already exists"}), 409
        raise


@app.route("/api/clients/<int:cid>", methods=["GET"])
@_require_auth()
def get_client(cid):
    client = db.get_client(cid)
    if not client:
        return jsonify({"error": "Not found"}), 404
    projects = db.get_projects_for_client(cid)
    issues = db.get_issues_for_client(cid)
    return jsonify({
        "client": dict(client),
        "projects": [dict(p) for p in projects],
        "issues": [dict(i) for i in issues],
    })


@app.route("/api/clients/<int:cid>", methods=["PATCH"])
@_require_auth(roles=["admin", "engineer"])
def update_client(cid):
    data = request.json or {}
    db.update_client(cid, **data)
    return jsonify({"ok": True})


@app.route("/api/clients/<int:cid>", methods=["DELETE"])
@_require_auth(roles=["admin", "engineer"])
def delete_client(cid):
    db.delete_client(cid)
    return jsonify({"ok": True})


@app.route("/api/clients/<int:cid>/report", methods=["GET"])
@_require_auth()
def client_report(cid):
    """Dynamic report for client: projects, issues, and ticket performance (for sales/product summaries)."""
    client = db.get_client(cid)
    if not client:
        return jsonify({"error": "Not found"}), 404
    projects = db.get_projects_for_client(cid)
    issues = db.get_issues_for_client(cid)
    open_issues = [i for i in issues if i.get("status") == "open"]
    resolved = [i for i in issues if i.get("resolved_at")]
    return jsonify({
        "client": dict(client),
        "projects": [dict(p) for p in projects],
        "issues": [dict(i) for i in issues],
        "open_count": len(open_issues),
        "resolved_count": len(resolved),
    })


# ── BRIEF ─────────────────────────────────────────────────────────────────────

def _brief_by_client(all_projects, at_risk, stale, open_issues_list, jira_tickets_per_client=None):
    """Build per-client summary for the brief (client lens). Includes issue log + Jira tickets linked via grooming."""
    clients = db.all_clients()
    at_risk_ids = {p["id"] for p in at_risk}
    stale_ids = {p["id"] for p in stale}
    issues_by_client_name = {}
    for i in open_issues_list:
        cname = (i.get("client") or "").strip() or "—"
        issues_by_client_name[cname] = issues_by_client_name.get(cname, 0) + 1
    jira_per_cid = jira_tickets_per_client or {}
    by_client = []
    for c in clients:
        cid, cname = c["id"], c["name"]
        projects_c = [p for p in all_projects if p.get("client_id") == cid or (p.get("client") or "").strip() == cname]
        active_c = [p for p in projects_c if p.get("stage") != "Done"]
        jira_linked = jira_per_cid.get(cid, 0)
        by_client.append({
            "client_id": cid,
            "client_name": cname,
            "project_count": len(active_c),
            "at_risk_count": sum(1 for p in active_c if p["id"] in at_risk_ids),
            "stale_count": sum(1 for p in active_c if p["id"] in stale_ids),
            "open_issues": issues_by_client_name.get(cname, 0),
            "jira_tickets": jira_linked,
        })
    return by_client


@app.route("/api/brief", methods=["GET"])
@_require_auth()
def get_brief():
    stale        = db.stale_projects(hours=24)
    at_risk      = db.at_risk_projects()
    all_projects = db.all_projects()  # active only (exclude_done=True)
    all_projects_with_done = db.all_projects(exclude_done=False)  # for Completed count + by_client
    jira_data    = jira.get_jira_brief_data()
    by_stage     = db.projects_by_stage()
    go_live_week = db.projects_go_live_this_week()
    go_live_overdue = db.projects_go_live_overdue()
    recently_completed = db.recently_completed_projects(days=14)
    open_issues_list = db.open_issues(limit=50)
    at_risk_list = [dict(p) for p in at_risk]
    stale_list = [dict(p) for p in stale]
    # Jira tickets linked to clients (from grooming dropdown) so "By client" reflects assigned items
    links = db.get_jira_ticket_client_links()
    jira_per_client = Counter(links.values())  # client_id -> count of linked tickets
    by_client = _brief_by_client(
        [dict(p) for p in all_projects_with_done],
        at_risk_list,
        stale_list,
        [dict(i) for i in open_issues_list],
        jira_tickets_per_client=dict(jira_per_client),
    )
    # Pending / do first: at risk + stale + overdue go-live + blocked Jira (combined priority list)
    overdue_ids = {p["id"] for p in go_live_overdue}
    pending = []
    for p in at_risk_list:
        pending.append({"type": "at_risk", "project": p, "label": f"At risk: {p.get('client')} — {p.get('stage')}"})
    for p in stale_list:
        if p["id"] not in {x["project"]["id"] for x in pending}:
            pending.append({"type": "stale", "project": p, "label": f"Stale: {p.get('client')} — {p.get('owner_name') or '—'}"})
    for p in go_live_overdue:
        p = dict(p)
        pending.append({"type": "overdue", "project": p, "label": f"Go-live overdue: {p.get('client')} (was {p.get('go_live')})"})
    if jira_data.get("configured"):
        for t in jira_data.get("blocked", [])[:5]:
            pending.append({"type": "jira_blocked", "ticket": t, "label": f"Jira blocked: {t.get('key')} — {t.get('summary', '')[:50]}"})

    return jsonify({
        "stale":       stale_list,
        "at_risk":     at_risk_list,
        "all_projects": [dict(p) for p in all_projects],
        "all_projects_with_done": [dict(p) for p in all_projects_with_done],
        "by_stage":    [dict(r) for r in by_stage],
        "by_client":   by_client,
        "go_live_this_week": [dict(p) for p in go_live_week],
        "go_live_overdue": [dict(p) for p in go_live_overdue],
        "recently_completed": [dict(p) for p in recently_completed],
        "open_issues": [dict(i) for i in open_issues_list],
        "pending_do_first": pending[:20],
        "jira":        {
            "configured": jira_data.get("configured", False),
            "stale":   jira_data.get("stale", []),
            "blocked": jira_data.get("blocked", []),
            "changes": jira_data.get("changes", []),
        },
        "generated_at": datetime.utcnow().isoformat(),
    })


# ── ISSUES ────────────────────────────────────────────────────────────────────

@app.route("/api/summary/monthly", methods=["GET"])
@_require_auth()
def get_monthly_summary():
    """End-of-month: what was shipped, resolved, issues logged, blockers, brain_dumps."""
    days = int(request.args.get("days", 30))
    data = db.monthly_summary(days=days)
    return jsonify(data)


@app.route("/api/reflections", methods=["GET"])
@_require_auth()
def get_reflections():
    """Brain dumps (EOD reflections) for the last N days — used in monthly reports and on request."""
    days = int(request.args.get("days", 30))
    rows = db.reflections_since(days=days)
    return jsonify([dict(r) for r in rows])


@app.route("/api/reflections", methods=["POST"])
@_require_auth(roles=["admin", "engineer"])
def create_reflection():
    """Log a brain dump (wins, blockers, lessons). Integrated into AI monthly reports."""
    data = request.json or {}
    rid = db.add_reflection(
        date=(data.get("date") or "").strip() or None,
        wins=(data.get("wins") or "").strip() or None,
        blockers=(data.get("blockers") or "").strip() or None,
        lessons=(data.get("lessons") or "").strip() or None,
    )
    return jsonify({"id": rid}), 201


@app.route("/api/jira/grooming", methods=["GET"])
@_require_auth()
def jira_grooming():
    """All open Jira tickets for ticket-grooming view. Optional ?client=Name filters to tickets whose summary contains the client name.
    Each ticket has oncall: true when key starts with CS- (oncall), else project."""
    tickets = jira.get_grooming_tickets()
    client_filter = (request.args.get("client") or "").strip()
    if client_filter:
        client_lower = client_filter.lower()
        tickets = [t for t in tickets if client_lower in (t.get("summary") or "").lower()]
    # Tag oncall (CS-*) vs project for grooming filter and badges
    for t in tickets:
        t["oncall"] = jira.is_oncall_ticket(t.get("key") or "")
    return jsonify({"configured": jira.is_configured(), "tickets": tickets})


@app.route("/api/jira/pipeline-tickets", methods=["GET"])
@_require_auth()
def jira_pipeline_tickets():
    """Jira project tickets (non-CS) with stage mapped from status, for populating pipeline board alongside DB projects."""
    tickets = jira.get_project_tickets_for_pipeline()
    return jsonify({"configured": jira.is_configured(), "tickets": tickets})


@app.route("/api/jira/oncall", methods=["GET"])
@_require_auth()
def jira_oncall():
    """Oncall tickets only (CS-*). For oncall one-pager."""
    tickets = jira.get_oncall_tickets()
    return jsonify({"configured": jira.is_configured(), "tickets": tickets})


@app.route("/api/jira/oncall/summary", methods=["GET"])
@_require_auth()
def jira_oncall_summary():
    """Summary/analysis for oncall one-pager: total open, by status, by assignee, unassigned, oldest."""
    if not jira.is_configured():
        return jsonify({"configured": False, "summary": None})
    summary = jira.get_oncall_summary()
    return jsonify({"configured": True, "summary": summary})


@app.route("/api/jira/oncall/monthly-report", methods=["GET"])
@_require_auth()
def jira_oncall_monthly_report():
    """Oncall tickets updated in the last N days (for monthly report). Query: days=30."""
    days = request.args.get("days", "30")
    try:
        days = int(days)
    except ValueError:
        days = 30
    days = max(1, min(365, days))
    if not jira.is_configured():
        return jsonify({"configured": False, "tickets": [], "period_label": f"Last {days} days"})
    tickets = jira.get_oncall_tickets_updated_since(days=days)
    summary = jira.get_oncall_summary()
    return jsonify({
        "configured": True,
        "tickets": tickets,
        "summary": summary,
        "period_label": f"Last {days} days",
    })


@app.route("/api/jira/oncall/monthly-report.pdf", methods=["GET"])
@_require_auth()
def jira_oncall_monthly_report_pdf():
    """Download oncall monthly report as PDF. Query: days=30."""
    days = request.args.get("days", "30")
    try:
        days = int(days)
    except ValueError:
        days = 30
    days = max(1, min(365, days))
    if not jira.is_configured():
        return jsonify({"error": "Jira not configured"}), 400
    tickets = jira.get_oncall_tickets_updated_since(days=days)
    summary = jira.get_oncall_summary()
    period_label = f"Last {days} days"
    pdf_bytes = pdf_export.oncall_monthly_report_pdf(period_label, summary, tickets)
    buf = BytesIO(pdf_bytes)
    return send_file(
        buf,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"oncall-report-{days}days.pdf",
    )


@app.route("/api/jira/client-links", methods=["GET"])
@_require_auth()
def jira_client_links():
    """Jira ticket key → client_id for grooming view client dropdown."""
    links = db.get_jira_ticket_client_links()
    return jsonify(links)


@app.route("/api/jira/link-client", methods=["POST"])
@_require_auth(roles=["admin", "engineer"])
def jira_link_client():
    """Link a Jira ticket to a client (or unlink if client_id is null). Body: ticket_key, client_id (int or null)."""
    data = request.json or {}
    ticket_key = (data.get("ticket_key") or "").strip()
    client_id = data.get("client_id")
    if not ticket_key:
        return jsonify({"error": "ticket_key required"}), 400
    if client_id is not None:
        try:
            client_id = int(client_id)
        except (TypeError, ValueError):
            return jsonify({"error": "client_id must be an integer or null"}), 400
        if db.get_client(client_id) is None:
            return jsonify({"error": "Client not found"}), 404
    db.set_jira_ticket_client(ticket_key, client_id)
    return jsonify({"ok": True})


@app.route("/api/jira/engineers", methods=["GET"])
@_require_auth()
def jira_engineers():
    """Jira display name → Slack mapping for request-update dropdown (names don't need to match)."""
    mapping = jira.get_engineer_mapping()
    return jsonify(mapping)


@app.route("/api/jira/request-update", methods=["POST"])
@_require_auth(roles=["admin", "engineer"])
def jira_request_update():
    """
    Send a Slack DM to the given user asking for an update on the Jira ticket.
    Jira and Slack names don't need to match — use JIRA_TO_SLACK mapping.
    """
    data = request.json or {}
    ticket_key = (data.get("ticket_key") or "").strip()
    ticket_summary = (data.get("ticket_summary") or "").strip()
    ticket_url = (data.get("ticket_url") or "").strip()
    slack_user_id = (data.get("slack_user_id") or "").strip()
    if not ticket_key or not slack_user_id:
        return jsonify({"error": "ticket_key and slack_user_id required"}), 400
    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        return jsonify({"error": "SLACK_BOT_TOKEN not set"}), 503
    try:
        import requests as req
        # Open DM with user (get channel ID)
        r = req.post(
            "https://slack.com/api/conversations.open",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"users": [slack_user_id]},
            timeout=10,
        )
        r.raise_for_status()
        body = r.json()
        if not body.get("ok"):
            return jsonify({"error": body.get("error", "conversations.open failed")}), 400
        channel_id = body.get("channel", {}).get("id")
        if not channel_id:
            return jsonify({"error": "No channel id"}), 400
        text = f"📋 *Update requested* for *{ticket_key}*: {ticket_summary or 'No summary'}\n"
        if ticket_url:
            text += f"<{ticket_url}|Open in Jira>\n"
        text += "\nPlease share a quick status update (reply here or use `/brief`)."
        msg = req.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"channel": channel_id, "text": text, "mrkdwn": True},
            timeout=10,
        )
        msg.raise_for_status()
        out = msg.json()
        if not out.get("ok"):
            return jsonify({"error": out.get("error", "chat.postMessage failed")}), 400
        return jsonify({"ok": True, "message": "DM sent"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/issues", methods=["GET"])
@_require_auth()
def get_issues():
    open_issues = db.open_issues(limit=50)
    patterns    = db.issue_patterns()
    return jsonify({
        "issues":   [dict(i) for i in open_issues],
        "patterns": [dict(r) for r in patterns],
    })


@app.route("/api/issues", methods=["POST"])
@_require_auth(roles=["admin", "engineer"])
def create_issue():
    data = request.json or {}
    iid = db.log_issue(
        title=data["title"],
        category=data["category"],
        description=data.get("description"),
        project_id=data.get("project_id"),
        client_id=data.get("client_id"),
        reported_by=request.user.get("name"),
    )
    return jsonify({"id": iid}), 201


@app.route("/api/issues/<int:iid>/resolve", methods=["POST"])
@_require_auth(roles=["admin"])
def resolve_issue(iid):
    db.resolve_issue(iid)
    return jsonify({"ok": True})


# ── AI ENDPOINTS ──────────────────────────────────────────────────────────────

@app.route("/api/ai/clientupdate/<int:pid>", methods=["POST"])
@_require_auth(roles=["admin"])
def ai_client_update(pid):
    """Generate status email draft for a single project (by project id)."""
    project = db.get_project(pid)
    if not project:
        return jsonify({"error": "Not found"}), 404
    try:
        recent_updates = db.recent_updates(pid, limit=5)
        open_issues    = db.open_issues(limit=50)
        project_issues = [dict(i) for i in open_issues if i.get("project_id") == pid]
        draft = ai.generate_client_update(
            dict(project),
            [dict(u) for u in recent_updates],
            project_issues,
        )
        return jsonify({"draft": draft})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ai/clientupdate/client/<int:cid>", methods=["POST"])
@_require_auth(roles=["admin"])
def ai_client_update_by_client(cid):
    """Generate status email draft for a client — aggregates their projects and issues. Select client, AI drafts; you edit top 2 lines and send."""
    client = db.get_client(cid)
    if not client:
        return jsonify({"error": "Not found"}), 404
    projects = db.get_projects_for_client(cid)
    issues = db.get_issues_for_client(cid)
    open_issues = [i for i in issues if i.get("status") == "open"]
    if not projects:
        return jsonify({"error": "No projects linked to this client"}), 400
    try:
        draft = ai.generate_client_update_for_client(
            dict(client),
            [dict(p) for p in projects],
            [dict(i) for i in open_issues],
        )
        return jsonify({"draft": draft})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ai/meetingprep", methods=["POST"])
@_require_auth(roles=["admin"])
def ai_meeting_prep():
    data         = request.json or {}
    meeting_type = data.get("type", "sales_sync")
    try:
        projects = db.all_projects()
        issues   = db.open_issues()
        prep     = ai.generate_meeting_prep(meeting_type, [dict(p) for p in projects], [dict(i) for i in issues])
        return jsonify({"prep": prep})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ai/monthly-report", methods=["POST"])
@_require_auth(roles=["admin"])
def ai_monthly_report():
    """Generate monthly report with brain dumps integrated into the AI prompt. On request or for end-of-month."""
    data = request.json or {}
    days = int(data.get("days", 30))
    monthly_data = db.monthly_summary(days=days)
    try:
        report = ai.generate_monthly_report(monthly_data)
        return jsonify({"report": report})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _cockpit_context():
    """Build a text snapshot of cockpit data for on-demand AI prompts."""
    projects = db.all_projects(exclude_done=False)
    clients = db.all_clients()
    open_issues = db.open_issues(limit=50)
    reflections = db.reflections_since(days=14)
    monthly = db.monthly_summary(days=30)
    at_risk = db.at_risk_projects()
    stale = db.stale_projects(hours=48)
    lines = []
    lines.append("PROJECTS (client, name, stage, health, owner):")
    for p in projects[:60]:
        lines.append(f"  - {p.get('client', '')} | {p.get('name', '')} | {p.get('stage', '')} | {p.get('health', '')} | {p.get('owner_name', '—')}")
    if not projects:
        lines.append("  (none)")
    lines.append("")
    lines.append("CLIENTS:")
    for c in clients:
        lines.append(f"  - {c.get('name', '')} (id {c.get('id')})")
    if not clients:
        lines.append("  (none)")
    lines.append("")
    lines.append("OPEN ISSUES (title, category, client/project):")
    for i in open_issues:
        lines.append(f"  - [{i.get('category', '')}] {i.get('title', '')} | {i.get('client', '—')}")
    if not open_issues:
        lines.append("  (none)")
    lines.append("")
    lines.append("BRAIN DUMPS / REFLECTIONS (last 14 days):")
    for r in reflections:
        lines.append(f"  - {r.get('date', '')}: wins={r.get('wins') or '—'} | blockers={r.get('blockers') or '—'} | lessons={r.get('lessons') or '—'}")
    if not reflections:
        lines.append("  (none)")
    lines.append("")
    lines.append("AT-RISK PROJECTS:")
    for p in at_risk:
        lines.append(f"  - {p.get('client', '')} | {p.get('health', '')} | {p.get('notes', '—')}")
    if not at_risk:
        lines.append("  (none)")
    lines.append("")
    lines.append("STALE (no update 48h+):")
    for p in stale[:15]:
        lines.append(f"  - {p.get('client', '')} | {p.get('stage', '')}")
    if not stale:
        lines.append("  (none)")
    lines.append("")
    lines.append("LAST 30 DAYS: shipped (Done) " + str(len(monthly.get("shipped", []))) + ", issues resolved " + str(len(monthly.get("resolved_issues", []))) + ", issues logged " + str(len(monthly.get("issues_logged", []))) + ", brain dumps " + str(len(monthly.get("brain_dumps", []))))
    if jira.is_configured():
        try:
            grooming = jira.get_grooming_tickets(max_results=20)
            lines.append("")
            lines.append("JIRA (recent open tickets):")
            for t in grooming[:20]:
                lines.append(f"  - {t.get('key', '')} | {t.get('status', '')} | {t.get('assignee', '—')} | {t.get('summary', '')[:60]}")
        except Exception:
            lines.append("")
            lines.append("JIRA: (error fetching)")
    return "\n".join(lines)


@app.route("/api/ai/ask", methods=["POST"])
@_require_auth()
def ai_ask():
    """On-demand prompt: ask anything about the cockpit. AI uses current projects, clients, issues, brain dumps, Jira, etc."""
    data = request.json or {}
    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"error": "prompt required"}), 400
    try:
        context = _cockpit_context()
        answer = ai.answer_cockpit_prompt(prompt, context)
        return jsonify({"answer": answer})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ai/document-templates", methods=["GET"])
@_require_auth()
def ai_document_templates():
    """List industry-standard document templates (id, name, description)."""
    out = [
        {"id": k, "name": v["name"], "description": v["description"]}
        for k, v in ai.DOCUMENT_TEMPLATES.items()
    ]
    return jsonify(out)


@app.route("/api/ai/document", methods=["POST"])
@_require_auth(roles=["admin", "engineer"])
def ai_document():
    """
    Generate a document from an industry-standard template. Purely AI.
    Body: template_id, jira_keys (optional list), context (optional string).
    If no tickets and no context, AI still produces a template-shaped draft.
    """
    data = request.json or {}
    template_id = (data.get("template_id") or "").strip()
    if not template_id or template_id not in ai.DOCUMENT_TEMPLATES:
        return jsonify({"error": "template_id required and must be a valid template"}), 400
    keys_raw = data.get("jira_keys") or []
    jira_keys = keys_raw if isinstance(keys_raw, list) else [k.strip() for k in str(keys_raw).replace(",", " ").split() if k.strip()]
    context = (data.get("context") or "").strip()
    client_name = None
    if data.get("client_id") is not None:
        c = db.get_client(data["client_id"])
        if c:
            client_name = c.get("name") or ""
    tickets_text = ""
    if jira_keys:
        tickets = jira.get_tickets_by_keys(jira_keys)
        tickets_text = jira.format_tickets_for_ai(tickets) if tickets else ""
    try:
        draft = ai.generate_document_from_template(template_id, tickets_text or None, context or None, client_name=client_name)
        return jsonify({"draft": draft, "template_id": template_id, "client_name": client_name})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/pdf", methods=["POST"])
@_require_auth()
def generate_pdf():
    """Generate a PDF from title + body (e.g. for AI document download). Body: title, body."""
    data = request.json or {}
    title = (data.get("title") or "Document").strip()
    body = (data.get("body") or "").strip()
    try:
        pdf_bytes = pdf_export.generic_pdf(title, body)
        return send_file(
            BytesIO(pdf_bytes),
            mimetype="application/pdf",
            as_attachment=True,
            download_name="document.pdf",
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── PRODUCT ESCALATIONS ───────────────────────────────────────────────────────

@app.route("/api/escalations", methods=["GET"])
@_require_auth()
def get_escalations():
    rows = db.list_product_escalations()
    return jsonify([dict(r) for r in rows])


@app.route("/api/escalations", methods=["POST"])
@_require_auth(roles=["admin", "engineer"])
def create_escalation():
    data = request.json or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    keys_raw = data.get("jira_keys")
    if isinstance(keys_raw, list):
        jira_keys = keys_raw
    elif isinstance(keys_raw, str):
        jira_keys = [k.strip() for k in keys_raw.replace(",", " ").split() if k.strip()]
    else:
        jira_keys = None
    eid = db.add_product_escalation(
        title=title,
        description=data.get("description"),
        jira_keys=jira_keys,
        future_notes=data.get("future_notes"),
        drive_links=data.get("drive_links"),
        drive_notes=data.get("drive_notes"),
        drafted_scope=data.get("drafted_scope"),
    )
    return jsonify({"id": eid}), 201


@app.route("/api/escalations/<int:eid>", methods=["GET"])
@_require_auth()
def get_escalation(eid):
    row = db.get_product_escalation(eid)
    if not row:
        return jsonify({"error": "Not found"}), 404
    return jsonify(dict(row))


@app.route("/api/escalations/<int:eid>", methods=["PATCH"])
@_require_auth(roles=["admin", "engineer"])
def update_escalation(eid):
    data = request.json or {}
    allowed = {"title", "description", "status", "future_notes", "drive_links", "drive_notes", "drafted_scope"}
    updates = {k: v for k, v in data.items() if k in allowed}
    if data.get("jira_keys") is not None:
        keys_raw = data["jira_keys"]
        if isinstance(keys_raw, list):
            updates["jira_keys"] = ",".join(k.strip() for k in keys_raw if k and str(k).strip())
        else:
            updates["jira_keys"] = (keys_raw or "").strip() or None
    if updates:
        db.update_product_escalation(eid, **updates)
    return jsonify({"ok": True})


@app.route("/api/escalations/<int:eid>", methods=["DELETE"])
@_require_auth(roles=["admin"])
def delete_escalation(eid):
    db.delete_product_escalation(eid)
    return jsonify({"ok": True})


@app.route("/api/ai/product-scope/from-tickets", methods=["POST"])
@_require_auth(roles=["admin", "engineer"])
def ai_product_scope_from_tickets():
    """
    Select tickets only: AI suggests title and drafts full product scope. No form needed.
    Body: { "jira_keys": ["NAO-831", "NAO-830"] }. Returns { "title", "draft", "jira_keys" }.
    """
    data = request.json or {}
    keys_raw = data.get("jira_keys") or []
    jira_keys = keys_raw if isinstance(keys_raw, list) else [k.strip() for k in str(keys_raw).replace(",", " ").split() if k.strip()]
    if not jira_keys:
        return jsonify({"error": "jira_keys required (list of ticket keys)"}), 400
    tickets = jira.get_tickets_by_keys(jira_keys)
    tickets_text = jira.format_tickets_for_ai(tickets) if tickets else "No ticket details found (check Jira config and keys)."
    try:
        raw = ai.generate_product_scope_from_tickets_only(tickets_text)
        title = ""
        draft = raw
        if raw.strip().upper().startswith("TITLE:"):
            first_line, _, rest = raw.strip().partition("\n")
            title = first_line[6:].strip()  # after "TITLE:"
            draft = rest.strip()
        return jsonify({"title": title or "Product escalation", "draft": draft, "jira_keys": jira_keys})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ai/product-scope", methods=["POST"])
@_require_auth(roles=["admin", "engineer"])
def ai_product_scope():
    """
    Draft a product scope from an escalation (by id) or from raw payload.
    Body: escalation_id (int) OR title, description, jira_keys[], future_notes, drive_links, drive_notes.
    Fetches Jira ticket details by keys and passes tickets + future + drive content to AI.
    """
    data = request.json or {}
    escalation_id = data.get("escalation_id")

    if escalation_id:
        esc = db.get_product_escalation(int(escalation_id))
        if not esc:
            return jsonify({"error": "Escalation not found"}), 404
        title = esc.get("title") or ""
        description = esc.get("description") or ""
        jira_keys_str = esc.get("jira_keys") or ""
        jira_keys = [k.strip() for k in jira_keys_str.replace(",", " ").split() if k.strip()]
        future_notes = esc.get("future_notes") or ""
        drive_links = esc.get("drive_links") or ""
        drive_notes = esc.get("drive_notes") or ""
    else:
        title = (data.get("title") or "").strip()
        if not title:
            return jsonify({"error": "title or escalation_id required"}), 400
        description = data.get("description") or ""
        keys_raw = data.get("jira_keys") or []
        jira_keys = keys_raw if isinstance(keys_raw, list) else [k.strip() for k in str(keys_raw).replace(",", " ").split() if k.strip()]
        future_notes = data.get("future_notes") or ""
        drive_links = data.get("drive_links") or ""
        drive_notes = data.get("drive_notes") or ""

    tickets = jira.get_tickets_by_keys(jira_keys) if jira_keys else []
    tickets_text = jira.format_tickets_for_ai(tickets) if tickets else "No tickets linked or Jira not configured."

    drive_content = drive_links.strip()
    if drive_notes.strip():
        drive_content = (drive_content + "\n\nPasted context:\n" + drive_notes.strip()) if drive_content else drive_notes.strip()

    try:
        scope = ai.generate_product_scope(title, description, tickets_text, future_notes, drive_content)
        out = {"draft": scope}
        if escalation_id:
            db.update_product_escalation(int(escalation_id), drafted_scope=scope)
            out["escalation_id"] = int(escalation_id)
        return jsonify(out)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ai/digest", methods=["POST"])
@_require_auth(roles=["admin"])
def ai_digest():
    try:
        patterns    = db.issue_patterns()
        reflections = db.reflections_this_week()
        stale       = db.stale_projects(hours=48)
        at_risk     = db.at_risk_projects()
        digest = ai.generate_pattern_digest(
            [dict(r) for r in patterns],
            [dict(r) for r in reflections],
            [dict(p) for p in stale],
            [dict(p) for p in at_risk],
        )
        return jsonify({"digest": digest})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── EISENHOWER (planning / prioritising) ───────────────────────────────────────

@app.route("/api/eisenhower", methods=["GET"])
@_require_auth()
def get_eisenhower():
    tasks = db.get_eisenhower_tasks()
    return jsonify([dict(t) for t in tasks])


@app.route("/api/eisenhower", methods=["POST"])
@_require_auth(roles=["admin", "engineer"])
def create_eisenhower_task():
    data = request.json or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    tid = db.add_eisenhower_task(
        title=title,
        quadrant=data.get("quadrant", "important_not_urgent"),
        assignee_name=data.get("assignee_name"),
        assignee_slack=data.get("assignee_slack"),
        jira_key=(data.get("jira_key") or "").strip() or None,
        due_date=(data.get("due_date") or "").strip() or None,
        notes=(data.get("notes") or "").strip() or None,
    )
    return jsonify({"id": tid}), 201


@app.route("/api/eisenhower/<int:tid>", methods=["PATCH"])
@_require_auth(roles=["admin", "engineer"])
def update_eisenhower_task(tid):
    data = request.json or {}
    db.update_eisenhower_task(tid, **data)
    return jsonify({"ok": True})


@app.route("/api/eisenhower/<int:tid>", methods=["DELETE"])
@_require_auth(roles=["admin", "engineer"])
def delete_eisenhower_task(tid):
    db.delete_eisenhower_task(tid)
    return jsonify({"ok": True})


# ── TEAM / WORKLOAD (DB projects + Jira grooming by assignee) ─────────────────

@app.route("/api/team/workload", methods=["GET"])
@_require_auth()
def team_workload():
    """Workload per engineer: DB projects (by owner) + Jira grooming tickets (by assignee). Populated from pipeline/grooming; engineers mapped via JIRA_TO_SLACK."""
    projects = db.all_projects()
    tickets = jira.get_grooming_tickets(max_results=150)
    engineers = {e["jira_name"]: e for e in jira.get_engineer_mapping()}

    workload = {}
    def ensure_member(name, slack_id=None):
        if not name:
            name = "Unassigned"
        if name not in workload:
            workload[name] = {
                "name": name,
                "slack": slack_id,
                "projects": [],
                "tickets": [],
                "at_risk": 0,
            }
        return workload[name]

    for p in projects:
        owner = (p.get("owner_name") or "").strip() or "Unassigned"
        m = ensure_member(owner, p.get("owner_slack"))
        m["projects"].append(dict(p))
        if p.get("health") in ("At Risk", "Blocked"):
            m["at_risk"] += 1

    for t in tickets:
        assignee = (t.get("assignee") or "").strip()
        if assignee and assignee.lower() in ("unassigned", "—", ""):
            assignee = "Unassigned"
        if not assignee:
            assignee = "Unassigned"
        m = ensure_member(assignee, engineers.get(assignee, {}).get("slack_id"))
        m["tickets"].append({
            "key": t.get("key"),
            "summary": t.get("summary"),
            "status": t.get("status"),
            "url": t.get("url"),
            "oncall": jira.is_oncall_ticket(t.get("key") or ""),
        })

    # Sort: by total work (projects + tickets) desc, then Unassigned last
    def order(m):
        total = len(m["projects"]) + len(m["tickets"])
        return (-total, (m["name"] == "Unassigned"))
    return jsonify(sorted(workload.values(), key=order))


# ── SERVE FRONTEND ────────────────────────────────────────────────────────────

@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_frontend(path):
    if path and os.path.exists(os.path.join(app.static_folder, path)):
        return send_from_directory(app.static_folder, path)
    return send_from_directory(app.static_folder, "index.html")


# ── MAIN ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    db.init_db()
    # Railway sets PORT; do not set PORT in Railway — it injects the correct one
    port = int(os.environ.get("PORT", 5001))
    print(f"🌐 CS Dashboard API running on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
