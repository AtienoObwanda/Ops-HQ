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


# ── MONTHLY REPORT (brain dumps integrated) ────────────────────────────────────

def generate_monthly_report(monthly_data):
    """
    Generate a narrative monthly report. Brain dumps (reflections) are integrated into the AI prompt
    so the report reflects wins, blockers, and lessons from EOD reflections.
    Use for end-of-month summaries or on request.
    """
    system = """You are a delivery operations lead writing an internal monthly summary for CS/implementation.
Use the data below (shipped work, issues, and brain dumps / EOD reflections) to write a concise narrative report.
Structure: 1) Summary in 2–3 sentences. 2) What shipped. 3) Issues and blockers (from brain dumps and issue log). 4) Wins and lessons (from brain dumps). 5) One paragraph of recommendations or focus for next period.
Be specific — reference client names, ticket counts, and reflection themes. Keep under 400 words."""

    days = monthly_data.get("days", 30)
    shipped = monthly_data.get("shipped", [])
    resolved = monthly_data.get("resolved_issues", [])
    opened = monthly_data.get("issues_logged", [])
    blockers_ref = monthly_data.get("blockers_from_reflections", [])
    brain_dumps = monthly_data.get("brain_dumps", [])

    shipped_text = "\n".join([f"- {p.get('client', '')} ({p.get('name', '')}) — {p.get('updated_at', '')[:10]}" for p in shipped]) or "None."
    resolved_text = "\n".join([f"- {r.get('title', '')} [{r.get('category', '')}]" for r in resolved[:15]]) or "None."
    opened_text = "\n".join([f"- {o.get('title', '')} [{o.get('category', '')}]" for o in opened[:15]]) or "None."
    blockers_text = "\n".join([f"- {r.get('date', '')}: {r.get('blockers', '')}" for r in blockers_ref]) or "None."

    brain_text = "\n".join([
        f"- {r.get('date', '')}: Wins: {r.get('wins') or '—'} | Blockers: {r.get('blockers') or '—'} | Lessons: {r.get('lessons') or '—'}"
        for r in brain_dumps
    ]) or "No brain dumps in this period."

    user = f"""Last {days} days — generate the monthly report.

SHIPPED (moved to Done):
{shipped_text}

RESOLVED ISSUES:
{resolved_text}

ISSUES LOGGED (opened):
{opened_text}

BLOCKERS FROM REFLECTIONS:
{blockers_text}

BRAIN DUMPS (EOD reflections — integrate these into the narrative):
{brain_text}

Write the monthly report now."""

    return _call(system, user, max_tokens=800)


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


# ── PRODUCT SCOPE (escalation to product: draft scope from tickets, future, drive) ─

def generate_product_scope(title, description, tickets_text, future_notes, drive_content):
    """
    Draft a product scope document for an escalation to product.
    Inputs: escalation title/description; text from Jira tickets; future/backlog notes; Google Drive links + pasted content.
    """
    system = """You are a senior delivery/ops lead drafting a product scope for an escalation to the Product team.
Your output is a clear, structured product scope document that Product can use to prioritise and spec work.
Use the provided context from Jira tickets, future/backlog notes, and any Drive references. Be specific and actionable.
Structure the scope as:
1. Summary (2–3 sentences: what we're asking for and why)
2. Background / context (from tickets and notes)
3. Proposed scope (clear bullets or numbered items; what should be in scope)
4. Out of scope (if relevant)
5. Success criteria / acceptance (how we know it's done)
6. References (ticket keys, Drive links if any)
Keep it under 500 words. Write in plain English, no jargon. This is for internal Product consumption."""

    user_parts = [f"Escalation title: {title}"]
    if description:
        user_parts.append(f"Escalation description:\n{description}")
    user_parts.append("\n--- JIRA TICKETS ---")
    user_parts.append(tickets_text or "No tickets linked.")
    user_parts.append("\n--- FUTURE / BACKLOG NOTES ---")
    user_parts.append(future_notes or "None provided.")
    user_parts.append("\n--- GOOGLE DRIVE / ADDITIONAL CONTEXT ---")
    user_parts.append(drive_content or "None provided.")
    user_parts.append("\n\nDraft the product scope document now.")

    return _call(system, "\n".join(user_parts), max_tokens=1200)


# ── ON-DEMAND ASK (any info from cockpit) ─────────────────────────────────────

def answer_cockpit_prompt(user_prompt, context_text):
    """
    Answer a free-form question using the provided cockpit data (projects, clients, issues, etc.).
    Context is built by the API from the database; user_prompt is what the user asked.
    """
    system = """You are an assistant for a delivery operations / CS cockpit (Ops HQ). You have been given a snapshot of current data: projects, clients, issues, brain dumps, Jira summary, etc.
Answer the user's question using only the data provided. Be concise and specific. If the data doesn't contain enough to answer, say so and suggest what would help.
Do not make up numbers or names. Quote or summarize from the context. Format lists clearly when useful."""

    user = f"""COCKPIT DATA (current snapshot):

{context_text}

---

USER QUESTION:
{user_prompt}

Answer based on the data above."""

    return _call(system, user, max_tokens=1000)
