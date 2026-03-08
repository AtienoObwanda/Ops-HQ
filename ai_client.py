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

def generate_product_scope_from_tickets_only(tickets_text):
    """
    From Jira tickets only (including comments): AI suggests an escalation title and drafts the full product scope.
    Returns a single text block: first line 'TITLE: <suggested title>', then blank line, then scope document.
    """
    system = """You are a senior delivery/ops lead drafting a product scope for an escalation to the Product team.
You will be given Jira ticket details including descriptions and comments. Analyze everything end-to-end: comments often contain clarifications, decisions, and context. Do two things:
1. Suggest a short escalation title (e.g. "Kuda Bill Payment – Glo reporting and recon") based on the tickets.
2. Write a full product scope document.

Your response MUST start with exactly:
TITLE: <your suggested title here>

Then a blank line, then the product scope with this structure:
- Summary (2–3 sentences: what we're asking for and why)
- Background / context (from the tickets)
- Proposed scope (clear bullets; what should be in scope)
- Out of scope (if relevant)
- Success criteria / acceptance
- References (ticket keys)

Keep the scope under 400 words. Plain English. For internal Product consumption."""

    user = f"""JIRA TICKETS (description + comments when present):

{tickets_text or 'No tickets provided.'}

Use the full context above (including any comments) to suggest a title and draft the scope. Output the TITLE line first, then the product scope document."""

    return _call(system, user, max_tokens=1200)


def generate_product_scope(title, description, tickets_text, future_notes, drive_content):
    """
    Draft a product scope document for an escalation to product.
    Inputs: escalation title/description; text from Jira tickets (including comments); future/backlog notes; Google Drive links + pasted content.
    """
    system = """You are a senior delivery/ops lead drafting a product scope for an escalation to the Product team.
Your output is a clear, structured product scope document that Product can use to prioritise and spec work.
Use the provided context: Jira tickets (descriptions and comments — analyze comments for decisions and clarifications), future/backlog notes, and any Drive references. Be specific and actionable.
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


# ── DELIVERY / PROJECT SCOPE (for completed projects, before assignment) ───────

def generate_delivery_scope(project, recent_updates, open_issues):
    """
    Draft a delivery scope / project scope document for a project (for handoff, before assignment).
    Used before UAT signoff; UAT signoff is then derived from this scope.
    """
    system = """You are a delivery manager writing a formal Delivery Scope / Project Scope document for a completed or near-complete implementation project.
This document defines what was delivered and is in scope for handoff and UAT signoff. Be clear and specific.
Structure:
1. Project summary (client, project name, stage, go-live if any)
2. Scope of delivery (numbered or bullet list of what was delivered / in scope)
3. Out of scope (if any)
4. Dependencies / prerequisites (if relevant)
5. Acceptance criteria (what constitutes successful delivery)
Keep it under 400 words. Plain English. This will be used to generate a UAT signoff document."""

    updates_text = "\n".join([f"- {u.get('content', '')} ({(u.get('created_at') or '')[:10]})" for u in recent_updates]) or "No updates logged."
    issues_text = "\n".join([f"- [{i.get('category', '')}] {i.get('title', '')}" for i in open_issues]) or "No open issues."

    user = f"""Project:
Client: {project.get('client', '')}
Name: {project.get('name', '')}
Stage: {project.get('stage', '')}
Health: {project.get('health', '')}
Go-Live: {project.get('go_live') or 'TBD'}
Notes: {project.get('notes') or 'None'}

Recent updates:
{updates_text}

Open issues (for context):
{issues_text}

Write the Delivery Scope / Project Scope document now."""

    return _call(system, user, max_tokens=800)


def generate_uat_signoff_from_scope(scope_text, project_name, client_name):
    """
    Generate a UAT signoff document from an existing project/delivery scope.
    Output: signoff-ready document with scope summary and sign-off lines for client/stakeholder.
    """
    system = """You are a delivery manager creating a UAT (User Acceptance Testing) signoff document.
You are given a Delivery/Project Scope. Turn it into a formal UAT signoff document that:
1. Has a title: "UAT Sign-off" and project/client names
2. Briefly restates the scope (summary bullets derived from the scope)
3. Includes a clear "Sign-off" section with:
   - Statement that the client/stakeholder has reviewed and accepts the delivered scope
   - Line for Name, Role, Date, Signature (or "Signed by" for electronic signoff)
4. Optional: space for comments/conditions
Keep it professional and under 350 words. The scope items should be traceable to the original scope so the client knows what they are signing off on."""

    user = f"""Project: {project_name}
Client: {client_name}

Delivery / Project Scope (use this to derive the signoff content):
---
{scope_text or 'No scope provided.'}
---

Generate the UAT signoff document now."""

    return _call(system, user, max_tokens=600)


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


# ── AI DOCUMENTS (industry-standard templates, tickets and/or context) ─────────

# Applied to every generated document so outputs are consistent across templates.
DOCUMENT_CONSISTENCY_RULES = """
Document consistency (apply to all output):
- Use clear numbered sections (1. 2. 3.) and optional subsections; bullet lists for scope/deliverables/actions.
- Use consistent terminology: "Out of scope" (not OOS), "Success criteria", "Acceptance criteria"; avoid acronyms unless defined once.
- Tone: professional, neutral; prefer active voice and concrete nouns.
- No placeholder text like [TBD] or [Insert X] unless the template explicitly allows it; infer from context or write "To be confirmed" where needed.
- Start with a document title line; include version or date where the template calls for it.
- If a client name is provided for the document, the first line (document title) MUST include the client name (e.g. "PRD — Acme Corp" or "Technical specification — Acme Corp").
"""

DOCUMENT_TEMPLATES = {
    "product_scope": {
        "name": "Product scope / Escalation",
        "description": "Summary, background, proposed scope, OOS, success criteria, references.",
        "system": """You are a senior delivery/ops lead drafting a product scope for the Product team.
