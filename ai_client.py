"""
CS Bot — Claude AI Client
Powers /clientupdate and weekly pattern digest.

Env vars needed:
    ANTHROPIC_API_KEY   your-anthropic-api-key
"""
import os
import json
import requests

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
API_URL = "https://api.anthropic.com/v1/messages"
MODEL   = "claude-sonnet-4-20250514"


def _call(system_prompt, user_content, max_tokens=800):
    if not ANTHROPIC_API_KEY:
        raise EnvironmentError("ANTHROPIC_API_KEY not set.")

    # Prompt Caching: cache repeated system prompt for ~5 min; cache reads ~90% cheaper (Anthropic)
    resp = requests.post(
        API_URL,
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": MODEL,
            "max_tokens": max_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_content}],
            "cache_control": {"type": "ephemeral"},
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["content"][0]["text"].strip()


# ── CLIENT UPDATE EMAIL ───────────────────────────────────────────────────────

def generate_client_update(project, recent_updates, open_issues):
    """
    Drafts a professional client-facing status update email.
    Atieno reviews and sends — bot does the writing.
    """
    system = """You are a senior delivery manager at a fintech company writing professional 
client status update emails. Your tone is confident, clear, and reassuring — never vague, 
never over-promising. You write in plain English, no jargon. Keep it under 200 words.
Structure: brief status summary → what's happening now → next milestone → any action needed from client.
Do NOT use bullet points. Write in flowing paragraphs. Sign off as 'Atieno'."""

    updates_text = "\n".join([f"- {u['content']} ({u['created_at'][:10]})" for u in recent_updates]) or "No recent updates logged."
    issues_text  = "\n".join([f"- [{i['category']}] {i['title']}" for i in open_issues]) or "No open issues."

    user = f"""Write a client status update email for:

Client: {project['client']}
Current Stage: {project['stage']}
Health: {project['health']}
Go-Live Date: {project['go_live'] or 'TBD'}
Notes: {project['notes'] or 'None'}

Recent internal updates:
{updates_text}

Open issues (internal — DO NOT mention specifics, just reflect in tone/next steps):
{issues_text}

Write the email now. Subject line first, then body."""

    return _call(system, user, max_tokens=500)


def generate_client_update_for_client(client, projects, open_issues):
    """
    Drafts one client-facing status email for the whole client (all their projects + issues).
    For sales/product summaries tied to ticket performance.
    """
    system = """You are a senior delivery manager at a fintech company writing professional 
client status update emails. Your tone is confident, clear, and reassuring — never vague, 
never over-promising. You write in plain English, no jargon. Keep it under 250 words.
Structure: brief status summary for this client → what's happening across their work → next milestones → any action needed from client.
Do NOT use bullet points. Write in flowing paragraphs. Sign off as 'Atieno'."""

    proj_lines = []
    for p in projects:
        proj_lines.append(f"- {p.get('name', p.get('client', ''))}: stage {p.get('stage', '')}, health {p.get('health', '')}, go-live {p.get('go_live') or 'TBD'}")
    issues_text = "\n".join([f"- [{i.get('category', '')}] {i.get('title', '')}" for i in open_issues]) or "No open issues."

    user = f"""Write a client status update email for:

Client: {client.get('name', '')}
Their projects:
{chr(10).join(proj_lines)}

Open issues (internal — reflect in tone/next steps, don't list specifics):
{issues_text}

Write the email now. Subject line first, then body."""

    return _call(system, user, max_tokens=600)


# ── WEEKLY PATTERN DIGEST ─────────────────────────────────────────────────────

def generate_pattern_digest(issue_patterns, recent_reflections, stale_projects, at_risk_projects):
    """
    Every Friday: analyzes the week's data and produces an actionable digest.
    """
    system = """You are a delivery operations analyst for a fintech implementation team. 
You analyze weekly data to surface patterns, risks, and process improvement opportunities.
Be direct and specific — no fluff. Each recommendation must be actionable this week.
Format your response as:
1. Key patterns (what keeps happening)
2. Root cause hypothesis (why)
3. Process fix (what to do about it — specific and practical)
Keep it under 300 words total."""

    patterns_text = "\n".join([
        f"- {r['category']}: {r['count']} issues total, {r['open_count']} open"
        for r in issue_patterns
    ]) or "No issues logged this week."

    reflections_text = "\n".join([
        f"- Blockers: {r['blockers']} | Lessons: {r['lessons']}"
        for r in recent_reflections if r.get('blockers') or r.get('lessons')
    ]) or "No reflections logged."

    stale_text = "\n".join([f"- {p['client']} ({p['stage']}) — {p['owner_name'] or 'unassigned'}" for p in stale_projects]) or "None."
    risk_text  = "\n".join([f"- {p['client']}: {p['notes'] or 'no notes'}" for p in at_risk_projects]) or "None."

    user = f"""Analyze this week's CS delivery data:

ISSUE PATTERNS:
{patterns_text}

EOD REFLECTION THEMES:
{reflections_text}

PROJECTS THAT WENT STALE (no update 24h+):
{stale_text}

PROJECTS AT RISK / BLOCKED:
{risk_text}

Give me the pattern analysis and process fixes."""

    return _call(system, user, max_tokens=600)


# ── MEETING PREP ──────────────────────────────────────────────────────────────

def generate_meeting_prep(meeting_type, projects, issues):
    """
    Generates talking points for cross-functional meetings.
    meeting_type: 'sales_sync' | 'product_eng' | 'client_call'
    """
    system = """You are a senior delivery manager preparing for a cross-functional meeting 
at a fintech company. Generate concise, structured talking points. Be specific, not generic.
Lead with what needs a decision or action from the other team. Max 200 words."""

    project_text = "\n".join([
        f"- {p['client']} | {p['stage']} | {p['health']} | {p['notes'] or 'no notes'}"
        for p in projects
    ]) or "No active projects."

    issue_text = "\n".join([
        f"- [{i['category']}] {i['title']}"
        for i in issues
    ]) or "No open issues."

    meeting_context = {
        "sales_sync":   "Sales & CS sync. You represent Customer Success. Sales wants to know delivery capacity and timelines. You need to push back on unrealistic promises and flag what's at risk.",
        "product_eng":  "Product & Engineering escalation meeting. You're escalating implementation blockers and system issues that are affecting client delivery.",
        "client_call":  "Client status call. You need to project confidence, give clear next steps, and handle any concerns without over-committing.",
    }.get(meeting_type, "Cross-functional meeting.")

    user = f"""Meeting type: {meeting_context}

Current project portfolio:
{project_text}

Open issues:
{issue_text}

Generate my talking points for this meeting."""

    return _call(system, user, max_tokens=400)
