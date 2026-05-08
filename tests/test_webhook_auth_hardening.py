"""Regression coverage for the webhook-auth hardening pass.

Pre-fix critical bugs surfaced by the webhook audit:

1. ``slack_invoices.py:/events`` — accepted unsigned JSON, letting any
   attacker fan out to ``_handle_mention_reply_sync`` (writes ap_item
   timeline) and ``_handle_conversational_query`` (sends Slack
   messages on the org's bot token). The strict-profile allowlist
   in main.py was the only thing keeping this route 404'd in prod;
   defence-in-depth says fix the route too.

2. ``slack_invoices.py:/interactive`` — derived ``organization_id``
   from the AP item alone. An attacker whose Slack workspace was
   bound to org A could submit a click whose ``value`` JSON
   referenced org B's ``gmail_id`` and have the click execute
   against org B.

3. ``teams_invoices.py:/`` — read ``organization_id`` directly from
   the (untrusted) request body. Anyone holding a valid AAD bot
   token could approve invoices in any tenant by setting the body
   field.

These tests pin all three fixes via source-inspection: the buggy
patterns must NOT reappear, and the new auth/cross-check primitives
must remain. Source-inspection is robust against test-env quirks
(strict-profile filtering, full FastAPI lifecycle) that obscure
live HTTP tests of webhook routes.
"""

from __future__ import annotations


def test_slack_events_route_requires_signature_verification():
    """Pre-fix the /slack/events route called ``await request.json()``
    directly with no ``_require_slack_signature`` call. The fix
    routes the body through the same primitive ``/interactive``
    already uses.
    """
    from clearledgr.api import slack_invoices

    src_path = slack_invoices.__file__
    with open(src_path, "r") as f:
        src = f.read()

    # Find the body of handle_slack_events.
    marker = '@router.post("/events")'
    assert marker in src, "Could not locate /slack/events route in source"
    after_marker = src.split(marker, 1)[1]
    # Snip the next route definition so we only inspect this handler.
    next_route = after_marker.find("@router.")
    handler_body = after_marker[: next_route if next_route > 0 else 4000]

    # Must call _require_slack_signature.
    assert "_require_slack_signature" in handler_body, (
        "/slack/events handler is no longer signature-checking the request. "
        "Pre-fix this allowed anyone to fan out to _handle_mention_reply_sync "
        "and _handle_conversational_query, sending Slack messages on the "
        "org's bot token."
    )
    # Must NOT do the bare ``await request.json()`` pattern that bypassed
    # the verifier. (Inside ``_require_slack_signature`` itself it's fine.)
    assert "body = await request.json()" not in handler_body, (
        "/slack/events handler is parsing the body before verifying the "
        "Slack signature. Order of operations matters — verify first."
    )


def test_slack_interactive_handler_has_team_org_cross_check():
    """Pre-fix the Slack /interactive handler derived org from the AP
    item alone with no check that the click's verified ``team_id``
    is bound to that org. Now the handler must call
    ``get_slack_installation_by_team`` and refuse with 403
    ``tenant_mismatch`` when the team isn't bound to the AP item's
    org."""
    from clearledgr.api import slack_invoices

    src_path = slack_invoices.__file__
    with open(src_path, "r") as f:
        src = f.read()

    assert "get_slack_installation_by_team" in src, (
        "Slack /interactive handler is no longer cross-checking the click's "
        "verified team_id against the AP item's org. That re-opens the "
        "cross-tenant vulnerability where workspace A's user could approve "
        "workspace B's invoices via a forged ``value`` JSON."
    )
    assert "tenant_mismatch" in src, (
        "Slack /interactive handler must emit a tenant_mismatch audit + 403 "
        "on team→org mismatch."
    )


def test_teams_handler_does_not_trust_organization_id_from_body():
    """Pre-fix the Teams handler read ``organization_id`` directly
    from the request body. Now the body field is ignored entirely;
    the org comes from the AP-item resolution. When no AP item
    resolves, the route fails closed with 404 ``ap_item_not_found``.
    """
    from clearledgr.api import teams_invoices

    src_path = teams_invoices.__file__
    with open(src_path, "r") as f:
        src = f.read()

    # The buggy pattern was passing the body's organization_id as the
    # default-org arg to _resolve_ap_context.
    assert 'str(payload.get("organization_id") or "default"),' not in src, (
        "Teams handler is reading organization_id from request body again. "
        "That re-introduces the pre-fix cross-tenant attack: anyone with a "
        "valid AAD bot token can approve invoices in any Solden tenant by "
        "setting the body field."
    )
    # The fail-closed guard must be present.
    assert "no_ap_item_resolution" in src, (
        "Teams handler missing the fail-closed guard for unresolved AP items."
    )
    assert "ap_item_not_found" in src, (
        "Teams handler must return 404 ap_item_not_found when the email "
        "candidate doesn't resolve to a row."
    )


def test_qbo_webhook_dispatch_refuses_realm_id_mismatch():
    """Pre-fix the QBO webhook trusted any ``realmId`` in the
    envelope as long as the URL-scoped org's signature checked out.
    A batched envelope or forged ``realmId`` could route an event
    into the wrong tenant. Now ``_dispatch_quickbooks_bill_intake``
    cross-checks every event's ``realmId`` against the connection's
    expected realm_id and refuses on mismatch.
    """
    from clearledgr.api import erp_webhooks

    src_path = erp_webhooks.__file__
    with open(src_path, "r") as f:
        src = f.read()

    assert "realm_id mismatch" in src, (
        "QBO webhook is no longer cross-checking realmId against the "
        "connection's expected realm_id. That re-opens the cross-tenant "
        "vulnerability where a batched/forged event routes to the wrong "
        "Solden tenant."
    )


def test_xero_webhook_dispatch_refuses_tenant_id_mismatch():
    """Pre-fix the Xero webhook accepted any ``tenantId`` in the
    envelope. Now refuses with a logged warning on mismatch."""
    from clearledgr.api import erp_webhooks

    src_path = erp_webhooks.__file__
    with open(src_path, "r") as f:
        src = f.read()

    assert "tenant_id mismatch" in src, (
        "Xero webhook is no longer cross-checking tenant_id against the "
        "connection's expected tenant_id."
    )


def test_erp_webhook_secret_lookup_distinguishes_db_error_from_not_configured():
    """Pre-fix ``_resolve_webhook_secret`` swallowed exceptions and
    returned ``None``, indistinguishable from "tenant not configured"
    — ERPs retried indefinitely and ops looked in the wrong place.
    Now raises ``_WebhookSecretLookupFailed`` on DB outage; the
    routes map it to HTTP 500. ``None`` stays as 503 ``not configured``.
    """
    from clearledgr.api import erp_webhooks

    src_path = erp_webhooks.__file__
    with open(src_path, "r") as f:
        src = f.read()

    assert "_WebhookSecretLookupFailed" in src, (
        "ERP webhook routes must distinguish 'not configured' from "
        "'lookup failed' so DB outages don't masquerade as missing "
        "tenant configuration."
    )
    # All four ERP routes must catch the new exception.
    qbo_count = src.count("_WebhookSecretLookupFailed")
    assert qbo_count >= 5, (
        f"expected ≥5 _WebhookSecretLookupFailed mentions (def + 4 routes), "
        f"got {qbo_count}"
    )
