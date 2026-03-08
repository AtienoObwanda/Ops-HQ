"""
Ops HQ - Database Layer
SQLite, zero infra. Just a file.
"""
import os
import sqlite3
from datetime import datetime, timedelta

# DB path: OPS_HQ_DB or CS_BOT_DB env; default cs_bot.db (on Railway use volume path when no env set)
_default_db = "cs_bot.db"
if os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") and not os.environ.get("OPS_HQ_DB") and not os.environ.get("CS_BOT_DB"):
    _default_db = os.path.join(os.environ["RAILWAY_VOLUME_MOUNT_PATH"], "cs_bot.db")
DB_PATH = os.environ.get("OPS_HQ_DB") or os.environ.get("CS_BOT_DB") or _default_db


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS projects (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            client      TEXT NOT NULL,
            stage       TEXT NOT NULL DEFAULT 'Discovery',
            health      TEXT NOT NULL DEFAULT 'On Track',
            owner_slack TEXT,          -- Slack user ID e.g. U0123ABC (engineer)
            owner_name  TEXT,
            recon_slack TEXT,          -- Recon/QA specialist Slack ID
            recon_name  TEXT,          -- Recon specialist display name
            go_live     TEXT,          -- ISO date string
            notes       TEXT,
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS updates (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id  INTEGER NOT NULL REFERENCES projects(id),
            author_slack TEXT,
            author_name  TEXT,
            content      TEXT NOT NULL,
            created_at   TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS issues (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id  INTEGER REFERENCES projects(id),
            category    TEXT NOT NULL,  -- integration | reconciliation | client | system | process | data
            title       TEXT NOT NULL,
            description TEXT,
            reported_by TEXT,
            status      TEXT NOT NULL DEFAULT 'open',  -- open | resolved
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            resolved_at TEXT
        );

        CREATE TABLE IF NOT EXISTS reflections (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            date      TEXT NOT NULL DEFAULT (date('now')),
            wins      TEXT,
            blockers  TEXT,
            lessons   TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS checkins (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            engineer_slack TEXT NOT NULL,
            engineer_name  TEXT,
            project_id     INTEGER REFERENCES projects(id),
            response       TEXT,
            sent_at        TEXT NOT NULL DEFAULT (datetime('now')),
            replied_at     TEXT
        );
        """)
        # Migration: add recon columns if missing (existing DBs)
        for col, typ in [("recon_slack", "TEXT"), ("recon_name", "TEXT")]:
            try:
                conn.execute(f"ALTER TABLE projects ADD COLUMN {col} {typ}")
            except sqlite3.OperationalError:
                pass
        # Migration: delivery scope & UAT signoff (for completed projects, on request)
        for col, typ in [
            ("project_scope_content", "TEXT"),
            ("project_scope_generated_at", "TEXT"),
            ("uat_signoff_content", "TEXT"),
            ("uat_signoff_generated_at", "TEXT"),
        ]:
            try:
                conn.execute(f"ALTER TABLE projects ADD COLUMN {col} {typ}")
            except sqlite3.OperationalError:
                pass
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS eisenhower_tasks (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                title         TEXT NOT NULL,
                quadrant      TEXT NOT NULL,
                assignee_name TEXT,
                assignee_slack TEXT,
                jira_key      TEXT,
                due_date      TEXT,
                notes         TEXT,
                created_at    TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS clients (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL UNIQUE,
                notes       TEXT,
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)
        try:
            conn.execute("ALTER TABLE projects ADD COLUMN client_id INTEGER REFERENCES clients(id)")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE issues ADD COLUMN client_id INTEGER REFERENCES clients(id)")
        except sqlite3.OperationalError:
            pass
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS jira_ticket_client (
                jira_key   TEXT PRIMARY KEY,
                client_id  INTEGER NOT NULL REFERENCES clients(id)
            );
        """)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS product_escalations (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                title         TEXT NOT NULL,
                description   TEXT,
                status        TEXT NOT NULL DEFAULT 'draft',
                jira_keys     TEXT,
                future_notes  TEXT,
                drive_links   TEXT,
                drive_notes   TEXT,
                drafted_scope TEXT,
                created_at    TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS documents (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT NOT NULL,
                client_id   INTEGER REFERENCES clients(id),
                template_id TEXT NOT NULL,
                content     TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS project_health_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id  INTEGER NOT NULL REFERENCES projects(id),
                health      TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)
    print(f"✅ DB initialised at {DB_PATH}")

# ── EISENHOWER (planning / prioritising) ───────────────────────────────────────

EISENHOWER_QUADRANTS = [
    "urgent_important",      # Do first
    "important_not_urgent",   # Schedule
    "urgent_not_important",  # Delegate
    "neither",               # Eliminate / later
]


def add_eisenhower_task(title, quadrant, assignee_name=None, assignee_slack=None, jira_key=None, due_date=None, notes=None):
    if quadrant not in EISENHOWER_QUADRANTS:
        quadrant = "important_not_urgent"
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO eisenhower_tasks (title, quadrant, assignee_name, assignee_slack, jira_key, due_date, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (title, quadrant, assignee_name, assignee_slack, jira_key, due_date, notes),
        )
        return cur.lastrowid


def get_eisenhower_tasks():
    with get_conn() as conn:
        return conn.execute(
            """SELECT * FROM eisenhower_tasks ORDER BY quadrant, created_at"""
        ).fetchall()


def update_eisenhower_task(task_id, **kwargs):
    allowed = {"title", "quadrant", "assignee_name", "assignee_slack", "jira_key", "due_date", "notes"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [task_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE eisenhower_tasks SET {set_clause} WHERE id = ?", values)


def delete_eisenhower_task(task_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM eisenhower_tasks WHERE id = ?", (task_id,))


# ── CLIENTS ───────────────────────────────────────────────────────────────────

def add_client(name, notes=None):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO clients (name, notes) VALUES (?, ?)",
            (name.strip(), (notes or "").strip() or None),
        )
        return cur.lastrowid


def get_client(client_id):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()


def all_clients():
    with get_conn() as conn:
        return conn.execute("SELECT * FROM clients ORDER BY name").fetchall()


def update_client(client_id, **kwargs):
    allowed = {"name", "notes"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return
    updates["updated_at"] = datetime.utcnow().isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [client_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE clients SET {set_clause} WHERE id = ?", values)


def delete_client(client_id):
    with get_conn() as conn:
        conn.execute("UPDATE projects SET client_id = NULL WHERE client_id = ?", (client_id,))
        conn.execute("UPDATE issues SET client_id = NULL WHERE client_id = ?", (client_id,))
        conn.execute("DELETE FROM jira_ticket_client WHERE client_id = ?", (client_id,))
        conn.execute("DELETE FROM clients WHERE id = ?", (client_id,))


def get_jira_ticket_client_links():
    """Return dict jira_key -> client_id for all linked tickets."""
    with get_conn() as conn:
        rows = conn.execute("SELECT jira_key, client_id FROM jira_ticket_client").fetchall()
        return {r["jira_key"]: r["client_id"] for r in rows}


def set_jira_ticket_client(jira_key, client_id):
    """Link a Jira ticket to a client. client_id must be valid. Replaces any existing link for this key."""
    jira_key = (jira_key or "").strip().upper()
    if not jira_key:
        return
    with get_conn() as conn:
        conn.execute("DELETE FROM jira_ticket_client WHERE jira_key = ?", (jira_key,))
        if client_id is not None:
            conn.execute("INSERT INTO jira_ticket_client (jira_key, client_id) VALUES (?, ?)", (jira_key, int(client_id)))


def get_projects_for_client(client_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM projects WHERE client_id = ? OR client = (SELECT name FROM clients WHERE id = ?) ORDER BY updated_at DESC",
            (client_id, client_id),
        ).fetchall()


def get_issues_for_client(client_id):
    """Issues linked to client directly or via projects."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT i.* FROM issues i
               LEFT JOIN projects p ON i.project_id = p.id
               WHERE i.client_id = ? OR p.client_id = ? OR p.client = (SELECT name FROM clients WHERE id = ?)
               ORDER BY i.created_at DESC""",
            (client_id, client_id, client_id),
        ).fetchall()


# ── PROJECTS ──────────────────────────────────────────────────────────────────

VALID_STAGES = [
    "Discovery", "Config", "Integration", "UAT", "Go-Live", "Hypercare", "Done"
]
VALID_HEALTH = ["On Track", "At Risk", "Blocked"]


def add_project(client, name, owner_slack=None, owner_name=None, recon_slack=None, recon_name=None, go_live=None, stage="Discovery", client_id=None):
    if client_id and not client:
        c = get_client(client_id)
        if c:
            client = c["name"]
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO projects (client, name, owner_slack, owner_name, recon_slack, recon_name, go_live, stage, client_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (client or "Unnamed", name, owner_slack, owner_name, recon_slack, recon_name, go_live, stage, client_id)
        )
        return cur.lastrowid


def get_project(project_id):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()


def find_project(query):
    """Fuzzy find by client or name."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT * FROM projects
               WHERE lower(client) LIKE ? OR lower(name) LIKE ?
               ORDER BY updated_at DESC LIMIT 1""",
            (f"%{query.lower()}%", f"%{query.lower()}%")
        ).fetchone()


def all_projects(exclude_done=True):
    with get_conn() as conn:
        q = "SELECT * FROM projects"
        if exclude_done:
            q += " WHERE stage != 'Done'"
        q += " ORDER BY updated_at DESC"
        return conn.execute(q).fetchall()


def stale_projects(hours=24):
    """Projects with no update in N hours."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT * FROM projects
               WHERE stage != 'Done'
               AND (
                   julianday('now') - julianday(updated_at)
               ) * 24 > ?
               ORDER BY updated_at ASC""",
            (hours,)
        ).fetchall()


def at_risk_projects():
    with get_conn() as conn:
        return conn.execute(
            """SELECT * FROM projects
               WHERE health IN ('At Risk', 'Blocked') AND stage != 'Done'
               ORDER BY health DESC, updated_at ASC"""
        ).fetchall()


def projects_go_live_this_week():
    """Projects with go_live date in the next 7 days (including today)."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT * FROM projects
               WHERE go_live IS NOT NULL AND go_live != ''
               AND date(go_live) BETWEEN date('now') AND date('now', '+7 days')
               AND stage != 'Done'
               ORDER BY go_live ASC"""
        ).fetchall()


def projects_go_live_overdue():
    """Projects with go_live in the past and not yet Done."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT * FROM projects
               WHERE go_live IS NOT NULL AND go_live != ''
               AND date(go_live) < date('now')
               AND stage != 'Done'
               ORDER BY go_live ASC"""
        ).fetchall()


def recently_completed_projects(days=14):
    """Projects moved to Done in the last N days (for handoff / UAT)."""
    with get_conn() as conn:
        since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        return conn.execute(
            """SELECT * FROM projects
               WHERE stage = 'Done' AND date(updated_at) >= ?
               ORDER BY updated_at DESC""",
            (since,),
        ).fetchall()


def update_project(project_id, **kwargs):
    allowed = {
        "stage", "health", "notes", "owner_slack", "owner_name", "recon_slack", "recon_name",
        "go_live", "name", "client", "client_id",
        "project_scope_content", "project_scope_generated_at", "uat_signoff_content", "uat_signoff_generated_at",
    }
    # Allow empty string for scope/uat content so they can be cleared
    updates = {
        k: v for k, v in kwargs.items()
        if k in allowed and (v is not None or k in ("project_scope_content", "uat_signoff_content"))
    }
    if not updates:
        return
    new_health = updates.get("health")
    updates["updated_at"] = datetime.utcnow().isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [project_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE projects SET {set_clause} WHERE id = ?", values)
    if new_health in ("At Risk", "Blocked"):
        record_project_health_change(project_id, new_health)


def record_project_health_change(project_id, health):
    """Record that a project was flagged At Risk or Blocked (for risk scoring history)."""
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO project_health_history (project_id, health) VALUES (?, ?)",
            (project_id, health),
        )


def get_project_at_risk_counts():
    """Return dict project_id -> count of times flagged At Risk or Blocked (for risk scoring)."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT project_id, COUNT(*) as cnt FROM project_health_history
               WHERE health IN ('At Risk', 'Blocked') GROUP BY project_id"""
        ).fetchall()
    return {r["project_id"]: r["cnt"] for r in rows}


def delete_project(project_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM updates WHERE project_id = ?", (project_id,))
        conn.execute("UPDATE issues SET project_id = NULL WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM checkins WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))


# ── UPDATES ───────────────────────────────────────────────────────────────────

def add_update(project_id, content, author_slack=None, author_name=None):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO updates (project_id, content, author_slack, author_name)
               VALUES (?, ?, ?, ?)""",
            (project_id, content, author_slack, author_name)
        )
    # Touch the project's updated_at
    update_project(project_id, notes=None)
    with get_conn() as conn:
        conn.execute(
            "UPDATE projects SET updated_at = datetime('now') WHERE id = ?",
            (project_id,)
        )


def recent_updates(project_id, limit=5):
    with get_conn() as conn:
        return conn.execute(
            """SELECT * FROM updates WHERE project_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (project_id, limit)
        ).fetchall()


# ── ISSUES ────────────────────────────────────────────────────────────────────

ISSUE_CATEGORIES = [
    "integration", "reconciliation", "client", "system", "process", "data"
]


def log_issue(title, category, description=None, project_id=None, client_id=None, reported_by=None):
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO issues (title, category, description, project_id, client_id, reported_by)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (title, category, description, project_id, client_id, reported_by)
        )
        return cur.lastrowid


def issue_patterns():
    """Return issue counts grouped by category."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT category, COUNT(*) as count,
               SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) as open_count
               FROM issues GROUP BY category ORDER BY count DESC"""
        ).fetchall()


