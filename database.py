"""
Ops Brain - Database Layer
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
            stage       TEXT NOT NULL DEFAULT 'Coming Soon',
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

        CREATE TABLE IF NOT EXISTS checkins (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            engineer_slack TEXT NOT NULL,
            engineer_name  TEXT,
            project_id     INTEGER REFERENCES projects(id),
            response       TEXT,
            sent_at        TEXT NOT NULL DEFAULT (datetime('now')),
            replied_at     TEXT
        );

        CREATE TABLE IF NOT EXISTS brain_dumps (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            author_slack TEXT NOT NULL,
            author_name  TEXT,
            content      TEXT NOT NULL,
            created_at   TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """)
    print(f"✅ DB initialised at {DB_PATH}")


# ── PROJECTS ──────────────────────────────────────────────────────────────────

VALID_STAGES = [
    "Coming Soon", "Requirement Gathering", "Ticket Grooming", "To Do",
    "In Progress", "Internal User Testing", "Customer Testing", "Done",
]
VALID_HEALTH = ["On Track", "At Risk", "Blocked"]


def add_project(client, name, owner_slack=None, owner_name=None, go_live=None, stage="Coming Soon"):
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


def projects_in_stage(stage_name):
    """All projects currently in this stage (e.g. Internal User Testing)."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT * FROM projects WHERE stage = ? ORDER BY client""",
            (stage_name,),
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


# ── BRAIN DUMPS ───────────────────────────────────────────────────────────────

def add_brain_dump(author_slack, content, author_name=None):
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO brain_dumps (author_slack, author_name, content)
               VALUES (?, ?, ?)""",
            (author_slack, author_name, content)
        )
        return cur.lastrowid


def brain_dumps_since(date_iso):
    """Get all brain dumps on or after this date (YYYY-MM-DD)."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT * FROM brain_dumps
               WHERE date(created_at) = date(?)
               ORDER BY created_at DESC""",
            (date_iso,)
        ).fetchall()


def latest_brain_dump_for_user(author_slack, days_back=1):
    """Get the most recent brain dump by this user in the last N days."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT * FROM brain_dumps
               WHERE author_slack = ?
               AND julianday('now') - julianday(created_at) <= ?
               ORDER BY created_at DESC LIMIT 1""",
            (author_slack, days_back)
        ).fetchone()


def brain_dumps_yesterday():
    """Get all brain dumps from yesterday (for morning brief)."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT * FROM brain_dumps
               WHERE date(created_at) = date('now', '-1 day')
               ORDER BY created_at DESC"""
        ).fetchall()


def brain_dumps_today():
    """Get all brain dumps from today (so /brief shows same-day dumps too)."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT * FROM brain_dumps
               WHERE date(created_at) = date('now')
               ORDER BY created_at DESC"""
        ).fetchall()


def brain_dumps_between(start_iso, end_iso):
    """Get all brain dumps between two dates (inclusive). Dates YYYY-MM-DD."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT * FROM brain_dumps
               WHERE date(created_at) >= date(?) AND date(created_at) <= date(?)
               ORDER BY created_at ASC""",
            (start_iso, end_iso),
        ).fetchall()


def brain_dumps_last_week():
    """Mon–Fri of the week that just ended (for Saturday week report). SQLite: weekday 0 = Sunday."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT * FROM brain_dumps
               WHERE date(created_at) BETWEEN date('now', 'weekday 0', '-6 days')
               AND date('now', 'weekday 0', '-2 days')
               ORDER BY created_at ASC"""
        ).fetchall()


def brain_dumps_last_month():
    """Previous calendar month (for month report on 1st)."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT * FROM brain_dumps
               WHERE strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now', '-1 month')
               ORDER BY created_at ASC"""
        ).fetchall()


# ── REPORTING ─────────────────────────────────────────────────────────────────

def projects_by_stage():
    with get_conn() as conn:
        return conn.execute(
            """SELECT stage, COUNT(*) as count FROM projects
               WHERE stage != 'Done' GROUP BY stage"""
        ).fetchall()


if __name__ == "__main__":
    init_db()
