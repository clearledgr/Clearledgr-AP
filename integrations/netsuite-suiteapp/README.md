# Clearledgr — NetSuite SuiteApp

Two-way bridge between NetSuite and Clearledgr's coordination layer:

* **Read direction** — embeds a Clearledgr panel inside the **Vendor Bill**
  record showing Box state, timeline, exceptions, and approval actions
  for whatever AP item is linked to that bill (whether it originated
  via Gmail and was posted to NetSuite, or originated in NetSuite and
  was tracked back into Clearledgr — see write direction).
* **Write direction** — the User Event Script's `afterSubmit` fires an
  HMAC-signed webhook to `/erp/webhooks/netsuite/<orgId>` on every
  vendor-bill insert / update / paid event. Bills that arrive via EDI,
  vendor portal, or AP-clerk-typed entry (i.e. never touching Gmail)
  become Boxes, run through validation/exception detection, and route
  to Slack the same way email-arrived bills do.

```
                    ┌────────────────────────────────────────┐
NetSuite Vendor     │  ┌──────────┐  ┌────────────────────┐  │
Bill (record view)  │  │ Standard │  │ Clearledgr (subtab)│  │
                    │  │  fields  │  │ ┌────────────────┐ │  │
                    │  │  …       │  │ │ State badge    │ │  │
                    │  └──────────┘  │ │ Summary        │ │  │
                    │                │ │ Exceptions     │ │  │
                    │                │ │ Timeline       │ │  │
                    │                │ │ Action buttons │ │  │
                    │                │ └────────────────┘ │  │
                    │                └────────────────────┘  │
                    └────────────────────────────────────────┘
                              │ iframe = Suitelet
                              ▼
                    Suitelet `customscript_cl_sl_panel`
                    serves panel.html / .js / .css
                    with bill_id + account_id + JWT
                              │ fetch (Bearer JWT)
                              ▼
                    api.clearledgr.com
                    GET /extension/ap-items/by-netsuite-bill/{id}?account_id=…
                              │ verify HMAC, lookup by erp_reference
                              ▼
                    {state, timeline, exceptions, outcome, summary}
```

---

## Status

| Phase | What it does | Done? | Hours |
|-------|--------------|-------|-------|
| 1 (read) | UE script injects "Clearledgr" subtab on Vendor Bill, iframe loads Suitelet | ✅ scaffolded | 2.5 |
| 2 (read) | Suitelet serves panel HTML/JS/CSS, panel calls Clearledgr API w/ dev token, renders Box state | ✅ scaffolded | 2.5 |
| 3 (read) | Real per-tenant HMAC JWT auth (Suitelet mints, backend verifies via `webhook_secret` in `erp_connections.credentials`) | ✅ scaffolded | 4 |
| 1 (write) | UE `afterSubmit` fires signed webhook on bill create/update/paid; backend dispatcher creates/advances/closes Box | ✅ scaffolded | 3 |
| 2 (write) | Slack approval routing for ERP-native bills with payment holds; remove the hold via NetSuite API on approval | not started | 2 |
| 4 | SuiteApp marketplace listing | not started | weeks |

The code on disk is Phase-1+2+3 ready. What still needs human action: a NetSuite sandbox account, a TBA integration record, and a SuiteCloud CLI install. See **Deploy** below.

---

## Repo layout

```
integrations/netsuite-suiteapp/
├── README.md                                   # this file
├── project.json                                # SDF project descriptor
├── suitecloud.config.js                        # CLI config
└── src/
    ├── manifest.xml                            # required SDF features
    ├── deploy.xml                              # what gets included on deploy
    ├── AccountConfiguration/                   # (empty — no account-level changes)
    ├── FileCabinet/SuiteApps/com.clearledgr.suiteapp/
    │   ├── ue_clearledgr_panel.js              # User Event Script (Vendor Bill beforeLoad)
    │   ├── sl_clearledgr_panel.js              # Suitelet (serves panel HTML)
    │   ├── lib/                                # (reserved — no shared lib yet)
    │   └── ui/
    │       ├── panel.html                      # iframe document (templated by Suitelet)
    │       ├── panel.js                        # vanilla-JS panel controller
    │       └── panel.css                       # mint #00D67E + navy #0A1628 styling
    └── Objects/
        ├── customscript_cl_ue_panel.xml        # UE script + deployment metadata
        ├── customscript_cl_sl_panel.xml        # Suitelet + deployment metadata
        └── customrecord_cl_settings.xml        # Tenant config (API base + panel secret)
```

---

## Deploy

### Prerequisites

