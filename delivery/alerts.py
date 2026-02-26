"""
delivery/alerts.py

Instant high-priority alert sender.

Triggers immediately when any deal scores ≥ 85 AND meets all alert conditions.
Sends a full deal summary with why it scored high, all strategy cash flows,
VA loan snapshot, Street View link, and a link to the deal detail page.

Env vars:
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, DIGEST_EMAIL
"""

from __future__ import annotations

import os
import smtplib
from typing import Any

import structlog

from delivery.digest_formatter import build_alert_message

logger = structlog.get_logger(__name__)


def send_alert(
    deal: dict[str, Any],
    recipient: str | None = None,
    base_url: str = "",
) -> None:
    """
    Send an instant high-priority alert for a single deal.

    Args:
        deal:       Deal dict with all scoring and property fields.
        recipient:  Recipient email. Falls back to DIGEST_EMAIL env var.
        base_url:   App base URL for deal links.
    """
    recipient = recipient or os.environ.get("DIGEST_EMAIL", "")
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASSWORD", "")

    if not recipient or not smtp_user or not smtp_pass:
        logger.warning("alert_skip_no_config", has_recipient=bool(recipient))
        return

    cid = deal.get("canonical_id", "")
    deal["app_deal_url"] = f"{base_url}/deal/{cid}"

    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))

    msg = build_alert_message(deal, recipient, smtp_user)

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, recipient, msg.as_string())
        logger.info("alert_sent",
                    address=deal.get("address"),
                    score=deal.get("deal_score"),
                    recipient=recipient)
    except Exception as exc:
        logger.error("alert_send_failed", error=str(exc))
        raise


def maybe_send_alert(
    deal: dict[str, Any],
    threshold: int = 85,
    recipient: str | None = None,
    base_url: str = "",
) -> bool:
    """
    Send alert only if deal meets threshold and is_high_priority.

    Returns:
        True if alert was sent, False otherwise.
    """
    score        = deal.get("deal_score", 0)
    is_hp        = deal.get("is_high_priority", False)

    if score >= threshold and is_hp:
        logger.info("alert_trigger", address=deal.get("address"), score=score)
        send_alert(deal, recipient, base_url)
        return True

    return False
