"""
CS Bot — Jira Cloud Integration
Polls Jira for stale tickets and status changes, surfaces them in the morning brief.

Env vars needed:
    JIRA_BASE_URL       https://yourcompany.atlassian.net
    JIRA_EMAIL          your@email.com
    JIRA_API_TOKEN      your-api-token (https://id.atlassian.com/manage-profile/security/api-tokens)
    JIRA_PROJECT_KEYS   CS,IMPL,OPS  (comma-separated)
    JIRA_CREATED_SINCE  optional; YYYY-MM-DD, only issues created on or after this date (default 2026-01-01)
"""
import os
import base64
import requests
from datetime import datetime, timezone, timedelta

JIRA_BASE_URL   = os.environ.get("JIRA_BASE_URL", "").rstrip("/")
JIRA_EMAIL      = os.environ.get("JIRA_EMAIL", "")
JIRA_API_TOKEN  = os.environ.get("JIRA_API_TOKEN", "")
JIRA_PROJECT_KEYS = [k.strip() for k in os.environ.get("JIRA_PROJECT_KEYS", "").split(",") if k.strip()]

STALE_HOURS = int(os.environ.get("JIRA_STALE_HOURS", "24"))
# Only issues created on or after this date (YYYY-MM-DD). Set to "" to include all.
JIRA_CREATED_SINCE = os.environ.get("JIRA_CREATED_SINCE", "2026-01-01").strip()


def _created_since_jql():
    """JQL fragment to restrict to issues created on or after JIRA_CREATED_SINCE."""
    if not JIRA_CREATED_SINCE:
        return ""
    return f' AND created >= "{JIRA_CREATED_SINCE}"'


