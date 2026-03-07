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
