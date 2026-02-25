"""Slack request signature verification (PLAN.md Section 5.2).

Implements HMAC v0 verification with timestamp replay protection
as required by the Slack API security guidelines.
"""

import hashlib
import hmac
import logging
import os
import time
from typing import Optional

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

# Maximum age of a request in seconds before it's rejected (replay protection)
MAX_REQUEST_AGE_SECONDS = 300  # 5 minutes


def verify_slack_signature(
    signing_secret: str,
    timestamp: str,
    body: bytes,
    signature: str,
) -> bool:
    """Verify a Slack request signature.

    Args:
        signing_secret: The Slack app's signing secret.
        timestamp: The ``X-Slack-Request-Timestamp`` header value.
        body: The raw request body bytes.
        signature: The ``X-Slack-Signature`` header value.

    Returns:
        True if the signature is valid and the request is not a replay.
    """
    # Replay protection
    try:
        ts = int(timestamp)
    except (ValueError, TypeError):
        return False
    if abs(time.time() - ts) > MAX_REQUEST_AGE_SECONDS:
        return False

    # Compute expected signature
    basestring = f"v0:{timestamp}:{body.decode('utf-8')}"
    computed = (
        "v0="
        + hmac.new(
            signing_secret.encode("utf-8"),
            basestring.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
    )
    return hmac.compare_digest(computed, signature)


async def require_slack_signature(request: Request) -> bytes:
    """FastAPI dependency that verifies Slack request signatures.

    Usage::

        @router.post("/slack/events")
        async def slack_events(body: bytes = Depends(require_slack_signature)):
            ...

    Returns the raw body bytes so the caller can parse them.
    """
    signing_secret = os.getenv("SLACK_SIGNING_SECRET", "").strip()
    if not signing_secret:
        logger.error("SLACK_SIGNING_SECRET not configured — rejecting request")
        raise HTTPException(status_code=503, detail="Slack integration not configured")

    timestamp: Optional[str] = request.headers.get("X-Slack-Request-Timestamp")
    signature: Optional[str] = request.headers.get("X-Slack-Signature")
    if not timestamp or not signature:
        raise HTTPException(status_code=401, detail="Missing Slack signature headers")

    body = await request.body()

    if not verify_slack_signature(signing_secret, timestamp, body, signature):
        logger.warning("Invalid Slack signature — possible tampering or replay")
        raise HTTPException(status_code=401, detail="Invalid Slack signature")

    return body