1. **NetSuite sandbox account** with admin access. (Cowrywise's sandbox once they hand it over; for internal testing, our own SuiteCloud Developer account works — sign up at [system.netsuite.com](https://system.netsuite.com).)
2. **SuiteCloud CLI:**
   ```bash
   npm install -g @oracle/suitecloud-cli
   ```
3. **Account features enabled** (Setup → Company → Enable Features → SuiteCloud):
   - Server SuiteScript
   - Client SuiteScript
   - Custom Records
   - SuiteCloud Development Framework
   - Token-based Authentication
4. **TBA integration record** (Setup → Integration → Manage Integrations → New). Save the consumer key + secret.
5. **Access token** (Setup → Users/Roles → Access Tokens → New) tied to that integration record and a role with at minimum: SuiteScript (Full), Custom Records (Full), Vendor Bill (View), Document Folders (Edit). Save the token id + secret.

### One-time setup

```bash
cd integrations/netsuite-suiteapp/
suitecloud account:setup
# Paste the four credentials when prompted — credentials are stored at
# ~/.suitecloud-sdk/credentials/<account_id> and reused on every deploy.
```

### Deploy

```bash
cd integrations/netsuite-suiteapp/
suitecloud project:deploy
```

Then in NetSuite:
1. Open any **Vendor Bill** record (view mode). You should see a "Clearledgr" tab. Click it. The panel iframe loads.
2. If no AP item is linked to that bill (the bill wasn't posted by Clearledgr), the panel renders the empty state: *"This Bill was not processed through Clearledgr."*
3. To get a real linked record: post a bill from Clearledgr to your NetSuite sandbox via the existing `POST /extension/post-to-erp` flow. The `erp_reference` field on the resulting `ap_items` row is the NetSuite bill internal id — that's the linkage.

### Phase 2 (dev) auth

The Suitelet mints `DEMO_PHASE_2` as the Bearer token. The Clearledgr backend accepts it **only** if the env var `NETSUITE_PANEL_DEV_TOKEN=DEMO_PHASE_2` is set on the API service. To enable in dev:

```bash
railway variables --service api --set "NETSUITE_PANEL_DEV_TOKEN=DEMO_PHASE_2"
```

**Do not set this in production.** It bypasses real auth.

### Phase 3 (production) auth

For each tenant:
1. Generate a strong shared secret (e.g. `openssl rand -base64 48`).
2. Add it to the tenant's NetSuite custom record (one record per account):
   - Customization → Lists, Records & Fields → Record Types → Clearledgr Settings → New
   - Field `custrecord_cl_api_base`: `https://api.clearledgr.com`
   - Field `custrecord_cl_bundle_secret`: paste the secret
   - Field `custrecord_cl_org_id`: paste the tenant's Clearledgr org_id (used in outbound webhook URL)
3. Add the same secret to Clearledgr's `erp_connections.credentials.webhook_secret` for that org. The same field is reused for both the panel JWT (read direction) and the outbound webhook signature (write direction).
4. Update `sl_clearledgr_panel.js` to read the secret from `customrecord_cl_settings` and HMAC-sign the JWT (the scaffold currently uses the dev token — see the comment block at the top of that file).

---

## Open questions / Phase-4 follow-ups

1. **OneWorld subsidiary disambiguation.** Today the backend resolves `account_id → organization_id` 1:1. NetSuite OneWorld accounts can have one tenant mapped to multiple Clearledgr orgs (per subsidiary). The JWT should also carry the subsidiary id and the lookup should be `(account_id, subsidiary_id) → org_id`. Not needed for Cowrywise (single subsidiary). Document the assumption when onboarding any multi-subsidiary customer.
2. **`erp_reference` shape for credit applications.** `erp_netsuite.py:911` stores `"{credit_id}:{target_ref}"` for credit-note applications, not the bare bill id. The current lookup matches on the bare id — won't find credit-application rows. For Phase 1-3 this is fine (Cowrywise pilot is straight bill posts). Phase 4 should either index both forms or normalize the linkage.
3. **Marketplace listing.** Convert ACP → SuiteApp project (requires reserved bundle ID + Application ID from NetSuite Partner Portal). NetSuite review takes 4-8 weeks. Out of scope for the demo.
4. **Subaccount `customrecord_cl_settings` provisioning UX.** Today the customer admin pastes the secret manually. A nicer flow: Clearledgr admin UI generates the secret + a one-time install link, the NetSuite admin clicks through and the record auto-populates. Backlog.

---

## Backend dependencies

### Read direction
- **New endpoint** `GET /extension/ap-items/by-netsuite-bill/{ns_internal_id}?account_id=…` — implemented in [`clearledgr/api/netsuite_panel.py`](../../clearledgr/api/netsuite_panel.py).
- **CORS regex** allows `https://<account>.app.netsuite.com` — see [`main.py`](../../main.py) `_resolve_cors_policy`.
- **Strict-profile allowlist** includes the new dynamic path — see `STRICT_PROFILE_ALLOWED_DYNAMIC_PATTERNS` in `main.py`.
- **Approve / Reject / Request-info actions** call the existing `POST /extension/submit-for-approval`, `POST /extension/reject-invoice`, `POST /extension/route-low-risk-approval` endpoints. No new action routes.

### Write direction
- **Existing endpoint reused:** `POST /erp/webhooks/netsuite/{org_id}` in [`clearledgr/api/erp_webhooks.py`](../../clearledgr/api/erp_webhooks.py). Verifies HMAC signature against `erp_connections.credentials.webhook_secret`, records audit, then calls the dispatcher.
- **New dispatcher:** [`clearledgr/services/erp_webhook_dispatch.py`](../../clearledgr/services/erp_webhook_dispatch.py) — routes `vendorbill.create / .update / .paid / .delete` to handlers that create or advance the AP item Box. Idempotent on `erp_reference == ns_internal_id`.
- **State machine bypass:** ERP-native bills enter the Box state machine at `posted_to_erp` (the bill is already in the ERP — Clearledgr is tracking, not creating) or `needs_approval` (if NetSuite has a payment hold). `closed` on payment events.

---

## Why this exists

Slide 3 of the pitch deck claims ERP as a "rendered into" surface alongside Gmail, Slack, and Backoffice. Today the ERP integration is write-only — Clearledgr posts bills *to* NetSuite, but a NetSuite user opening that bill sees no Clearledgr context. This SuiteApp closes that gap. **One Box. Many windows.** The NetSuite Vendor Bill becomes one of the windows.

Cowrywise is the launch design partner.
