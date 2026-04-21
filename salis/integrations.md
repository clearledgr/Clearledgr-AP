# Integrations

Per-surface playbook. What each third-party system expects, how we talk to it, what breaks and how to recover.

*Last verified: 2026-04-21 against commit `94c98eb`.*

---

## Gmail (the work surface)

**What it is:** the customer's inbox. We see the invoice, we show the sidebar, we write draft replies, we apply labels. Everything the AP manager does with their day happens here.

**How we connect:** OAuth with the user's Google account. Scopes listed in [`clearledgr/services/gmail_api.py`](../clearledgr/services/gmail_api.py):

- `https://www.googleapis.com/auth/gmail.modify` — read, write drafts, modify labels.
- `https://www.googleapis.com/auth/gmail.send` — send replies.
- `https://www.googleapis.com/auth/gmail.labels` — create + list labels.
- `https://www.googleapis.com/auth/pubsub` — subscribe to Gmail push notifications via Pub/Sub.

Tokens live in the `gmail_tokens` table, encrypted at rest with the `TOKEN_ENCRYPTION_KEY` Fernet derivation. See ADR in progress.

**Inbound path (new email arrives):**

1. Gmail notifies Google Pub/Sub with the mailbox's history ID.
2. Pub/Sub pushes to `POST /gmail/push` ([`clearledgr/api/gmail_webhooks.py`](../clearledgr/api/gmail_webhooks.py)). OIDC JWT verification gates the route — only Google can call it.
3. The handler enqueues an `email_received` event onto the coordination runtime's queue.
4. The planning engine + coordination engine take over. End result: an `ap_item` row in state `received`, classified, extracted, with validation gate + AP routing decision applied.

**Outbound:**

- Label mutations: `gmail_api.py` → `users.messages.modify`.
- Draft creation (vendor outreach): `gmail_api.py` → `users.drafts.create`.
- Send: `gmail_api.py` → `users.messages.send`.

**Failure modes you'll see:**

- `invalid_grant` on token refresh → user has revoked access. Mark the integration inactive, show a reconnect prompt in the sidebar.
- `quota_exceeded` → we're hitting Gmail API rate limits. Back off, retry with exponential delay.
- Pub/Sub push not arriving → check that the topic exists, the subscription is pointing at our endpoint, and the service account has `pubsub.publisher` on the topic.

**Local testing:** the autopilot in `services/gmail_autopilot.py` can fallback-poll the mailbox instead of waiting for Pub/Sub. Set `GMAIL_AUTOPILOT_POLL_MODE=true` in your dev env.

---

## Slack (the decision surface)

**What it is:** where approvals happen. A Slack card shows the invoice, the AP manager clicks Approve / Reject / Needs Info, and the runtime posts the bill (or routes to a follow-up).

**How we connect:** Slack app per workspace. Bot token + signing secret stored in the `slack_installs` table.

**Outbound:**

- Approval cards via `slack_cards.py` + `slack_api.py`. Card blocks are Block Kit JSON.
- Exception notifications (bank change, duplicate, missing PO) render with different block shapes in the same `slack_cards.py`.
- Digest messages via `send_digest` call `chat.postMessage` with a summary.

**Inbound (user clicks a button):**

1. Slack sends a signed interactive-action payload to `POST /slack/events` ([`clearledgr/api/slack_invoices.py`](../clearledgr/api/slack_invoices.py)).
2. `slack_verify.py` checks the HMAC-SHA256 signature using the signing secret.
3. Lookup `channel_threads` by the Slack message ID to find the Box.
4. Dispatch an intent (`approve_invoice`, `reject_invoice`, `request_info_from_vendor`) — this enqueues an event, which the runtime plans + executes.

**Rate limits:** Slack allows ~1 message per channel per second and a few thousand per minute per workspace. `rate_limit.py` uses Redis-backed sliding windows; we don't hit this in practice but it's there.

**Failure modes:**

- `token_revoked` → workspace removed the app. Mark install inactive; incoming webhooks return 404 with `workspace_uninstalled`.
- Slack message not found when clicking approve → we might have deleted the `channel_threads` row (cleanup bug). Grep logs for "channel_thread_not_found."
- Signature verification fails → either clock skew or the signing secret got rotated. Refresh the install.

