"""
Generate PDFs for Delivery Scope and UAT Signoff (on request).
Uses fpdf2 for simple, dependency-free PDF output.
"""
from fpdf import FPDF


def _ascii_safe(s):
    """Replace common Unicode chars so default Helvetica (Latin-1) works."""
    if not s:
        return ""
    s = str(s)
    replacements = (
        ("\u2014", "-"),   # em dash
        ("\u2013", "-"),   # en dash
        ("\u2018", "'"),  # left single quote
        ("\u2019", "'"),  # right single quote
        ("\u201c", '"'),  # left double quote
        ("\u201d", '"'),  # right double quote
        ("\u2026", "..."), # ellipsis
    )
    for a, b in replacements:
        s = s.replace(a, b)
    return "".join(c if ord(c) < 256 else "?" for c in s)


def _pdf_from_text(title, body, filename_hint="document"):
    """Build a simple PDF with title and body text (wrapped). Returns PDF bytes."""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_font("Helvetica", "B", 14)
    pdf.multi_cell(0, 8, _ascii_safe(title or "Document"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_font("Helvetica", "", 10)
    body = (body or "").strip() or "No content."
    pdf.multi_cell(0, 6, _ascii_safe(body), new_x="LMARGIN", new_y="NEXT")
    return pdf.output()


def delivery_scope_pdf(project, scope_content):
    """Return PDF bytes for a project's delivery scope."""
    title = f"Delivery Scope — {project.get('client', '')} / {project.get('name', 'Project')}"
    return _pdf_from_text(title, scope_content)


def uat_signoff_pdf(project, signoff_content):
    """Return PDF bytes for a project's UAT signoff."""
    title = f"UAT Sign-off — {project.get('client', '')} / {project.get('name', 'Project')}"
    return _pdf_from_text(title, signoff_content)


def generic_pdf(title, body):
    """Return PDF bytes for any document (e.g. AI-generated)."""
    return _pdf_from_text(title or "Document", body)


def oncall_monthly_report_pdf(period_label, summary, tickets):
    """Return PDF bytes for oncall monthly report. summary: {total_open, by_status, by_assignee, unassigned_count, oldest_ticket}. tickets: list of {key, summary, status, assignee, updated}."""
    lines = [
        f"Oncall Monthly Report — {period_label}",
        "",
        "Summary (current open)",
        f"  Total open: {summary.get('total_open', 0)}",
        f"  Unassigned: {summary.get('unassigned_count', 0)}",
        "",
    ]
    by_status = summary.get("by_status") or {}
    if by_status:
        lines.append("By status:")
        for s, c in sorted(by_status.items(), key=lambda x: -x[1]):
            lines.append(f"  {s}: {c}")
        lines.append("")
    by_assignee = summary.get("by_assignee") or {}
    if by_assignee:
        lines.append("By assignee:")
        for a, c in sorted(by_assignee.items(), key=lambda x: -x[1]):
            lines.append(f"  {a}: {c}")
        lines.append("")
    oldest = summary.get("oldest_ticket")
    if oldest:
        lines.append(f"Oldest open: {oldest.get('key', '')} — {oldest.get('summary', '')[:50]}…")
        lines.append(f"  Updated: {oldest.get('updated', '')[:10]}")
        lines.append("")
    lines.append("Tickets updated in period")
    lines.append("-" * 40)
    for t in tickets[:80]:
        key = t.get("key", "")
        summary_short = (t.get("summary") or "")[:45]
        if len(t.get("summary") or "") > 45:
            summary_short += "…"
        status = t.get("status", "")
        assignee = t.get("assignee", "—")
        updated = (t.get("updated") or "")[:10]
        lines.append(f"{key}  {status}  {assignee}  {updated}")
        lines.append(f"  {summary_short}")
        lines.append("")
    body = "\n".join(lines)
    return _pdf_from_text(f"Oncall Report — {period_label}", body)