def open_issues(limit=10):
    with get_conn() as conn:
        return conn.execute(
            """SELECT i.*, COALESCE(c.name, p.client) as client
               FROM issues i
               LEFT JOIN projects p ON i.project_id = p.id
               LEFT JOIN clients c ON i.client_id = c.id
               WHERE i.status = 'open'
               ORDER BY i.created_at DESC LIMIT ?""",
            (limit,)
        ).fetchall()


def resolve_issue(issue_id):
    with get_conn() as conn:
        conn.execute(
            """UPDATE issues SET status='resolved', resolved_at=datetime('now')
               WHERE id=?""",
            (issue_id,)
        )


def update_issue(issue_id, **kwargs):
    allowed = {"title", "category", "description", "project_id", "client_id"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [issue_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE issues SET {set_clause} WHERE id = ?", values)


def delete_issue(issue_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM issues WHERE id = ?", (issue_id,))


def get_issue(issue_id):
    with get_conn() as conn:
        row = conn.execute(
            """SELECT i.*, COALESCE(c.name, p.client) as client
               FROM issues i
               LEFT JOIN projects p ON i.project_id = p.id
               LEFT JOIN clients c ON i.client_id = c.id
               WHERE i.id = ?""",
            (issue_id,),
        ).fetchone()
        return dict(row) if row else None


# ── CHECK-INS ─────────────────────────────────────────────────────────────────

def log_checkin(engineer_slack, engineer_name, project_id):
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO checkins (engineer_slack, engineer_name, project_id)
               VALUES (?, ?, ?)""",
            (engineer_slack, engineer_name, project_id)
        )
        return cur.lastrowid


def record_checkin_reply(checkin_id, response):
    with get_conn() as conn:
        conn.execute(
            """UPDATE checkins SET response=?, replied_at=datetime('now')
               WHERE id=?""",
            (response, checkin_id)
        )


# ── PRODUCT ESCALATIONS (escalation to product, draft scopes from tickets/future/drive) ─

def add_product_escalation(title, description=None, jira_keys=None, future_notes=None, drive_links=None, drive_notes=None, drafted_scope=None):
    keys_str = ",".join([k.strip() for k in (jira_keys or []) if k and k.strip()]) if isinstance(jira_keys, list) else (jira_keys or "")
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO product_escalations (title, description, jira_keys, future_notes, drive_links, drive_notes, drafted_scope)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                (title or "").strip(),
                (description or "").strip() or None,
                keys_str or None,
                (future_notes or "").strip() or None,
                (drive_links or "").strip() or None,
                (drive_notes or "").strip() or None,
                (drafted_scope or "").strip() or None,
            ),
        )
        return cur.lastrowid


def get_product_escalation(eid):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM product_escalations WHERE id = ?", (eid,)).fetchone()


def list_product_escalations():
    with get_conn() as conn:
        return conn.execute(
            """SELECT * FROM product_escalations ORDER BY created_at DESC"""
        ).fetchall()


def update_product_escalation(eid, **kwargs):
    allowed = {"title", "description", "status", "jira_keys", "future_notes", "drive_links", "drive_notes", "drafted_scope"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return
    updates["updated_at"] = datetime.utcnow().isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [eid]
    with get_conn() as conn:
        conn.execute(f"UPDATE product_escalations SET {set_clause} WHERE id = ?", values)


def delete_product_escalation(eid):
    with get_conn() as conn:
        conn.execute("DELETE FROM product_escalations WHERE id = ?", (eid,))


# ── DOCUMENTS REPOSITORY (AI-generated docs: PRD, tech spec, meeting notes, etc.) ─

def add_document(title, template_id, content, client_id=None):
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO documents (title, client_id, template_id, content)
               VALUES (?, ?, ?, ?)""",
            (title.strip(), client_id, (template_id or "").strip(), (content or "").strip()),
        )
        return cur.lastrowid


def list_documents(client_id=None, template_id=None, limit=100):
    with get_conn() as conn:
        q = """SELECT d.*, c.name as client_name FROM documents d
               LEFT JOIN clients c ON d.client_id = c.id WHERE 1=1"""
        params = []
        if client_id is not None:
            q += " AND d.client_id = ?"
            params.append(client_id)
        if template_id:
            q += " AND d.template_id = ?"
            params.append(template_id)
        q += " ORDER BY d.updated_at DESC LIMIT ?"
        params.append(limit)
        return conn.execute(q, params).fetchall()


def get_document(doc_id):
    with get_conn() as conn:
        row = conn.execute(
            """SELECT d.*, c.name as client_name FROM documents d
               LEFT JOIN clients c ON d.client_id = c.id WHERE d.id = ?""",
            (doc_id,),
        ).fetchone()
        return row


def update_document(doc_id, **kwargs):
    allowed = {"title", "content", "client_id", "template_id"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return
    updates["updated_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    with get_conn() as conn:
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(f"UPDATE documents SET {set_clause} WHERE id = ?", list(updates.values()) + [doc_id])


def delete_document(doc_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))


# ── REPORTING ─────────────────────────────────────────────────────────────────

def projects_by_stage():
    with get_conn() as conn:
        return conn.execute(
            """SELECT stage, COUNT(*) as count FROM projects
               WHERE stage != 'Done' GROUP BY stage"""
        ).fetchall()


if __name__ == "__main__":
    init_db()


def reflections_this_week():
    """Pull EOD reflections from the last 7 days for the weekly digest."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT * FROM reflections
               WHERE julianday('now') - julianday(date) <= 7
               ORDER BY date DESC"""
        ).fetchall()


def reflections_since(days=30):
    """Brain dumps (reflections) in the last N days — for monthly reports and on-request reports."""
    with get_conn() as conn:
        since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        return conn.execute(
            """SELECT * FROM reflections WHERE date >= ? ORDER BY date DESC""",
            (since,),
        ).fetchall()


def add_reflection(date=None, wins=None, blockers=None, lessons=None):
    """Log a brain dump (EOD reflection). date = YYYY-MM-DD or today."""
    when = date or datetime.utcnow().strftime("%Y-%m-%d")
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO reflections (date, wins, blockers, lessons) VALUES (?, ?, ?, ?)""",
            (when, (wins or "").strip() or None, (blockers or "").strip() or None, (lessons or "").strip() or None),
        )
        return cur.lastrowid


def monthly_summary(days=30):
    """
    For end-of-month view: shipped (Done projects), resolved issues, open issues logged, blockers from reflections.
    Returns counts and lists for the last N days.
    """
    with get_conn() as conn:
        since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        done_projects = conn.execute(
            """SELECT id, client, name, updated_at FROM projects
               WHERE stage = 'Done' AND date(updated_at) >= ? ORDER BY updated_at DESC""",
            (since,),
        ).fetchall()
        resolved = conn.execute(
            """SELECT i.id, i.title, i.category, i.resolved_at, p.client FROM issues i
               LEFT JOIN projects p ON i.project_id = p.id
               WHERE i.resolved_at IS NOT NULL AND date(i.resolved_at) >= ?
               ORDER BY i.resolved_at DESC""",
            (since,),
        ).fetchall()
        opened = conn.execute(
            """SELECT i.id, i.title, i.category, i.created_at, p.client FROM issues i
               LEFT JOIN projects p ON i.project_id = p.id
               WHERE date(i.created_at) >= ? ORDER BY i.created_at DESC""",
            (since,),
        ).fetchall()
        reflections_with_blockers = conn.execute(
            """SELECT date, blockers, wins FROM reflections
               WHERE date >= ? AND (blockers IS NOT NULL AND blockers != '') ORDER BY date DESC""",
            (since,),
        ).fetchall()
    brain_dumps = reflections_since(days)
    return {
        "days": days,
        "shipped": [dict(p) for p in done_projects],
        "resolved_issues": [dict(r) for r in resolved],
        "issues_logged": [dict(o) for o in opened],
        "blockers_from_reflections": [dict(r) for r in reflections_with_blockers],
        "brain_dumps": [dict(r) for r in brain_dumps],
    }