**Local testing:** `services/slack_notifications_test.py` patterns let you assert that a given `ap_item` state triggers the right card without hitting Slack.

---

## ERPs (the system of record)

**What it is:** where the money actually moves. We post a bill once all approvals land, and we poll payment status afterward.

**Four ERPs supported at V1** (see ADR 005):

| ERP | Auth | Connector file |
|---|---|---|
| QuickBooks Online | OAuth2, company_id in URL | `clearledgr/integrations/erp_quickbooks.py` |
| Xero | OAuth2, tenant_id header | `clearledgr/integrations/erp_xero.py` |
| NetSuite | Token-based auth (TBA) | `clearledgr/integrations/erp_netsuite.py` |
| SAP S/4HANA | OAuth2 + CSRF token | `clearledgr/integrations/erp_sap.py` |

All four go through [`clearledgr/integrations/erp_router.py`](../clearledgr/integrations/erp_router.py). The router picks the connector by `ERPConnection.erp_type` and calls the right `post_bill_to_<erp>` function.

**Outbound (posting a bill):**

1. `approve_invoice` in `invoice_posting.py` assembles the canonical payload (vendor, amount, currency, invoice_number, gl_code, company_code for SAP).
2. Pre-flight validation — especially SAP, which needs `company_code` + nonempty `vendor_id` before the httpx call. Returns `{"reason": "sap_validation_failed", "missing_fields": [...]}` if invalid.
3. Per-ERP call via `erp_router.post_bill`. Returns `{"status": "success", "erp_reference": "123"}` on success or `{"status": "error", "reason": "...", "erp": "<type>"}` on failure.
4. On 401: `refresh_<erp>_token()`, then retry ONCE. If still 401, bubble up as `auth_failed`.
5. On success: caller updates `ap_items.erp_reference`, transitions state to `posted_to_erp`, and the `box_outcomes` mirror fires.

**Inbound (ERP webhook with payment confirmation):**

1. Each ERP has its own webhook endpoint under `/erp/webhooks/{erp_type}/{org_id}`.
2. HMAC verification in `core/erp_webhook_verify.py` (per-ERP signature scheme — Xero uses X-Xero-Signature, QBO uses its own, etc.).
3. Dispatch to event queue → `payment_confirmed` / `erp_grn_confirmed` / etc. event.

**Rate limiting:** each ERP has its own limits. `erp_rate_limiter.py` enforces them per-tenant per-ERP so one customer posting 100 bills doesn't slow another tenant's requests.

**Failure modes:**

- 401 on post_bill → auto-refresh, retry once, then abort. `_classify_failure` returns `AUTH` category.
- 429 → rate-limited. Back off with the `Retry-After` header. `_classify_failure` returns `RATE_LIMIT`.
- 400 validation (missing field, bad format) → `PERMANENT` failure. Aborts, raises a `box_exceptions` row with the ERP's error message.
- 5xx → `TRANSIENT`. Retry 3 times with exponential backoff.

**Per-tenant GL mapping:** `settings_json.gl_account_map` on the `organizations` row maps internal `gl_code` → per-tenant ERP `AccountRef`. Passed to `post_bill_to_<erp>` as `gl_map` param. `GET/PUT /erp/gl-map` endpoints manage it.

**Adding a new ERP:** see `contributing.md` → "Add a new ERP" recipe.

---

## Vendor portal (magic-link surface)

**What it is:** the only unauthenticated HTML surface. When we need KYC docs or bank details from a vendor, we email them a magic link. They click it, land on the portal, submit the info. No login.

**Entry point:** `POST /portal/onboard/{token}/submit` → [`clearledgr/api/vendor_portal.py`](../clearledgr/api/vendor_portal.py).

**Auth:** magic-link tokens in `onboarding_token_store`. Tokens are single-use, scoped to one vendor session, expire in 7 days.

**Input validation:** every field goes through `core/portal_input.py` — explicit allowlists, max lengths, type coercion. No `eval`, no raw dict-to-model shortcuts. ADR 010 covers the threat model.

**Failure modes:**

- Token expired or already used → 404 with a regenerate-link affordance.
- Invalid input → 400 with per-field errors.
- Vendor submits the wrong workflow step's data → silently accepted, ignored (vendors retry with the right form).

**Templates:** Jinja2 in `clearledgr/templates/`. Server-rendered HTML only — no frontend framework. Kept simple because vendors will view this on mobile browsers of wildly varying capability.

