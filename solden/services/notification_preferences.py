"""Notification preferences — Module 11 (per-user notification toggles).

The product surfaces three notification channels — email, Slack, and
in-app — and each carries a fixed set of event toggles. The
preferences are stored inside the existing ``users.preferences_json``
blob so we don't grow a new column or table for what is fundamentally
configuration data.

Why a typed schema layer (this module) on top of the generic
preferences store:
  - The UI needs a stable contract; "anything goes" JSON makes the
    settings page brittle.
  - Notification dispatch sites call ``should_notify`` to gate, which
    is one line per call site. A schema-less shape would force every
    caller to spell out the same key path.
  - Adding a new event type is one line in
    ``DEFAULT_NOTIFICATION_PREFS`` — every channel inherits the new
    toggle with its default value.

What is NOT here:
  - The notifications themselves. Slack / email / in-app dispatch
    code lives where it always did. This module just provides the
    `should_notify(user_id, channel, event)` gate.
"""
from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


VALID_CHANNELS = frozenset({"email", "slack", "in_app"})


# Canonical schema. Adding a new event type is a one-line change here;
# every channel inherits the toggle with its default value. Removing
# a key is a breaking-contract change — keep the key and default it
# to False instead, so existing user prefs don't blow up at read time.
DEFAULT_NOTIFICATION_PREFS: Dict[str, Dict[str, bool]] = {
    "email": {
        # Exception was raised on a Box you own / approve / watch.
        "exception_raised": True,
        # An approval was requested of you.
        "approval_requested": True,
        # An approval you submitted was decided (approved / rejected).
        "approval_decided": False,
        # A vendor responded on a thread you own.
        "vendor_response": True,
        # Org-level digest emails (weekly / monthly).
        "weekly_digest": True,
        # Whether scheduled report subscriptions email this user
        # personally (a leader can subscribe themselves; an admin can
        # subscribe an alias). Distinct toggle so a user can keep
        # exception emails on but mute the recurring report.
        "report_subscriptions": True,
    },
    "slack": {
        "exception_raised": True,
        "approval_requested": True,
        "approval_decided": True,
        "vendor_response": False,
    },
    "in_app": {
        "exception_raised": True,
        "approval_requested": True,
        "approval_decided": True,
        "comment_mentions": True,
        "vendor_response": True,
    },
}


def get_default_notification_prefs() -> Dict[str, Dict[str, bool]]:
    """Deep-copy the canonical defaults so callers can mutate safely."""
    return deepcopy(DEFAULT_NOTIFICATION_PREFS)


def merge_with_defaults(
    raw: Optional[Dict[str, Any]],
) -> Dict[str, Dict[str, bool]]:
    """Merge a stored preferences blob with the defaults.

    Stored prefs only contain the toggles the user has flipped; every
    other toggle inherits the canonical default. Channels and toggle
    keys not in the schema are silently dropped — the API enforces the
    schema on write so this is a defense-in-depth scrub.
    """
    out = get_default_notification_prefs()
    if not isinstance(raw, dict):
        return out
    for channel, toggles in raw.items():
        if channel not in VALID_CHANNELS:
            continue
        if not isinstance(toggles, dict):
            continue
        out[channel] = dict(out[channel])  # detach from the prototype
        for event, value in toggles.items():
            if event not in DEFAULT_NOTIFICATION_PREFS[channel]:
                continue
            out[channel][event] = bool(value)
    return out


def load_notification_prefs(
    db: Any, user_id: str,
) -> Dict[str, Dict[str, bool]]:
    """Load a user's notification preferences.

    Returns the merged-with-defaults shape so callers don't have to
    handle the "user has never set this" case.
    """
    if not user_id:
        return get_default_notification_prefs()
    try:
        raw = db.get_user_preferences(user_id) or {}
    except Exception as exc:
        logger.debug(
            "[notification_prefs] load failed for user=%s: %s", user_id, exc,
        )
        return get_default_notification_prefs()
    if not isinstance(raw, dict):
        return get_default_notification_prefs()
    notifications = raw.get("notifications") if isinstance(raw, dict) else None
    return merge_with_defaults(notifications)


def save_notification_prefs(
    db: Any, user_id: str, prefs: Dict[str, Any],
) -> Dict[str, Dict[str, bool]]:
    """Persist a sanitised preferences blob, returning the resolved shape.

    The store mixes the new prefs into the existing
    ``users.preferences_json`` document, preserving anything else
    (workflow filter favourites, table layouts) the user has stored
    there.
    """
    sanitised = merge_with_defaults(prefs)
    try:
        existing = db.get_user_preferences(user_id) or {}
    except Exception as exc:
        logger.debug(
            "[notification_prefs] read-before-write failed: %s", exc,
        )
        existing = {}
    if not isinstance(existing, dict):
        existing = {}
    existing["notifications"] = sanitised
    try:
        db.update_user_preferences(user_id, existing)
    except Exception as exc:
        logger.warning(
            "[notification_prefs] save failed for user=%s: %s", user_id, exc,
        )
    return sanitised


def should_notify(
    db: Any, user_id: str, *, channel: str, event: str,
) -> bool:
    """The gate every notification dispatch site can call.

    Returns True when the user has the (channel, event) toggle
    enabled, or when the event is unknown to the schema (default-
    open: a new code path that hasn't been added to the schema yet
    shouldn't silently drop notifications). Returns False only when
    the user has explicitly opted out OR the channel is not in the
    canonical set.
    """
    if channel not in VALID_CHANNELS:
        return False
    if event not in DEFAULT_NOTIFICATION_PREFS.get(channel, {}):
        # Unknown event — default open. The schema is the source of
        # truth; if a dispatch site uses an event name not in the
        # schema, we'd rather over-notify (and have the operator file
        # a "missing toggle" ticket) than silently drop on them.
        return True
    prefs = load_notification_prefs(db, user_id)
    return bool(prefs.get(channel, {}).get(event, True))
