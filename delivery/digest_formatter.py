"""
delivery/digest_formatter.py

Shared email formatting logic used by both daily digest and instant alerts.
Produces plain text + HTML multipart messages.
"""

from __future__ import annotations

import email.mime.multipart
import email.mime.text
from datetime import datetime
from typing import Any


def _fmt_cf(cf: float) -> str:
    return f"${cf:,.0f}/mo"


def _score_label(score: int) -> str:
    if score >= 85:
        return f"🔥 {score}/100"
    if score >= 60:
        return f"✅ {score}/100"
    return f"⚠ {score}/100"


# ── Plain text card ────────────────────────────────────────────────────────────

def deal_card_text(deal: dict[str, Any]) -> str:
    """Render a single deal as plain text."""
    lines = [
        "=" * 60,
        deal.get("address", "Unknown address"),
        f"  Price:       ${deal.get('price', 0):,.0f}",
        f"  Type:        {deal.get('property_type', '—')} | {deal.get('num_units', '?')} units",
        f"  Deal Score:  {_score_label(deal.get('deal_score', 0))}",
        f"  Return:      {deal.get('return_score', '—')}/40  |  "
        f"Risk: {deal.get('risk_score', '—')}/30  |  "
        f"Confidence: {deal.get('confidence_score_pts', '—')}/30",
    ]

    for strat in deal.get("strategies", [])[:3]:
        lines.append(f"  {strat['name']}: {_fmt_cf(strat['cash_flow'])} | DSCR {strat['dscr']:.2f}")

    if deal.get("why_scored_high"):
        lines.append(f"\n  {deal['why_scored_high']}")

    if deal.get("maps_link"):
        lines.append(f"\n  View deal: {deal.get('app_deal_url', '')}")

    return "\n".join(lines)


# ── HTML card ─────────────────────────────────────────────────────────────────

def deal_card_html(deal: dict[str, Any]) -> str:
    """Render a single deal as an HTML table row / card."""
    score    = deal.get("deal_score", 0)
    color    = "#e53e3e" if score >= 85 else ("#38a169" if score >= 60 else "#d69e2e")
    cf_color = "#38a169" if deal.get("top_cash_flow", 0) >= 500 else "#e53e3e"

    strategies_html = ""
    for s in deal.get("strategies", [])[:3]:
        cf_c = "#38a169" if s.get("cash_flow", 0) >= 500 else "#e53e3e"
        strategies_html += (
            f'<td style="padding:6px 10px;border:1px solid #e2e8f0;font-size:12px">'
            f'<strong>{s.get("name","")}</strong><br>'
            f'<span style="color:{cf_c};font-weight:700">${s.get("cash_flow",0):,.0f}/mo</span><br>'
            f'DSCR {s.get("dscr",0):.2f}'
            f'</td>'
        )

    deal_url = deal.get("app_deal_url", "#")
    sv_url   = deal.get("street_view_url", "")
    sv_img   = (f'<img src="{sv_url}" width="200" height="130" style="border-radius:4px;display:block">'
                if sv_url else "")

    return f"""
<table width="100%" cellpadding="0" cellspacing="0"
       style="margin-bottom:20px;background:#fff;border:1px solid #e2e8f0;border-radius:8px;overflow:hidden">
  <tr>
    <td style="padding:16px;border-bottom:1px solid #e2e8f0;background:#f7fafc">
      <table width="100%"><tr>
        <td>
          <a href="{deal_url}" style="color:#1a2744;font-size:16px;font-weight:700;text-decoration:none">
            {deal.get('address','—')}
          </a><br>
          <span style="color:#718096;font-size:12px">
            {deal.get('property_type','—')} &middot; {deal.get('num_units','?')} units &middot; {deal.get('zip_code','')}
          </span>
        </td>
        <td align="right">
          <span style="font-size:22px;font-weight:800;color:#1a2744">${deal.get('price',0):,.0f}</span><br>
          <span style="display:inline-block;padding:3px 10px;border-radius:12px;font-weight:700;
                       background:{color}22;color:{color};font-size:13px">
            {_score_label(score)}
          </span>
        </td>
      </tr></table>
    </td>
  </tr>
  <tr>
    <td style="padding:12px 16px">
      <table><tr>{strategies_html}</tr></table>
      {f'<p style="margin-top:10px;color:#4a5568;font-size:12px;font-style:italic">{deal.get("why_scored_high","")}</p>' if deal.get("why_scored_high") else ''}
      <div style="margin-top:10px">
        <a href="{deal_url}" style="color:#4299e1;font-size:12px">View full deal →</a>
        {f' &nbsp; <a href="{deal.get(\"maps_link\",\"\")}\" style="color:#4299e1;font-size:12px">Google Maps →</a>' if deal.get("maps_link") else ''}
      </div>
    </td>
  </tr>
</table>"""


