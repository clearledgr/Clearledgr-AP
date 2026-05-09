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


def test_gmail_webhook_existing_item_initialized_before_inner_try():
    """Pre-fix gmail_webhooks.py:1050 referenced ``existing_item``
    that was only assigned inside an inner try block. When the
    inner try raised (no thread on the message, transient DB
    failure on get_ap_item_by_thread), ``existing_item`` was
    undefined → ``NameError`` → silently swallowed by the outer
    except → every per-message branch fell through to inline LLM
    processing in the worker, an architecture the docstring
    forbids. Initialising to ``None`` before the inner try makes
    the fall-through explicit + correct.
    """
    from clearledgr.api import gmail_webhooks

    src_path = gmail_webhooks.__file__
    with open(src_path, "r") as f:
        src = f.read()

    # The fix initialises existing_item BEFORE the inner try block.
    assert "existing_item = None" in src, (
        "gmail_webhooks.py is no longer initialising existing_item before "
        "the inner try. That re-introduces the NameError fall-through bug."
    )
    # The reference site uses (existing_item or {}) defensively.
    assert "(existing_item or {}).get(\"vendor_name\"" in src, (
        "gmail_webhooks.py is reading existing_item.get without the "
        "(existing_item or {}) None-guard."
    )


def test_gmail_push_route_verifies_before_parsing_json():
    """Pre-fix gmail_webhooks.py:/push parsed the JSON body BEFORE
    calling ``_enforce_push_verifier``. An attacker could DoS the
    api fleet by sending an enormous body — the JSON parser ran on
    every request, even unsigned ones. Now the verifier runs first;
    a defensive body-size cap (64KB) blocks oversize requests
    before they reach the JSON parse.
    """
    from clearledgr.api import gmail_webhooks

    src_path = gmail_webhooks.__file__
    with open(src_path, "r") as f:
        src = f.read()

    # Find the body of the /push handler.
    marker = '@router.post("/push")'
    assert marker in src, "Could not locate /gmail/push route"
    after_marker = src.split(marker, 1)[1]
    next_route = after_marker.find("@router.")
    push_body = after_marker[: next_route if next_route > 0 else 4000]

    # _enforce_push_verifier MUST come before request.json() in the function body.
    verify_pos = push_body.find("_enforce_push_verifier(request)")
    parse_pos = push_body.find("body = await request.json()")
    assert verify_pos > 0, "/gmail/push handler missing _enforce_push_verifier call"
    assert parse_pos > 0, "/gmail/push handler missing body parse"
    assert verify_pos < parse_pos, (
        "/gmail/push parses JSON before verifying signature. That re-introduces "
        f"the DoS where unsigned bodies are parsed. verify_pos={verify_pos} parse_pos={parse_pos}"
    )
    # Body-size cap must be present.
    assert "gmail_push_body_too_large" in push_body, (
        "/gmail/push missing the 64KB body-size cap that prevents JSON-parser DoS."
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


def test_erp_oauth_routes_never_accept_org_from_url_or_body():
    """Pre-fix the ERP OAuth surface accepted ``organization_id`` from
    the URL (authorize/disconnect/status/refresh callbacks) and from
    request bodies (NetSuite/SAP connect). Any user from tenant A
    could attach freshly-issued QuickBooks/Xero tokens — or
    NetSuite/SAP credentials — to tenant B's connection record by
    passing the target org. This is a direct cross-tenant credential
    attack.

    Fix: org is derived from ``Depends(get_current_user)`` everywhere;
    OAuth state is bound to ``user_id`` and re-checked at callback so a
    leaked state cannot be redeemed by a different session.
    """
    from clearledgr.api import erp_oauth

    src_path = erp_oauth.__file__
    with open(src_path, "r") as f:
        src = f.read()

    # The buggy patterns must NOT reappear:
    forbidden_patterns = [
        "organization_id: str = Query(...)",
        'organization_id: str = Query(default="default")',
        "organization_id: str = Query(default=\"default\")",
    ]
    for pat in forbidden_patterns:
        assert pat not in src, (
            f"Forbidden pattern still present in erp_oauth.py: {pat!r}. "
            "ERP OAuth routes must derive organization_id from the "
            "authenticated session, never from the URL."
        )

    # The fail-closed helper must be present.
    assert "_require_session_org" in src, (
        "_require_session_org helper missing — disconnect/status/refresh/"
        "netsuite/sap routes must derive org from the authenticated user."
    )

    # Pydantic body models must NOT declare organization_id as a field
    # any more. (The audit-flagged pre-fix shape was ``organization_id: str``
    # on NetSuiteConnectRequest and SAPConnectRequest.)
    forbidden_field = "organization_id: str"
    netsuite_block = src.split("class NetSuiteConnectRequest", 1)[1].split("class ", 1)[0]
    assert forbidden_field not in netsuite_block, (
        "NetSuiteConnectRequest still declares 'organization_id: str' — "
        "an authenticated user could attach NetSuite credentials to a "
        "different tenant by setting this field."
    )
    sap_block = src.split("class SAPConnectRequest", 1)[1].split("class ", 1)[0]
    assert forbidden_field not in sap_block, (
        "SAPConnectRequest still declares 'organization_id: str' — "
        "same cross-tenant credential attack as NetSuite."
    )

    # Callbacks must verify state-org and state-user against the session.
    assert "oauth_state_org_mismatch" in src, (
        "OAuth callbacks must reject when state's organization_id does "
        "not match the authenticated user's org — leaked-state replay."
    )
    assert "oauth_state_user_mismatch" in src, (
        "OAuth callbacks must reject when state's user_id does not "
        "match the authenticated session — leaked-state cross-user replay."
    )


def test_create_paths_fail_closed_on_missing_organization_id():
    """M4: three create-paths used to log a warning then silently fall
    back to a literal ``"default"`` tenant when the caller forgot to
    pass ``organization_id``:

    - ``payment_store.create_payment``
    - ``auth_store.save_google_auth_code``
    - ``ap_store.create_agent_retry_job``

    The fallback was a cross-tenant landmine: any payload that lost
    its org along the way silently wrote into a shared bucket. A
    ``"default"``-bound auth code redeemed against the auth surface
    produced a session in the wrong tenant; a ``"default"``-bound
    retry job resumed an AP workflow under the wrong tenant.

    Each store now raises ``ValueError`` when org is missing/empty.
    This test pins that contract by inspecting the source so a future
    regression that re-introduces the literal ``"default"`` fallback
    on these three call paths fails the test immediately.
    """
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent
    cases = [
        (
            repo_root / "clearledgr" / "core" / "stores" / "payment_store.py",
            "create_payment",
        ),
        (
            repo_root / "clearledgr" / "core" / "stores" / "auth_store.py",
            "save_google_auth_code",
        ),
        (
            repo_root / "clearledgr" / "core" / "stores" / "ap_store.py",
            "create_agent_retry_job",
        ),
    ]
    import re

    # The buggy fallback shape was specifically ``... or "default"``
    # (or ``or 'default'``) used to coerce a missing org. Comments and
    # docstrings legitimately mention the word "default" — match on
    # the executable ``or`` coercion only.
    or_default = re.compile(r"""or\s+["']default["']""")

    for path, fn_name in cases:
        text = path.read_text()
        marker = f"def {fn_name}"
        assert marker in text, f"could not find {fn_name} in {path}"
        body = text.split(marker, 1)[1].split("\n    def ", 1)[0]
        assert not or_default.search(body), (
            f"{fn_name} in {path.name} still contains an "
            f"``or 'default'`` fallback. Cross-tenant landmine: any "
            f"payload that loses its org silently writes to a shared "
            f"bucket."
        )
        assert "raise ValueError" in body or "raise HTTPException" in body, (
            f"{fn_name} in {path.name} must fail closed on a missing "
            f"organization_id (raise ValueError / HTTPException), not "
            f"silently coerce to a default tenant."
        )

    # The HTTP-layer wrapper must also fail closed.
    auth_path = repo_root / "clearledgr" / "api" / "auth.py"
    auth_src = auth_path.read_text()
    issue_body = auth_src.split("def _issue_google_auth_code", 1)[1].split("\ndef ", 1)[0]
    assert not or_default.search(issue_body), (
        "_issue_google_auth_code in api/auth.py still contains an "
        "``or 'default'`` fallback. An auth code redeemed under "
        "'default' produces a session in the wrong tenant."
    )
    assert "raise HTTPException" in issue_body, (
        "_issue_google_auth_code must raise HTTPException on a missing "
        "organization_id, not silently coerce."
    )


def test_slack_runtime_per_org_fallback_off_by_default():
    """Tier 2: ``resolve_slack_runtime`` had
    ``SLACK_ALLOW_SHARED_FALLBACK`` defaulting to ``"true"``. A
    freshly-onboarded tenant whose Slack installation hadn't
    completed silently ran on the platform-wide bot token — every
    message looked like the platform was speaking on behalf of the
    tenant, and incoming Slack interactions sent to the platform bot
    landed without a clear ``team_id``→``organization_id`` mapping.
    Effectively every un-installed tenant shared a Slack identity.

    Plus the same M4 ``or "default"`` coercion on the
    ``organization_id`` field — a missing org silently bound the
    runtime to the literal "default" tenant.

    The default is now ``"false"``: per_org mode requires an
    org-specific installation, missing → ``connected=False``. The
    coercion is gone — a missing org returns ``None``.
    """
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent
    src = (repo_root / "clearledgr" / "services" / "slack_api.py").read_text()
    body = src.split("def resolve_slack_runtime", 1)[1].split("\ndef ", 1)[0]

    assert 'SLACK_ALLOW_SHARED_FALLBACK", "true"' not in body, (
        "resolve_slack_runtime still defaults SLACK_ALLOW_SHARED_FALLBACK "
        "to 'true' — that's the cross-tenant landmine the audit flagged. "
        "Operators must opt in explicitly via the env var."
    )
    assert 'SLACK_ALLOW_SHARED_FALLBACK", "false"' in body, (
        "resolve_slack_runtime must default SLACK_ALLOW_SHARED_FALLBACK "
        "to 'false' so per_org mode fails closed without an explicit "
        "org installation."
    )
    assert 'organization_id or "default"' not in body, (
        "resolve_slack_runtime still coerces missing org to 'default' "
        "— same M4 landmine. Should return organization_id=None."
    )


def test_ops_autopilot_status_does_not_silently_escalate_to_default_org():
    """M11 (post-codex-review CRITICAL): ``api/ops.py:get_autopilot_status``
    pre-fix did
    ``org_id = str(getattr(_user, "organization_id", "default") or "default")``
    then ``get_platform_finance_runtime(org_id)``. A session whose
    organization_id was missing/empty silently obtained an
    ``is_platform=True`` runtime keyed to the legacy ``"default"`` org —
    surfaced other tenants' agent-skill readiness + pending retry job
    state on the response, plus granted cross-tenant dispatch privilege
    if any later call dispatched.

    Fix: derive the org via ``_assert_org_access(_user, ...)`` which
    fail-closes (403 ``user_missing_organization_id``) when the session
    has no org. Pinned via source inspection because the M10 platform-
    privilege gate is the load-bearing invariant.
    """
    from pathlib import Path
    import re

    repo_root = Path(__file__).resolve().parent.parent
    src = (repo_root / "clearledgr" / "api" / "ops.py").read_text()
    # Strip docstrings + comments before scanning.
    body = re.sub(r'""".*?"""', "", src, flags=re.DOTALL)
    body = re.sub(r"#.*$", "", body, flags=re.MULTILINE)
    bad = re.compile(
        r'getattr\(\s*_?user\s*,\s*["\']organization_id["\']\s*,\s*["\']default["\']\s*\)'
    )
    assert not bad.search(body), (
        "api/ops.py still defaults the user's organization_id to "
        "'default' before constructing the platform finance runtime. "
        "That re-arms the M10 cross-tenant escalation."
    )


def test_ap_item_task_routes_check_user_org_against_task_org():
    """M12 (post-codex-review CRITICAL): the three task-mutation routes
    (``/tasks/{task_id}/status`` / ``/assign`` / ``/comments``) pre-fix
    derived org from ``task.organization_id`` via
    ``_resolve_task_owner_item`` and never compared it to
    ``user.organization_id``. A user from Tenant A could submit any
    Tenant B task_id and update its status, reassign it, or comment.

    The fix routes all three through ``_require_task_in_session_org``
    which asserts ``task.organization_id == _session_org(user)`` BEFORE
    any side-effecting call. 404 (not 403) on mismatch so existence of
    tasks in other tenants doesn't leak.
    """
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent
    src = (repo_root / "clearledgr" / "api" / "ap_items_action_routes.py").read_text()

    assert "def _require_task_in_session_org" in src, (
        "ap_items_action_routes.py must define "
        "_require_task_in_session_org(db, task_id, user) — the helper "
        "that fail-closes when task.org != user.org."
    )
    helper_body = src.split("def _require_task_in_session_org", 1)[1].split("\ndef ", 1)[0]
    assert '_session_org(user)' in helper_body, (
        "_require_task_in_session_org must derive org via _session_org(user), "
        "not from the task row itself."
    )
    assert 'task.get("organization_id")' in helper_body and "!=" in helper_body, (
        "_require_task_in_session_org must compare task.organization_id "
        "against the session-derived org and refuse on mismatch."
    )

    # Each of the 3 task routes must call the helper before any mutation.
    for route_marker in (
        "/tasks/{task_id}/status",
        "/tasks/{task_id}/assign",
        "/tasks/{task_id}/comments",
    ):
        route_body = src.split(route_marker, 1)[1].split("\n@router.", 1)[0]
        assert "_require_task_in_session_org" in route_body, (
            f"Route {route_marker} must call _require_task_in_session_org "
            f"before any task mutation — pre-fix it relied on "
            f"_resolve_task_owner_item which used task.org, not user.org."
        )


def test_finance_runtime_platform_privilege_gated_by_explicit_flag():
    """Tier 2: ``FinanceAgentRuntime`` previously gated cross-tenant
    write privilege on ``self.organization_id == "default"`` — a
    string-comparison sentinel that any code path constructing a
    runtime under the legacy ``"default"`` org silently inherited.
    The M4/M6/M7/M8 ``"default"`` fallback landmines all fed into
    this; closing those didn't fully remove the privilege bypass —
    any one new caller forgetting to thread the org through could
    revive it.

    The privilege gate is now an explicit ``is_platform: bool``
    keyword argument. ``"default"`` carries no special meaning on
    its own. Only ``get_platform_finance_runtime`` (the sanctioned
    constructor) passes ``is_platform=True``.

    This test exercises the runtime directly to confirm:
      1. A regular runtime constructed with
         ``organization_id="default"`` does NOT get cross-tenant
         dispatch privilege.
      2. Only a runtime with ``is_platform=True`` permits a
         cross-tenant payload to flow through ``_resolve_payload_org``.
    """
    from clearledgr.services.finance_agent_runtime import FinanceAgentRuntime

    # 1. ``organization_id="default"`` alone does NOT grant privilege.
    tenant_runtime = FinanceAgentRuntime(
        organization_id="default",
        actor_id="alice@acme",
        actor_email="alice@acme",
    )
    assert tenant_runtime.is_platform is False, (
        "FinanceAgentRuntime constructed with organization_id='default' "
        "must NOT auto-escalate to platform privilege — that's the "
        "string-sentinel bypass the audit flagged."
    )
    # A cross-tenant payload must be rejected even on a "default" runtime.
    import pytest as _pytest
    with _pytest.raises(ValueError, match="cross_tenant_write_blocked"):
        tenant_runtime._resolve_payload_org(
            {"organization_id": "victim-org"}, "test_context"
        )

    # 2. Explicit ``is_platform=True`` permits cross-tenant dispatch.
    platform_runtime = FinanceAgentRuntime(
        organization_id="default",
        actor_id="system",
        actor_email="system@clearledgr.local",
        is_platform=True,
    )
    assert platform_runtime.is_platform is True
    resolved = platform_runtime._resolve_payload_org(
        {"organization_id": "real-tenant"}, "test_context"
    )
    assert resolved == "real-tenant", (
        "Platform runtime must permit cross-tenant payload dispatch — "
        "that's the legitimate use case (startup/background flows "
        "fanning into real tenants)."
    )

    # 3. ``get_platform_finance_runtime`` is the sanctioned constructor
    # for the privileged form.
    from clearledgr.services.finance_agent_runtime import (
        get_platform_finance_runtime,
        _reset_platform_finance_runtime_cache,
    )
    _reset_platform_finance_runtime_cache()
    sanctioned = get_platform_finance_runtime("default")
    assert sanctioned.is_platform is True


def test_celery_load_box_state_blocks_cross_tenant_box_id():
    """Tier 2: ``celery_tasks._load_box_state`` fetched
    ``db.get_ap_item(box_id)`` purely by primary key when the event
    payload carried a ``box_id`` / ``ap_item_id``. A poisoned event
    or queue-routing bug carrying ``organization_id=tenant_A`` plus
    ``box_id`` from ``tenant_B`` would have the planner receive
    tenant B's row as the box state and the coordination engine
    execute the event under tenant A's runtime against tenant B's
    data. The thread_id path was already org-scoped; the box_id
    path now has a post-fetch organization_id check that fails
    closed on mismatch (returns empty box state and logs the
    mismatch as an error so the queue-routing bug surfaces).
    """
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent
    src = (repo_root / "clearledgr" / "services" / "celery_tasks.py").read_text()
    body = src.split("def _load_box_state", 1)[1].split("\ndef ", 1)[0]

    # Must compare row's org against event's org before returning.
    assert "row_org == event_org" in body, (
        "_load_box_state must compare the row's organization_id "
        "against the event's organization_id before returning the "
        "row — pre-fix it returned by box_id alone, a cross-tenant "
        "box-state leak."
    )
    # Must surface the mismatch as an explicit error.
    assert "cross-tenant box-state mismatch" in body, (
        "_load_box_state must log cross-tenant mismatches as errors "
        "rather than silently treating the event as having no prior "
        "box state — silence hides the queue-routing bug."
    )


def test_ap_items_action_routes_no_query_org_no_default_fallback():
    """Tier 1B: ``ap_items_action_routes.py`` accepted
    ``organization_id`` from the URL on every mutating route, then
    threaded it through ``or "default"`` fallback chains down to
    FinanceAgentRuntime construction. A user from Tenant A could
    reverse, snooze, classify, or bulk-mutate Tenant B's AP items by
    passing ``?organization_id=Tenant_B`` if any one of the
    ``or "default"`` fallbacks latched onto the literal "default"
    tenant.

    Worse: the ``/{ap_item_id}/classify`` route called
    ``verify_org_access(user, organization_id)`` with the arguments
    swapped — the deps helper signature is
    ``(claimed_org_id, user)``. With a TokenData as ``claimed_org_id``
    and a string as ``user``, the assertion silently passed every
    time. That was the same B1 anti-pattern at a different layer.

    This test pins:
      1. no ``Query(default="default")`` parameters anywhere in the
         file,
      2. no ``or "default"`` coercions in executable code,
      3. the ``_session_org`` helper exists and fails closed when
         the session has no organization_id.
    """
    from pathlib import Path
    import re

    repo_root = Path(__file__).resolve().parent.parent
    src = (repo_root / "clearledgr" / "api" / "ap_items_action_routes.py").read_text()

    assert 'Query(default="default")' not in src, (
        "ap_items_action_routes.py still has Query(default=\"default\") "
        "parameters. Org must be derived from the authenticated session, "
        "not from the URL."
    )

    or_default = re.compile(r"""or\s+["']default["']""")
    # Strip docstrings so their prose mentions of "or 'default'" don't
    # false-positive — re.DOTALL lets the inner ``.`` match newlines.
    body_only = re.sub(r'"""(.*?)"""', "", src, flags=re.DOTALL)
    matches = or_default.findall(body_only)
    assert not matches, (
        f"ap_items_action_routes.py still contains executable "
        f"``or 'default'`` fallbacks ({len(matches)} occurrences). "
        f"Cross-tenant landmine: any payload that loses its org "
        f"silently writes to a shared bucket."
    )

    assert "def _session_org" in src, (
        "ap_items_action_routes.py must define _session_org(user) "
        "that fails closed when the session has no organization_id."
    )
    helper_body = src.split("def _session_org", 1)[1].split("\ndef ", 1)[0]
    assert "user_missing_organization_id" in helper_body, (
        "_session_org must raise 403 user_missing_organization_id "
        "when the session has no org."
    )


def test_ops_assert_org_access_has_no_role_bypass_and_no_default_fallback():
    """Tier 1B: ``ops.py:_assert_org_access`` pre-fix returned early
    when ``user.role`` was ``admin`` or ``owner`` — but those are
    TENANT-LEVEL roles, not platform-ops roles. An admin of Tenant A
    could pass ``?organization_id=Tenant_B`` to any ``/api/ops/*``
    route and read Tenant B's tenant-health, box-health, and KPI
    digests. There is no super-admin role on the tenant-facing API.

    The same function also coerced ``organization_id or "default"``
    before the equality check — a session whose org was the legacy
    ``"default"`` literal could bypass via an empty query parameter.

    This test pins both fixes via source inspection.
    """
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent
    src = (repo_root / "clearledgr" / "api" / "ops.py").read_text()

    body = src.split("def _assert_org_access", 1)[1].split("\ndef ", 1)[0]

    # No early-return on role.
    assert "_OPS_ADMIN_ROLES" not in body, (
        "ops.py:_assert_org_access still gates on _OPS_ADMIN_ROLES — "
        "that's the tenant-admin cross-tenant bypass the audit flagged."
    )

    # No ``or "default"`` coercion before equality.
    import re
    or_default = re.compile(r"""or\s+["']default["']""")
    assert not or_default.search(body), (
        "ops.py:_assert_org_access still coerces missing org to "
        "'default' before comparing — same M4 landmine."
    )

    # Must fail closed when the session has no org.
    assert "user_missing_organization_id" in body, (
        "ops.py:_assert_org_access must fail closed with 403 when "
        "the user's session carries no organization_id."
    )


def test_gmail_extension_common_assert_user_org_access_fails_closed():
    """Tier 1B: ``gmail_extension_common.assert_user_org_access`` /
    ``resolve_org_id_for_user`` had ``or "default"`` coercions on
    both the requested-org side and the session-org side. A session
    with no org silently coerced to the literal ``"default"`` and
    then either matched another ``"default"``-org session or fell
    through to a global bucket. Same M4 landmine on a different
    surface.
    """
    from pathlib import Path
    import re

    repo_root = Path(__file__).resolve().parent.parent
    src = (repo_root / "clearledgr" / "api" / "gmail_extension_common.py").read_text()
    or_default = re.compile(r"""or\s+["']default["']""")

    # The two functions are the audit-flagged pair.
    for fn_name in ("assert_user_org_access", "resolve_org_id_for_user"):
        body = src.split(f"def {fn_name}", 1)[1].split("\ndef ", 1)[0]
        assert not or_default.search(body), (
            f"gmail_extension_common.{fn_name} still contains an "
            f"``or 'default'`` coercion. Cross-tenant landmine: a "
            f"session whose org was the legacy 'default' literal "
            f"could bypass the access check via an empty body org."
        )

    # ``resolve_org_id_for_user`` must fail closed when the session
    # has no org rather than returning the literal "default".
    body_resolve = src.split("def resolve_org_id_for_user", 1)[1].split("\ndef ", 1)[0]
    assert "user_missing_organization_id" in body_resolve, (
        "resolve_org_id_for_user must raise on a session without an "
        "organization_id, not silently return the literal 'default'."
    )


def test_get_invoice_status_endpoint_scopes_lookup_to_session_org():
    """M5: ``ap_store.get_invoice_status`` matches by ``thread_id``
    only. If two tenants ever share a thread_id (rare with Gmail
    UUIDs but possible with deterministic test ids or shared upstream
    systems) the unscoped form returns whichever row sorts last by
    ``created_at`` — at minimum a cross-tenant existence leak even
    though row contents are protected by the API-layer
    ``_assert_user_org_access`` check.

    The store now accepts an optional ``organization_id`` kwarg that
    scopes the SQL. The externally-exposed Gmail extension endpoint
    (``/api/gmail-extension/invoice-status/{gmail_id}``) MUST pass the
    caller's org so a foreign thread_id is invisible at the SQL level
    and returns 404 in both the foreign-row and unknown-id case.

    This test pins:
      1. the store has the new optional ``organization_id`` parameter,
      2. the endpoint passes ``user.organization_id`` to it,
      3. the post-fetch ``_assert_user_org_access(... or "default")``
         shape — which used a literal 'default' coercion — is gone.
    """
    import inspect
    from pathlib import Path
    from clearledgr.core.stores.ap_store import APStore

    sig = inspect.signature(APStore.get_invoice_status)
    assert "organization_id" in sig.parameters, (
        "ap_store.get_invoice_status must accept an optional "
        "organization_id parameter so externally-exposed callers can "
        "scope the SQL lookup."
    )

    repo_root = Path(__file__).resolve().parent.parent
    ext_path = repo_root / "clearledgr" / "api" / "gmail_extension.py"
    src = ext_path.read_text()

    # Locate the /invoice-status endpoint body.
    marker = '"/invoice-status/{gmail_id}"'
    assert marker in src, "could not locate /invoice-status endpoint in source"
    after = src.split(marker, 1)[1]
    # Snip to the next decorator so we only inspect this handler.
    next_route = after.find("\n@router.")
    body = after[: next_route if next_route > 0 else 4000]

    assert "organization_id=user_org" in body or "organization_id=str(getattr(user" in body, (
        "/invoice-status endpoint must pass the caller's org to "
        "get_invoice_status so the SQL lookup is scoped at the data "
        "layer, not just at the post-fetch _assert_user_org_access "
        "check."
    )

    # The pre-fix shape post-fetched the row, then called
    # _assert_user_org_access with ``or "default"``. The post-fix
    # endpoint should not have that coercion any more.
    assert 'or "default"' not in body, (
        "/invoice-status endpoint still contains the post-fetch "
        "``or \"default\"`` coercion — that path leaked existence of "
        "rows in other tenants via the 403/404 distinction."
    )


def test_byid_store_mutations_require_organization_id():
    """M3: every by-id mutation/lookup on the three stores audited
    (webhook_store, dispute_store, custom_roles_store) must require
    ``organization_id`` so the SQL ``WHERE`` clause fails closed on
    cross-tenant ids regardless of caller diligence.

    Pre-fix any caller from tenant A holding a known id from tenant B
    could read or mutate tenant B's row — we relied on API-layer
    ``_resolve_org_id`` checks alone, which is not defence in depth.

    This test inspects the store sources directly so a future regression
    that drops the ``organization_id`` parameter from a method signature
    fails the test immediately.
    """
    import inspect
    from clearledgr.core.stores import (
        webhook_store as _webhook_store,
        dispute_store as _dispute_store,
        custom_roles_store as _custom_roles_store,
    )

    methods_that_must_have_org = [
        (_webhook_store.WebhookStore, "get_webhook_subscription"),
        (_webhook_store.WebhookStore, "update_webhook_subscription"),
        (_webhook_store.WebhookStore, "delete_webhook_subscription"),
        (_dispute_store.DisputeStore, "get_dispute"),
        (_dispute_store.DisputeStore, "update_dispute"),
        (_dispute_store.DisputeStore, "get_disputes_for_item"),
        (_custom_roles_store.CustomRolesStore, "get_custom_role"),
        (_custom_roles_store.CustomRolesStore, "update_custom_role"),
        (_custom_roles_store.CustomRolesStore, "delete_custom_role"),
        (_custom_roles_store.CustomRolesStore, "resolve_custom_role_permissions"),
    ]
    missing = []
    for cls, name in methods_that_must_have_org:
        sig = inspect.signature(getattr(cls, name))
        if "organization_id" not in sig.parameters:
            missing.append(f"{cls.__name__}.{name}")
    assert not missing, (
        "Cross-tenant by-id mutations: the following store methods are "
        "missing the required ``organization_id`` parameter — a caller "
        "holding a known id from another tenant could read/mutate that "
        "tenant's row at the SQL level. Methods:\n  - "
        + "\n  - ".join(missing)
    )


def test_vendor_profile_callers_use_canonical_arg_order():
    """The B1 anti-pattern at the data layer: callers passing
    ``(vendor_name, organization_id)`` to a function whose signature
    is ``(organization_id, vendor_name)``. Under Postgres, a crafted
    ``vendor_name`` matching a target tenant's org_id will match the
    target tenant's row.

    Pre-fix: ``vendor_store.py:2212`` and ``vendor_onboarding.py:436/448``
    had the arguments swapped. This test scans every caller of
    ``get_vendor_profile`` / ``upsert_vendor_profile`` to ensure the
    canonical ``(organization_id, vendor_name)`` order is used.
    """
    import re
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent
    targets = [
        repo_root / "clearledgr" / "core" / "stores" / "vendor_store.py",
        repo_root / "clearledgr" / "api" / "vendor_onboarding.py",
        repo_root / "clearledgr" / "api" / "payment_confirmations.py",
        repo_root / "clearledgr" / "api" / "vendor_portal.py",
        repo_root / "clearledgr" / "api" / "threshold_policy.py",
        repo_root / "clearledgr" / "api" / "gmail_extension.py",
        repo_root / "clearledgr" / "integrations" / "erp_router.py",
        repo_root / "clearledgr" / "workflows" / "gmail_activities.py",
    ]
    # Match a positional call with two simple args: ``func(arg1, arg2)``
    # where neither arg is a kwarg. We then require arg1 to look like
    # an org identifier (contains ``org``) — every legitimate caller
    # passes ``organization_id`` / ``org_id`` / ``user.organization_id``
    # first.
    pattern = re.compile(
        r"\.(?:get_vendor_profile|upsert_vendor_profile)\(\s*([^,()]+?)\s*,"
    )
    org_like = re.compile(r"organization_id|org_id|\.organization_id")

    bad_sites = []
    for path in targets:
        if not path.exists():
            continue
        text = path.read_text()
        for lineno, line in enumerate(text.splitlines(), start=1):
            for m in pattern.finditer(line):
                first_arg = m.group(1).strip()
                if "vendor" in first_arg.lower() and not org_like.search(first_arg):
                    bad_sites.append(f"{path.relative_to(repo_root)}:{lineno}: {line.strip()}")

    assert not bad_sites, (
        "Found vendor-profile call sites with arguments in the wrong order. "
        "Canonical signature is (organization_id, vendor_name). Sites:\n"
        + "\n".join(bad_sites)
    )
