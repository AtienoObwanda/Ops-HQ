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
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, request, jsonify, send_from_directory
from dotenv import load_dotenv

load_dotenv()

import database as db
import ai_client as ai
import jira_client as jira

app = Flask(__name__, static_folder="frontend", static_url_path="")

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
    pid = db.add_project(
        client=data["client"],
        name=data.get("name", data["client"]),
        owner_slack=data.get("owner_slack"),
        owner_name=data.get("owner_name"),
        go_live=data.get("go_live"),
        stage=data.get("stage", "Discovery"),
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


# ── BRIEF ─────────────────────────────────────────────────────────────────────

@app.route("/api/brief", methods=["GET"])
@_require_auth()
def get_brief():
    stale        = db.stale_projects(hours=24)
    at_risk      = db.at_risk_projects()
    all_projects = db.all_projects()
    jira_data    = jira.get_jira_brief_data()
    by_stage     = db.projects_by_stage()

    return jsonify({
        "stale":       [dict(p) for p in stale],
        "at_risk":     [dict(p) for p in at_risk],
        "all_projects": [dict(p) for p in all_projects],
        "by_stage":    [dict(r) for r in by_stage],
        "jira":        {
            "configured": jira_data.get("configured", False),
            "stale":   jira_data.get("stale", []),
            "blocked": jira_data.get("blocked", []),
            "changes": jira_data.get("changes", []),
        },
        "generated_at": datetime.utcnow().isoformat(),
    })


# ── ISSUES ────────────────────────────────────────────────────────────────────

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
        reported_by=request.user.get("name"),
    )
    return jsonify({"id": iid}), 201


@app.route("/api/issues/<int:iid>/resolve", methods=["POST"])
@_require_auth(roles=["admin"])
def resolve_issue(iid):
    db.resolve_issue(iid)
    return jsonify({"ok": True})


# ── GOALS ─────────────────────────────────────────────────────────────────────

@app.route("/api/goals", methods=["GET"])
@_require_auth()
def get_goals():
    with db.get_conn() as conn:
        goals = conn.execute("SELECT * FROM goals ORDER BY created_at DESC").fetchall()
    return jsonify([dict(g) for g in goals])


@app.route("/api/goals", methods=["POST"])
@_require_auth(roles=["admin"])
def create_goal():
    data = request.json or {}
    with db.get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO goals (title, owner_name, target_date, progress, status)
               VALUES (?, ?, ?, ?, 'active')""",
            (data["title"], data.get("owner_name"), data.get("target_date"), data.get("progress", 0))
        )
    return jsonify({"id": cur.lastrowid}), 201


@app.route("/api/goals/<int:gid>", methods=["PATCH"])
@_require_auth(roles=["admin", "engineer"])
def update_goal(gid):
    data = request.json or {}
    allowed = {"progress", "status", "notes"}
    updates = {k: v for k, v in data.items() if k in allowed}
    if updates:
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        with db.get_conn() as conn:
            conn.execute(f"UPDATE goals SET {set_clause} WHERE id = ?", list(updates.values()) + [gid])
    return jsonify({"ok": True})


# ── AI ENDPOINTS ──────────────────────────────────────────────────────────────

@app.route("/api/ai/clientupdate/<int:pid>", methods=["POST"])
@_require_auth(roles=["admin"])
def ai_client_update(pid):
    project = db.get_project(pid)
    if not project:
        return jsonify({"error": "Not found"}), 404
    try:
        recent_updates = db.recent_updates(pid, limit=5)
        open_issues    = db.open_issues(limit=5)
        draft = ai.generate_client_update(
            dict(project),
            [dict(u) for u in recent_updates],
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


# ── TEAM / WORKLOAD ───────────────────────────────────────────────────────────

@app.route("/api/team/workload", methods=["GET"])
@_require_auth()
def team_workload():
    projects = db.all_projects()
    workload = {}
    for p in projects:
        owner = p["owner_name"] or "Unassigned"
        if owner not in workload:
            workload[owner] = {"name": owner, "slack": p["owner_slack"], "projects": [], "at_risk": 0}
        workload[owner]["projects"].append(dict(p))
        if p["health"] in ("At Risk", "Blocked"):
            workload[owner]["at_risk"] += 1
    return jsonify(list(workload.values()))


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
    # Also ensure goals table exists
    with db.get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS goals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT NOT NULL,
                owner_name  TEXT,
                target_date TEXT,
                progress    INTEGER DEFAULT 0,
                status      TEXT DEFAULT 'active',
                notes       TEXT,
                created_at  TEXT DEFAULT (datetime('now'))
            )
        """)
    # Railway sets PORT; locally use DASHBOARD_PORT or 5001
    port = int(os.environ.get("PORT", os.environ.get("DASHBOARD_PORT", 5001)))
    print(f"🌐 CS Dashboard API running on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