# ── Message builders ───────────────────────────────────────────────────────────

def build_digest_message(
    deals: list[dict[str, Any]],
    recipient: str,
    sender: str,
) -> email.mime.multipart.MIMEMultipart:
    """Build a daily digest MIMEMultipart email."""
    alert_count = sum(1 for d in deals if d.get("is_high_priority"))
    subject = (
        f"JAX Analyzer — {len(deals)} deal{'s' if len(deals) != 1 else ''}"
        + (f", {alert_count} alert{'s' if alert_count != 1 else ''}" if alert_count else "")
        + f" — {datetime.now().strftime('%b %d')}"
    )

    text_body  = f"JAX Analyzer Daily Digest — {datetime.now().strftime('%A, %B %d, %Y')}\n\n"
    text_body += f"{len(deals)} deal(s) scoring 60+ today\n"
    if alert_count:
        text_body += f"🔥 {alert_count} HIGH PRIORITY alert(s)\n"
    text_body += "\n"
    for deal in deals:
        text_body += deal_card_text(deal) + "\n\n"

    html_body = f"""<!DOCTYPE html><html><body style="font-family:-apple-system,sans-serif;max-width:700px;margin:0 auto;padding:20px;background:#f5f5f5">
<h1 style="font-size:20px;color:#1a2744">JAX Analyzer — Daily Digest</h1>
<p style="color:#718096">{datetime.now().strftime('%A, %B %d, %Y')}</p>
<p style="color:#4a5568">{len(deals)} deal(s) scoring 60+ &nbsp;·&nbsp;
{'🔥 ' + str(alert_count) + ' HIGH PRIORITY' if alert_count else 'No alerts'}</p>
<hr style="border:none;border-top:1px solid #e2e8f0;margin:16px 0">
{"".join(deal_card_html(d) for d in deals)}
<p style="color:#a0aec0;font-size:11px;margin-top:20px">
  JAX Analyzer &middot; Settings: {deals[0].get('app_base_url','')}/settings
</p>
</body></html>"""

    return _build_mime(subject, text_body, html_body, sender, recipient)


def build_alert_message(
    deal: dict[str, Any],
    recipient: str,
    sender: str,
) -> email.mime.multipart.MIMEMultipart:
    """Build a high-priority instant alert MIMEMultipart email."""
    score   = deal.get("deal_score", 0)
    address = deal.get("address", "Unknown")
    subject = f"🔥 HIGH PRIORITY — {address} scored {score}/100"

    text_body = (
        f"HIGH PRIORITY ALERT\n{'='*60}\n\n"
        + deal_card_text(deal)
        + "\n\nAll alert conditions met: "
        + ", ".join(deal.get("alert_reasons_passed", []))
        + "\n\nView full analysis: " + deal.get("app_deal_url", "")
    )

    html_body = f"""<!DOCTYPE html><html><body style="font-family:-apple-system,sans-serif;max-width:700px;margin:0 auto;padding:20px">
<div style="background:#fff5f5;border:2px solid #e53e3e;border-radius:8px;padding:16px;margin-bottom:20px">
  <h1 style="color:#e53e3e;font-size:20px;margin:0">🔥 HIGH PRIORITY ALERT</h1>
  <p style="color:#c53030;margin:4px 0 0 0">{address} scored {score}/100</p>
</div>
{deal_card_html(deal)}
<p style="color:#a0aec0;font-size:11px;margin-top:20px">
  Sent by JAX Analyzer
</p>
</body></html>"""

    return _build_mime(subject, text_body, html_body, sender, recipient)


def _build_mime(
    subject: str,
    text_body: str,
    html_body: str,
    sender: str,
    recipient: str,
) -> email.mime.multipart.MIMEMultipart:
    msg = email.mime.multipart.MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = sender
    msg["To"]      = recipient
    msg.attach(email.mime.text.MIMEText(text_body, "plain"))
    msg.attach(email.mime.text.MIMEText(html_body, "html"))
    return msg