---

## KYC + open banking (stubbed)

**Status:** provider adapters are stubbed until contracts are signed. See ADR 009.

- `services/kyc_provider.py` — interface; `MockKYCProvider` returns a canned success response.
- `services/open_banking_provider.py` — interface; `MockOpenBankingProvider` returns a canned verification result.

The vendor onboarding lifecycle events (`KYC_CHECK_COMPLETED`, `OPEN_BANKING_VERIFICATION_COMPLETED`) have planners wired ([`planning_engine.py:_plan_kyc_check_completed`](../clearledgr/core/planning_engine.py)) but no real producers until we integrate a real provider. When you wire one, the producer call site goes in the provider adapter; everything downstream just works.

Candidates on the shortlist: Sumsub + Plaid (US), Onfido + TrueLayer (UK/EU). Contract + integration is a Q3 2026 conversation.

---

## Teams + Outlook (V1.1, feature-flagged)

`FEATURE_TEAMS_ENABLED=false` and `FEATURE_OUTLOOK_ENABLED=false` by default. The code exists so we can demo to a Microsoft-shop customer, but routes don't register in strict profile.

- Teams: `clearledgr/api/teams_invoices.py` + `services/teams_api.py`. Adaptive Cards posted via Graph API. Inbound callbacks via a Teams bot webhook.
- Outlook: `clearledgr/api/outlook_routes.py` + `services/outlook_autopilot.py`. Outlook Mail API + webhooks equivalent.

Both paths go through the same `CoordinationEngine` — the surface is just a different adapter. If a customer needs it, flip the flag, deploy, and the routes register.

---

## Webhook subscriptions (outbound to customers)

**What it is:** customer-configured URL where we send them event notifications. Powers the `box.exception_raised`, `box.exception_resolved`, `box.outcome_recorded` emissions from Phase 9.

**Schema:** `webhook_subscriptions(id, organization_id, url, event_types, secret, is_active, description, created_at, updated_at)`.

**Management:** `POST /webhooks`, `GET /webhooks`, `DELETE /webhooks/{id}`, `POST /webhooks/{id}/test` — all in [`clearledgr/api/workspace_shell.py`](../clearledgr/api/workspace_shell.py). Admin/owner role gated.

**Delivery:** [`clearledgr/services/webhook_delivery.py`](../clearledgr/services/webhook_delivery.py). `emit_webhook_event` looks up all subscriptions matching the `event_type`, POSTs the payload with an HMAC signature derived from the subscriber's secret. Fire-and-forget with async retry via `notification_queue` on failure.

**Event catalog (customer-facing):**

| Event | Payload shape |
|---|---|
| `invoice.*` (received, validated, needs_approval, approved, rejected, posted_to_erp, closed, etc.) | `{organization_id, ap_item_id, new_state, prev_state, invoice_data}` |
| `vendor.*` (kyc_complete, bank_verified, activated, suspended) | `{organization_id, vendor_session_id, new_state, ...}` |
| `vendor.invited` | `{organization_id, vendor_name, session_id, magic_link_expires_at}` |
| `box.exception_raised` | `{box_id, box_type, organization_id, exception: {id, exception_type, severity, reason, raised_at, raised_by}}` |
| `box.exception_resolved` | `{box_id, box_type, organization_id, exception: {id, exception_type, resolved_at, resolved_by, resolution_note}}` |
| `box.outcome_recorded` | `{box_id, box_type, organization_id, outcome: {id, outcome_type, recorded_at, recorded_by, data}}` |

---

## Where to look when something's wrong

- New email not showing up → `gmail_webhooks.py` logs. Is Pub/Sub pushing? Is OIDC verifying?
- Slack button click does nothing → `slack_invoices.py` logs. Signature verifying? `channel_threads` lookup finding the Box?
- Bill posted but no `erp_reference` → `invoice_posting.py` + `erp_router.py` logs. Did the ERP return success? Did the retry-once auth refresh fire?
- Vendor portal not accepting magic link → `vendor_portal.py` logs + `onboarding_token_store` for the token state.
- Webhook not reaching customer → `webhook_delivery.py` logs. Did the secret match? Is the URL returning 2xx?

Runbooks for the more involved investigations live in [`operations.md`](./operations.md).
