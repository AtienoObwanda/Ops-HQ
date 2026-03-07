"""
CS Bot - Database Layer
SQLite, zero infra. Just a file.
"""
import sqlite3
import os
from datetime import datetime, timedelta

# Use Railway volume path when set so the DB persists across deploys (otherwise each deploy = new container = empty disk)
_default_db = "cs_bot.db"
if os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") and os.environ.get("CS_BOT_DB", _default_db) == _default_db:
    _default_db = os.path.join(os.environ["RAILWAY_VOLUME_MOUNT_PATH"], "cs_bot.db")
DB_PATH = os.environ.get("CS_BOT_DB", _default_db)


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
        conn.execute("DELETE FROM clients WHERE id = ?", (client_id,))


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


def update_project(project_id, **kwargs):
    allowed = {"stage", "health", "notes", "owner_slack", "owner_name", "recon_slack", "recon_name", "go_live", "name", "client", "client_id"}
    updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not updates:
        return
    updates["updated_at"] = datetime.utcnow().isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [project_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE projects SET {set_clause} WHERE id = ?", values)


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