def _auth_header():
    creds = base64.b64encode(f"{JIRA_EMAIL}:{JIRA_API_TOKEN}".encode()).decode()
    return {
        "Authorization": f"Basic {creds}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _jira_get(path, params=None):
    if not all([JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN]):
        raise EnvironmentError("Jira env vars not set. Check JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN.")
    url = f"{JIRA_BASE_URL}/rest/api/3{path}"
    resp = requests.get(url, headers=_auth_header(), params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


def _jira_get_issue(issue_key, fields=None):
    """GET single issue by key. Returns raw issue dict or None if not found."""
    if not all([JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN]):
        return None
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}"
    params = {}
    if fields:
        params["fields"] = ",".join(fields) if isinstance(fields, list) else fields
    try:
        resp = requests.get(url, headers=_auth_header(), params=params or None, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def _jira_get_issue_comments(issue_key):
    """GET comments for an issue. Returns list of { author, created, body_plain }."""
    if not all([JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN]):
        return []
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}/comment"
    try:
        resp = requests.get(url, headers=_auth_header(), timeout=10)
        resp.raise_for_status()
        data = resp.json()
        comments = data.get("comments", [])
        out = []
        for c in comments:
            body = c.get("body")
            body_plain = _adf_to_plain(body).strip() if body else ""
            author = (c.get("author") or {}).get("displayName", "Unknown")
            created = c.get("created", "")[:19]  # trim to datetime
            out.append({"author": author, "created": created, "body_plain": body_plain})
        return out
    except Exception:
        return []


def _adf_to_plain(node):
    """Recursively extract plain text from Jira ADF (Atlassian Document Format) description."""
    if not node:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        text = node.get("text") or ""
        content = node.get("content") or []
        return text + "".join(_adf_to_plain(c) for c in content)
    if isinstance(node, list):
        return "".join(_adf_to_plain(c) for c in node)
    return ""


def _jira_search_jql(jql, fields=None, max_results=20, expand=None):
    """
    Use POST /rest/api/3/search/jql (old GET /search returns 410 Gone on Jira Cloud).
    Request body: jql, maxResults, fields (array), optional expand.
    """
    if not all([JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN]):
        raise EnvironmentError("Jira env vars not set.")
    url = f"{JIRA_BASE_URL}/rest/api/3/search/jql"
    body = {"jql": jql, "maxResults": max_results}
    if fields:
        body["fields"] = fields if isinstance(fields, list) else [f.strip() for f in (fields or "").split(",") if f.strip()]
    if expand:
        body["expand"] = expand
    resp = requests.post(url, headers=_auth_header(), json=body, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    # New API may return "values" instead of "issues"; accept both
    return data.get("issues", data.get("values", []))


def is_configured():
    return all([JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN, JIRA_PROJECT_KEYS])


# ── STALE TICKETS ─────────────────────────────────────────────────────────────

def get_stale_tickets(hours=None):
    """
    Returns tickets with no update/comment in the last N hours.
    Excludes Done/Closed tickets.
    """
    if not is_configured():
        return []

    hours = hours or STALE_HOURS
    project_jql = " OR ".join(f'project = "{k}"' for k in JIRA_PROJECT_KEYS)
    jql = (
        f"({project_jql}) "
        f"AND status NOT IN (Done, Closed, Resolved) "
        f"AND updated <= -{hours}h "
        f"{_created_since_jql()} "
        f"ORDER BY updated ASC"
    )

    try:
        issues = _jira_search_jql(jql, fields=["summary", "status", "assignee", "updated", "priority", "comment"], max_results=20)
        return [_parse_ticket(t) for t in issues]
    except Exception as e:
        print(f"⚠️  Jira stale tickets error: {e}")
        return []


def get_blocked_tickets():
    """Returns tickets explicitly in Blocked status."""
    if not is_configured():
        return []

    project_jql = " OR ".join(f'project = "{k}"' for k in JIRA_PROJECT_KEYS)
    jql = f"({project_jql}) AND status = Blocked{_created_since_jql()} ORDER BY updated ASC"

    try:
        issues = _jira_search_jql(jql, fields=["summary", "status", "assignee", "updated", "priority"], max_results=10)
        return [_parse_ticket(t) for t in issues]
    except Exception as e:
        print(f"⚠️  Jira blocked tickets error: {e}")
        return []


def get_tickets_for_project(project_key):
    """All open tickets for a specific Jira project key."""
    if not is_configured():
        return []

    jql = f'project = "{project_key}" AND status NOT IN (Done, Closed, Resolved){_created_since_jql()} ORDER BY priority DESC'
    try:
        issues = _jira_search_jql(jql, fields=["summary", "status", "assignee", "updated", "priority"], max_results=15)
        return [_parse_ticket(t) for t in issues]
    except Exception as e:
        print(f"⚠️  Jira project tickets error: {e}")
        return []


def get_recent_status_changes(hours=24):
    """Tickets whose status changed in the last N hours."""
    if not is_configured():
        return []

    project_jql = " OR ".join(f'project = "{k}"' for k in JIRA_PROJECT_KEYS)
    jql = (
        f"({project_jql}) "
        f"AND status CHANGED DURING (-{hours}h, now()) "
        f"{_created_since_jql()} "
        f"ORDER BY updated DESC"
    )
    try:
        issues = _jira_search_jql(jql, fields=["summary", "status", "assignee", "updated", "priority"], max_results=15, expand="changelog")
        results = []
        for issue in (issues if isinstance(issues, list) else []):
            ticket = _parse_ticket(issue)
            # Extract the status transition from changelog
            for history in reversed(issue.get("changelog", {}).get("histories", [])):
                for item in history.get("items", []):
                    if item.get("field") == "status":
                        ticket["from_status"] = item.get("fromString", "")
                        ticket["to_status"]   = item.get("toString", "")
                        ticket["changed_at"]  = history.get("created", "")
                        break
                if "from_status" in ticket:
                    break
            results.append(ticket)
        return results
    except Exception as e:
        print(f"⚠️  Jira status changes error: {e}")
        return []


# ── TICKET PARSER ─────────────────────────────────────────────────────────────

def _parse_ticket(issue):
    fields = issue.get("fields", {})
    assignee = fields.get("assignee") or {}
    return {
        "key":        issue.get("key", ""),
        "summary":    fields.get("summary", ""),
        "status":     fields.get("status", {}).get("name", ""),
        "priority":   fields.get("priority", {}).get("name", "Medium"),
        "assignee":   assignee.get("displayName", "Unassigned"),
        "assignee_account_id": assignee.get("accountId"),
        "updated":    fields.get("updated", ""),
        "url":        f"{JIRA_BASE_URL}/browse/{issue.get('key', '')}",
        "stale_hours": _hours_since(fields.get("updated", "")),
    }


def _hours_since(iso_str):
    if not iso_str:
        return 0
    try:
        # Jira returns: 2024-01-15T14:30:00.000+0000
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        diff = datetime.now(timezone.utc) - dt
        return int(diff.total_seconds() / 3600)
    except Exception:
        return 0


# ── FETCH BY KEYS (for product scope drafting) ──────────────────────────────────

def get_tickets_by_keys(keys, include_comments=True):
    """
    Fetch full issue details (summary, description, status, comments) for given Jira keys.
    Returns list of dicts with key, summary, status, description, comments (list of {author, created, body_plain}).
    Used for AI scope/document drafting so the AI can analyze ticket + comments end-to-end.
    """
    if not is_configured() or not keys:
        return []
    keys = [k.strip().upper() for k in keys if k and str(k).strip()]
    if not keys:
        return []
    result = []
    fields = ["summary", "description", "status", "assignee", "priority", "updated"]
    for key in keys:
        issue = _jira_get_issue(key, fields=fields)
        if not issue:
            continue
        fields_obj = issue.get("fields", {})
        desc = fields_obj.get("description")
        description_plain = _adf_to_plain(desc).strip() if desc else ""
        comments = _jira_get_issue_comments(key) if include_comments else []
        result.append({
            "key":        issue.get("key", ""),
            "summary":    fields_obj.get("summary", ""),
            "status":     (fields_obj.get("status") or {}).get("name", ""),
            "priority":   (fields_obj.get("priority") or {}).get("name", ""),
            "assignee":   (fields_obj.get("assignee") or {}).get("displayName", "Unassigned"),
            "updated":    fields_obj.get("updated", ""),
            "description": description_plain,
            "comments":   comments,
            "url":        f"{JIRA_BASE_URL}/browse/{issue.get('key', '')}",
        })
    return result


def format_tickets_for_ai(tickets):
    """
    Format ticket list (with descriptions and comments) into one text block for AI.
    AI can analyze ticket + comments end-to-end for scope/docs.
    """
    if not tickets:
        return ""
    blocks = []
    for t in tickets:
        lines = [
            f"[{t.get('key')}] {t.get('summary')}",
            f"Status: {t.get('status')}",
            f"Assignee: {t.get('assignee')}",
            "",
            (t.get("description") or "").strip() or "(No description)",
        ]
        comments = t.get("comments") or []
        if comments:
            lines.append("")
            lines.append("Comments:")
            for c in comments:
                lines.append(f"  - {c.get('created', '')} | {c.get('author', '')}: {c.get('body_plain', '')}")
        blocks.append("\n".join(lines))
    return "\n\n---\n\n".join(blocks)


# ── ONCALL vs PROJECT (CS- = oncall, rest = projects) ─────────────────────────

def is_oncall_ticket(key):
    """Tickets whose key starts with CS- are oncall; the rest are project tickets."""
    return (key or "").upper().startswith("CS-")


# Jira status -> pipeline stage for project tickets (non-CS) in pipeline view.
# Unknown statuses default to Discovery.
JIRA_STATUS_TO_STAGE = {
    "Ticket Grooming": "Discovery",
    "To Do": "Discovery",
    "Open": "Discovery",
    "Requirement gathering": "Discovery",
    "BLOCKED": "Discovery",
    "Config": "Config",
    "Integration": "Integration",
    "Internal User Testing": "UAT",
    "IN REVIEW": "UAT",
    "UAT": "UAT",
    "Go-Live": "Go-Live",
    "Hypercare": "Hypercare",
}

PIPELINE_STAGES = ["Discovery", "Config", "Integration", "UAT", "Go-Live", "Hypercare"]


def get_project_tickets_for_pipeline(max_results=100):
    """
    Open Jira tickets that are *not* oncall (i.e. not CS-*). Each ticket gets a
    `stage` field mapped from Jira status for pipeline board display.
    """
    if not is_configured():
        return []
    raw = get_grooming_tickets(max_results=max_results)
    out = []
    for t in raw:
        key = t.get("key") or ""
        if is_oncall_ticket(key):
            continue
        status = (t.get("status") or "").strip()
        stage = JIRA_STATUS_TO_STAGE.get(status) or JIRA_STATUS_TO_STAGE.get(
            status.upper()
        ) or "Discovery"
        if stage not in PIPELINE_STAGES:
            stage = "Discovery"
        out.append({**t, "stage": stage})
    return out


def get_oncall_tickets(max_results=80):
    """Open Jira tickets that are oncall (CS-*). For oncall one-pager."""
    if not is_configured():
        return []
    raw = get_grooming_tickets(max_results=max_results)
    return [t for t in raw if is_oncall_ticket(t.get("key") or "")]


# Project key for oncall in Jira (CS-*). Used for monthly report JQL.
ONCALL_PROJECT_KEY = os.environ.get("JIRA_ONCALL_PROJECT_KEY", "CS").strip() or "CS"


def get_oncall_tickets_updated_since(days=30, max_results=150):
    """Oncall (CS) tickets updated in the last N days (open or closed). For monthly report."""
    if not is_configured():
        return []
    jql = (
        f'project = "{ONCALL_PROJECT_KEY}" '
        f"AND updated >= -{int(days)}d "
        f"ORDER BY updated DESC"
    )
    try:
        issues = _jira_search_jql(
            jql,
            fields=["summary", "status", "assignee", "updated", "priority", "created"],
            max_results=max_results,
        )
        return [_parse_ticket(t) for t in issues]
    except Exception as e:
        print(f"⚠️  Jira oncall monthly report error: {e}")
        return []


def get_oncall_summary():
    """Summary stats for oncall one-pager: counts by status, by assignee, unassigned, oldest ticket."""
    tickets = get_oncall_tickets(max_results=200)
    by_status = {}
    by_assignee = {}
    unassigned = 0
    oldest = None
    for t in tickets:
        s = (t.get("status") or "—").strip() or "—"
        by_status[s] = by_status.get(s, 0) + 1
        a = (t.get("assignee") or "").strip()
        if a and a.lower() not in ("unassigned", "—", ""):
            by_assignee[a] = by_assignee.get(a, 0) + 1
        else:
            unassigned += 1
        updated = t.get("updated") or ""
        if updated:
            if oldest is None or (updated < oldest.get("updated", "")):
                oldest = {"key": t.get("key"), "summary": (t.get("summary") or "")[:60], "updated": updated, "url": t.get("url", "")}
    return {
        "total_open": len(tickets),
        "by_status": by_status,
        "by_assignee": by_assignee,
        "unassigned_count": unassigned,
        "oldest_ticket": oldest,
    }


# ── SUMMARY FOR BRIEF ─────────────────────────────────────────────────────────

def get_grooming_tickets(max_results=80):
    """
    All open Jira tickets (not Done/Closed) for ticket grooming view.
    Ordered by updated so backlog and in-progress items are visible.
    """
    if not is_configured():
        return []

    project_jql = " OR ".join(f'project = "{k}"' for k in JIRA_PROJECT_KEYS)
    jql = (
        f"({project_jql}) "
        f"AND status NOT IN (Done, Closed, Resolved) "
        f"{_created_since_jql()} "
        f"ORDER BY updated DESC"
    )
    try:
        issues = _jira_search_jql(jql, fields=["summary", "status", "assignee", "updated", "priority", "project"], max_results=max_results)
        return [_parse_ticket(t) for t in issues]
    except Exception as e:
        print(f"⚠️  Jira grooming tickets error: {e}")
        return []


# ── ENGINEER PERFORMANCE (resolved tickets, pickup → closure) ───────────────────

def get_resolved_tickets_for_performance(days=30, max_results=200):
    """
    Resolved (Done/Closed/Resolved) tickets in the last N days.
    Used for engineer performance: created to resolutiondate = days_to_resolve.
    Returns list of { key, assignee, created, resolutiondate, days_to_resolve }.
    """
    if not is_configured():
        return []
    project_jql = " OR ".join(f'project = "{k}"' for k in JIRA_PROJECT_KEYS)
    jql = (
        f"({project_jql}) "
        f"AND status IN (Done, Closed, Resolved) "
        f"AND resolutiondate >= -{int(days)}d "
        f"{_created_since_jql()} "
        f"ORDER BY resolutiondate DESC"
    )
    try:
        issues = _jira_search_jql(
            jql,
            fields=["summary", "assignee", "created", "resolutiondate"],
            max_results=max_results,
        )
        out = []
        for issue in issues:
            fields = issue.get("fields", {})
            assignee = (fields.get("assignee") or {}).get("displayName", "Unassigned")
            created = (fields.get("created") or "")[:10]
            res = (fields.get("resolutiondate") or "")[:10]
            if not created or not res:
                continue
            try:
                d1 = datetime.strptime(created, "%Y-%m-%d")
                d2 = datetime.strptime(res, "%Y-%m-%d")
                days_to_resolve = max(0, (d2 - d1).days)
            except Exception:
                days_to_resolve = 0
            out.append({
                "key": issue.get("key", ""),
                "assignee": assignee,
                "created": created,
                "resolutiondate": res,
                "days_to_resolve": days_to_resolve,
            })
        return out
    except Exception as e:
        print(f"⚠️  Jira resolved tickets for performance error: {e}")
        return []


def get_engineer_performance_stats(days=30):
    """
    Aggregate by assignee: closed count, avg days to resolve (created → resolution).
    Returns { "engineers": [ { name, closed_count, avg_days_to_resolve } ], "days": N }.
    """
    resolved = get_resolved_tickets_for_performance(days=days)
    by_assignee = {}
    for t in resolved:
        name = (t.get("assignee") or "").strip() or "Unassigned"
        if name.lower() in ("unassigned", "—", ""):
            name = "Unassigned"
        if name not in by_assignee:
            by_assignee[name] = {"closed_count": 0, "days_list": []}
        by_assignee[name]["closed_count"] += 1
        by_assignee[name]["days_list"].append(t.get("days_to_resolve", 0))
    engineers = []
    for name, data in by_assignee.items():
        days_list = data["days_list"]
        avg = round(sum(days_list) / len(days_list), 1) if days_list else 0
        engineers.append({
            "name": name,
            "closed_count": data["closed_count"],
            "avg_days_to_resolve": avg,
        })
    return {"engineers": engineers, "days": days}


# ── Jira ↔ Slack mapping (names don't need to match) ───────────────────────────
# JIRA_TO_SLACK = Felix:Uxxx:Kolakodhek,Joy:Uyyy:Joy,Mark:Uzzz:Kibocha,...
# Format: jira_display_name:slack_user_id[:slack_display_name]
def get_engineer_mapping():
    """
    Returns list of { jira_name, slack_id, slack_name } for request-update dropdown.
    Slack_name is how they appear on Slack (can differ from Jira).
    """
    raw = os.environ.get("JIRA_TO_SLACK", "").strip()
    if not raw:
        return []
    out = []
    for entry in raw.split(","):
        parts = [p.strip() for p in entry.strip().split(":") if p.strip()]
        if len(parts) >= 2:
            jira_name = parts[0]
            slack_id = parts[1]
            slack_name = parts[2] if len(parts) > 2 else jira_name
            out.append({"jira_name": jira_name, "slack_id": slack_id, "slack_name": slack_name})
    return out


def get_jira_brief_data():
    """
    Returns everything the morning brief needs from Jira.
    Single call so we don't hammer the API.
    """
    if not is_configured():
        return {"configured": False, "stale": [], "blocked": [], "changes": []}

    return {
        "configured": True,
        "stale":   get_stale_tickets(),
        "blocked": get_blocked_tickets(),
        "changes": get_recent_status_changes(hours=24),
    }
