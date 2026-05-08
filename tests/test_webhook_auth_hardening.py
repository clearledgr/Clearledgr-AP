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


def test_slack_received_lock_short_circuits_concurrent_retry():
    """Pre-fix the Slack /interactive handler had a TOCTOU race on
    its own ``received_key`` audit row: it CHECKED the row before
    dispatch but only WROTE it after dispatch. Two concurrent
    deliveries of the same payload (Slack 3s retry) both passed
    the check, both dispatched, both fired the underlying intent.

    Now the handler writes the received_key sentinel BEFORE dispatch
    via ``_try_acquire_received_lock``, which uses the
    ``audit_events.idempotency_key`` UNIQUE constraint to grant
    exactly one writer the lock. The loser's correlation_id won't
    match the winner's row → returns False → short-circuits as
    duplicate.

    Tests the lock primitive in isolation: simulate the
    unique-constraint by having the second call's correlation_id
    differ from the row's. Real DB integration is exercised by
    the channel-approval contract tests; this asserts the
    primitive's contract.
    """
    from clearledgr.api.slack_invoices import _try_acquire_received_lock

    # First call: row created with correlation_id "corr-A".
    # Second call: same idempotency_key, different correlation_id "corr-B".
    # The store would return the existing winner row (corr-A).
    #
    # We fake the store by recording the first-write's correlation_id
    # and returning it on every subsequent get_ap_audit_event_by_key.
    state: dict = {"row": None}

    class _FakeDB:
        def append_audit_event(self_inner, payload):
            # First write wins; subsequent writes are no-ops in our fake
            # (the real store would raise UniqueViolation, but
            # ``_try_acquire_received_lock`` only cares about what
            # ``get_ap_audit_event_by_key`` returns afterwards).
            if state["row"] is None:
                state["row"] = dict(payload)

        def get_ap_audit_event_by_key(self_inner, key):
            return state["row"]

    fake_db = _FakeDB()

    # First request acquires the lock.
    won_first = _try_acquire_received_lock(
        fake_db,
        idempotency_key="key-XYZ",
        organization_id="org_test",
        ap_item_id="ap-1",
        actor_id="alice@co",
        correlation_id="corr-A",
        source="slack",
        metadata={},
    )
    assert won_first is True, "first request must acquire the lock"

    # Concurrent retry with the SAME idempotency_key but different
    # correlation_id — must lose, return False.
    won_second = _try_acquire_received_lock(
        fake_db,
        idempotency_key="key-XYZ",
        organization_id="org_test",
        ap_item_id="ap-1",
        actor_id="alice@co",
        correlation_id="corr-B",
        source="slack",
        metadata={},
    )
    assert won_second is False, (
        "second request with different correlation_id must lose the lock "
        "(short-circuits as duplicate); got True which would let the "
        "concurrent retry double-dispatch."
    )


def test_gmail_register_token_refuses_unprovisioned_email():
    """Pre-fix the Gmail extension's /register-token endpoint
    auto-provisioned ANY new email into ``org_id="default"`` when
    the email's domain didn't map to any org. Combined with a
    backend JWT mint (``create_access_token``), an attacker with a
    personal Gmail + valid Google OAuth token received a Solden
    session in the literal "default" org — cross-tenant write
    access if any tenant happened to have id="default" (or via
    test fixtures).

    Now both ``/gmail/register-token`` and ``/gmail/exchange-code``
    refuse with HTTP 403 ``unprovisioned_email`` when the email's
    domain has no mapped org. Auto-provisioning still works for
    domain-matched emails (legitimate org bootstrap path).
    """
    from clearledgr.api import gmail_extension

    src_path = gmail_extension.__file__
    with open(src_path, "r") as f:
        src = f.read()

    # The buggy bootstrap fallback log message must NOT exist any
    # more. (The phrase ``resolved_org_id = ... or "default"`` on
    # post-provision lines is a separate concern — those read from
    # an existing user row that has data-corruption signals; the
    # auto-provision path is what the audit flagged.)
    assert 'using default' not in src, (
        "'using default' fallback log message present — the unmappable-"
        "domain auto-provision path is still creating users in the "
        "default org. That's the cross-tenant landmine the audit flagged."
    )

    # The fail-closed guard must be present.
    assert "unprovisioned_email" in src, (
        "Gmail extension must reject unprovisioned emails with "
        "HTTP 403 unprovisioned_email — auto-provisioning into "
        "'default' is the cross-tenant attack."
    )
    # Both endpoints must have the guard (register-token + exchange-code).
    assert src.count("unprovisioned_email") >= 2, (
        "Both /gmail/register-token and /gmail/exchange-code must guard "
        "against unprovisioned domains; only one site has the check."
    )
