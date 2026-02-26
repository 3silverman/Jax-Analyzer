"""
delivery/email_digest.py

Daily email digest sender.

Sends at 7:00 AM ET (after 6:00 AM scan).
Lists all deals scoring 60+ with address, top strategy, cash flow,
Deal Score, and deal detail link.

Env vars:
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, DIGEST_RECIPIENT_EMAIL
"""

from __future__ import annotations

import os
import smtplib
from typing import Any

import structlog

from delivery.digest_formatter import build_digest_message

logger = structlog.get_logger(__name__)


def _smtp_config() -> dict:
    return {
        "host":     os.environ.get("SMTP_HOST", "smtp.gmail.com"),
        "port":     int(os.environ.get("SMTP_PORT", "587")),
        "user":     os.environ.get("SMTP_USER", ""),
        "password": os.environ.get("SMTP_PASSWORD", ""),
    }


def send_digest(
    all_deals: list[dict[str, Any]],
    recipient: str | None = None,
    base_url: str = "",
) -> None:
    """
    Send the daily digest email.

    Filters to deals with deal_score ≥ 60. Does nothing if no qualifying deals
    or if SMTP credentials are not configured.

    Args:
        all_deals:  Full list of deal dicts from the latest scan.
        recipient:  Recipient email address. Falls back to DIGEST_RECIPIENT_EMAIL env var.
        base_url:   App base URL for deal links (e.g. https://jax-analyzer.onrender.com).
    """
    recipient = recipient or os.environ.get("DIGEST_RECIPIENT_EMAIL", "")
    if not recipient:
        logger.info("digest_skip_no_recipient")
        return

    smtp = _smtp_config()
    if not smtp["user"] or not smtp["password"]:
        logger.warning("digest_skip_no_smtp_credentials")
        return

    qualifying = [d for d in all_deals if d.get("deal_score", 0) >= 60]
    qualifying.sort(key=lambda d: d.get("deal_score", 0), reverse=True)

    if not qualifying:
        logger.info("digest_skip_no_qualifying_deals")
        return

    # Inject deal links
    for deal in qualifying:
        cid = deal.get("canonical_id", "")
        deal["app_deal_url"] = f"{base_url}/deal/{cid}"
        deal["app_base_url"] = base_url

    msg = build_digest_message(qualifying, recipient, smtp["user"])

    try:
        with smtplib.SMTP(smtp["host"], smtp["port"]) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp["user"], smtp["password"])
            server.sendmail(smtp["user"], recipient, msg.as_string())
        logger.info("digest_sent", recipient=recipient, deals=len(qualifying))
    except Exception as exc:
        logger.error("digest_send_failed", error=str(exc))
        raise
