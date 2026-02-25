"""Safe error formatting for HTTP responses.

Never expose stack traces, exception class names, or internal paths
to API callers. Instead, log the full error server-side with a
correlation ID and return only the ID to the caller.
"""

import logging
import uuid

logger = logging.getLogger(__name__)


def safe_error(exc: Exception, context: str = "") -> str:
    """Return a user-safe error message and log the real error.

    Usage::

        except Exception as e:
            raise HTTPException(status_code=500, detail=safe_error(e, "posting to ERP"))
    """
    error_id = uuid.uuid4().hex[:8]
    logger.error(
        "error_id=%s context=%s exception=%s",
        error_id,
        context or "unspecified",
        exc,
        exc_info=True,
    )
    if context:
        return f"Internal error in {context} (ref: {error_id})"
    return f"Internal error (ref: {error_id})"
