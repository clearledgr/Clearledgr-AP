"""Transactional email — scheduled report delivery and similar admin
notifications. Distinct from Gmail-API outbound (vendor outreach).

Uses stdlib ``smtplib`` against an env-configured SMTP relay so the
deployment can point at SES / SendGrid / Postmark / a customer-owned
relay without code changes. When SMTP is not configured the service
short-circuits to a no-op + log line, so dev environments and tests
don't fall over.

Required environment for live delivery:

  CLEARLEDGR_SMTP_HOST       e.g. smtp.sendgrid.net
  CLEARLEDGR_SMTP_PORT       e.g. 587
  CLEARLEDGR_SMTP_USERNAME   e.g. apikey
  CLEARLEDGR_SMTP_PASSWORD   the password / API key
  CLEARLEDGR_SMTP_FROM       From: header, e.g. reports@clearledgr.com
  CLEARLEDGR_SMTP_USE_TLS    "true" / "false" (default true on port 587)

Failures are surfaced to the caller so the worker can record + retry.
The function never raises across the public boundary; it returns a
result dataclass with ``ok`` + ``error_message``.
"""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class EmailAttachment:
    filename: str
    content: bytes
    content_type: str = "application/octet-stream"


@dataclass
class EmailDeliveryResult:
    ok: bool
    skipped: bool = False
    error_message: Optional[str] = None


def _smtp_config() -> Tuple[Optional[str], int, Optional[str], Optional[str], Optional[str], bool]:
    host = os.environ.get("CLEARLEDGR_SMTP_HOST", "").strip() or None
    port = int(os.environ.get("CLEARLEDGR_SMTP_PORT", "587") or "587")
    username = os.environ.get("CLEARLEDGR_SMTP_USERNAME", "").strip() or None
    password = os.environ.get("CLEARLEDGR_SMTP_PASSWORD", "")
    from_addr = os.environ.get("CLEARLEDGR_SMTP_FROM", "").strip() or None
    use_tls_raw = os.environ.get("CLEARLEDGR_SMTP_USE_TLS", "").strip().lower()
    if use_tls_raw in ("false", "0", "no"):
        use_tls = False
    else:
        # Default: TLS on for ports 587 / 465; off for 25 unless explicitly enabled.
        use_tls = port in (587, 465)
    return host, port, username, password or None, from_addr, use_tls


def send_transactional_email(
    *,
    to_addr: str,
    subject: str,
    body_text: str,
    body_html: Optional[str] = None,
    attachments: Optional[List[EmailAttachment]] = None,
) -> EmailDeliveryResult:
    """Send a transactional email via the configured SMTP relay.

    Returns a result dataclass with ``ok`` / ``skipped`` / ``error_message``.
    Never raises. ``skipped=True`` means SMTP is not configured and the
    call was a no-op — the worker treats this as a non-failure for the
    failure_count purposes, since not-configured is a deployment state,
    not a delivery miss.
    """
    if not to_addr or "@" not in to_addr:
        return EmailDeliveryResult(ok=False, error_message="invalid recipient")

    host, port, username, password, from_addr, use_tls = _smtp_config()
    if not host or not from_addr:
        logger.info(
            "[transactional_email] SMTP not configured; skipping email to %s "
            "(host=%s from=%s)", to_addr, host, from_addr,
        )
        return EmailDeliveryResult(ok=False, skipped=True)

    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body_text)
    if body_html:
        msg.add_alternative(body_html, subtype="html")

    for att in (attachments or []):
        maintype, _, subtype = att.content_type.partition("/")
        msg.add_attachment(
            att.content,
            maintype=maintype or "application",
            subtype=subtype or "octet-stream",
            filename=att.filename,
        )

    try:
        if port == 465:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, context=context, timeout=15) as smtp:
                if username and password:
                    smtp.login(username, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=15) as smtp:
                if use_tls:
                    smtp.starttls(context=ssl.create_default_context())
                if username and password:
                    smtp.login(username, password)
                smtp.send_message(msg)
    except Exception as exc:
        logger.warning(
            "[transactional_email] delivery to %s failed: %s", to_addr, exc,
        )
        return EmailDeliveryResult(ok=False, error_message=str(exc))

    return EmailDeliveryResult(ok=True)