Use ONLY the context provided (Jira tickets may include descriptions and comments — analyze comments end-to-end; and/or pasted context). Output a document that meets engineering/product standards.
Structure: 1) Summary (2–3 sentences). 2) Background/context. 3) Proposed scope (bullets; each item clear and actionable). 4) Out of scope. 5) Success criteria (measurable/verifiable where possible). 6) References (ticket keys, docs).
Under 500 words. Plain English. Suitable for engineering and product handoff.""",
    },
    "prd": {
        "name": "PRD (Product Requirements Document)",
        "description": "Overview, goals, user stories, acceptance criteria, OOS, dependencies.",
        "system": """You are a product manager writing a Product Requirements Document (PRD) that meets engineering standards.
Use ONLY the context provided. Follow industry-standard PRD structure (BABOK/PMI-aligned):
1. Overview & problem statement
2. Goals and success metrics (measurable)
3. User personas (if applicable)
4. User stories / requirements in clear, testable form (e.g. As a [role] I want [goal] so that [outcome]; or Given/When/Then)
5. Acceptance criteria — each criterion must be testable/verifiable (no vague language)
6. Out of scope
7. Dependencies and assumptions
8. Timeline / phases (if known)
Under 600 words. Requirements must be unambiguous and implementable by engineering.""",
    },
    "technical_spec": {
        "name": "Technical specification",
        "description": "Overview, architecture, data/API, security, NFRs, risks.",
        "system": """You are a technical lead writing a Technical Specification that meets engineering standards.
Use ONLY the context provided. Follow industry-standard tech spec structure (IEEE/SWE best practices):
1. Overview and objectives
2. Architecture / approach (components, flow, key design decisions)
3. Data model or API (key contracts, payloads, or schema points)
4. Security and compliance considerations
5. Non-functional requirements (performance, availability, scalability — measurable where possible)
6. Error handling and rollback (if applicable)
7. Risks and mitigations
Under 550 words. Precise, implementable, and reviewable by engineers. Avoid vague language.""",
    },
    "uat_signoff": {
        "name": "UAT signoff",
        "description": "Formal UAT sign-off with scope summary and sign-off block.",
        "system": """You are a delivery manager creating a UAT (User Acceptance Testing) signoff document that meets governance standards.
Use ONLY the context provided. Output a formal signoff suitable for audit and compliance:
1. Document title, version/date, and project/client name
2. Scope summary (bullets of what is being accepted; traceable to requirements)
3. Sign-off section: clear statement of acceptance, lines for Name, Role, Date, Signature
4. Optional: comments/conditions/limitations
Under 350 words. Professional, legally-sound, and suitable for client or internal approval.""",
    },
    "project_charter": {
        "name": "Project charter",
        "description": "Objectives, scope, deliverables, assumptions, success criteria.",
        "system": """You are a project manager writing a Project Charter that meets PMI/PMBOK standards.
Use ONLY the context provided. Follow project charter best practices:
1. Project name and sponsor
2. Business case / objectives (SMART where applicable)
3. Scope (in and out; clear boundaries)
4. Key deliverables (concrete, measurable)
5. Assumptions and constraints
6. High-level timeline and milestones
7. Success criteria (measurable)
Under 500 words. Authoritative and approval-ready for steering or sponsor sign-off.""",
    },
    "meeting_notes": {
        "name": "Meeting notes / Decision log",
        "description": "Decisions, action items, next steps.",
        "system": """You are an operations lead writing meeting notes / decision log that meet governance standards.
Use ONLY the context provided. Structure:
1. Date and (if known) attendees
2. Key decisions made (each decision clearly stated; reversible/irreversible if relevant)
3. Action items (owner + deliverable + due date where possible)
4. Next steps and follow-up date
Under 400 words. Clear ownership, deadlines, and audit trail.""",
    },
}


def generate_document_from_template(template_id, tickets_text, context, client_name=None):
    """
    Generate a document from an industry-standard template. Purely AI.
    template_id: key in DOCUMENT_TEMPLATES
    tickets_text: optional text from Jira tickets (includes descriptions and comments when provided)
    context: optional free-form context (user paste)
    client_name: optional; when set, the document title must include this client name.
    If no tickets and no context, AI will still try to produce a draft from minimal prompt.
    """
    template = DOCUMENT_TEMPLATES.get(template_id)
    if not template:
        raise ValueError(f"Unknown template: {template_id}")
    system = DOCUMENT_CONSISTENCY_RULES.strip() + "\n\n" + template["system"]
    parts = []
    if client_name and client_name.strip():
        parts.append(f"--- CLIENT FOR THIS DOCUMENT (include in document title) ---\nClient: {client_name.strip()}")
    if tickets_text and tickets_text.strip():
        parts.append("--- JIRA TICKETS (description + comments when present; analyze end-to-end) ---\n" + tickets_text.strip())
    if context and context.strip():
        parts.append("--- ADDITIONAL CONTEXT (user-provided) ---\n" + context.strip())
    if not parts:
        parts.append("--- CONTEXT ---\nNo tickets or context provided. Generate a concise placeholder document following the template structure so the user can replace with real content.")
    user = "\n\n".join(parts) + "\n\nGenerate the document now."
    return _call(system, user, max_tokens=1200)
