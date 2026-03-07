"""
CS Bot - Database Layer
SQLite, zero infra. Just a file.
"""
import sqlite3
import os
from datetime import datetime

DB_PATH = os.environ.get("CS_BOT_DB", "cs_bot.db")


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
            owner_slack TEXT,          -- Slack user ID e.g. U0123ABC
            owner_name  TEXT,
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
    print(f"✅ DB initialised at {DB_PATH}")


# ── PROJECTS ──────────────────────────────────────────────────────────────────

VALID_STAGES = [
    "Discovery", "Config", "Integration", "UAT", "Go-Live", "Hypercare", "Done"
]
VALID_HEALTH = ["On Track", "At Risk", "Blocked"]


def add_project(client, name, owner_slack=None, owner_name=None, go_live=None, stage="Discovery"):
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO projects (client, name, owner_slack, owner_name, go_live, stage)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (client, name, owner_slack, owner_name, go_live, stage)
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
    allowed = {"stage", "health", "notes", "owner_slack", "owner_name", "go_live", "name", "client"}
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


def log_issue(title, category, description=None, project_id=None, reported_by=None):
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO issues (title, category, description, project_id, reported_by)
               VALUES (?, ?, ?, ?, ?)""",
            (title, category, description, project_id, reported_by)
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
            """SELECT i.*, p.client FROM issues i
               LEFT JOIN projects p ON i.project_id = p.id
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
# Note: reflections table is created via init_db — add it if missing
