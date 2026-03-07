#!/usr/bin/env python3
"""
Print Jira brief data with assignees. Load .env from repo root.
Usage: python3 scripts/jira_assignees.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.chdir(os.path.join(os.path.dirname(__file__), ".."))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import jira_client as jira

if not jira.is_configured():
    print("Jira not configured. Set JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN, JIRA_PROJECT_KEYS in .env")
    sys.exit(1)

d = jira.get_jira_brief_data()

def show(label, tickets):
    print(f"\n{label} ({len(tickets)})")
    print("-" * 60)
    for t in tickets:
        print(f"  {t['key']:<14} {t['assignee']:<25} {t['summary'][:50]}")

show("Stale (no update 24h+)", d.get("stale", []))
show("Blocked", d.get("blocked", []))
show("Status changes (last 24h)", d.get("changes", []))
