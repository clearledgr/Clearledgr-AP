# Clearledgr — SAP S/4HANA Fiori Extension

Two-way bridge between SAP S/4HANA and Clearledgr's coordination layer
— the SAP-side equivalent of the NetSuite SuiteApp under
[`integrations/netsuite-suiteapp/`](../netsuite-suiteapp/README.md).

* **Read direction** — a native SAPUI5 application deployed via SAP
  BTP (HTML5 Apps Repo + Approuter) that renders the Clearledgr Box
  (state, timeline, exceptions, outcome) for the supplier invoice the
  user is viewing in S/4HANA. Phase 1-3 ships as a side-by-side Fiori
  app launched from a button on **Manage Supplier Invoices**; Phase 4
  upgrades to a **Manifest Extension** that renders inline on the
  Display Supplier Invoice page.
* **Write direction** — S/4HANA business events
  (`sap.s4.beh.supplierinvoice.v1.SupplierInvoice.Created`, `…Posted`,
  `…Blocked`, `…Cancelled`, `…Paid`) flow into our webhook at
  `/erp/webhooks/sap/<orgId>` via either:
    * **S/4HANA Cloud:** SAP BTP Event Mesh subscription forwards
      CloudEvents directly.
    * **S/4HANA on-premise:** an ABAP enhancement (BAdI on `BUS2081`
      / `MIRO` post-save) pushes the same payload through SAP Cloud
      Connector.
  The dispatcher creates / advances Boxes; the existing Slack approval
  routing (with per-amount approver targets) handles bills that land
  with a payment block.

## Architecture

```
                S/4HANA (customer's tenant)
                ┌────────────────────────────────────────┐
       AP user ▶│  Manage Supplier Invoices (F0859)      │
                │     ┌──────────────────────────────┐   │
                │     │ Clearledgr Box panel         │◀──┤── side-by-side Fiori app
                │     │ (SAPUI5 component)           │   │   in BTP HTML5 Repo
                │     └──────────────────────────────┘   │
                └────────────────┬───────────────────────┘
                                 │ XSUAA-authed via Approuter
                                 ▼
                ┌────────────────────────────────────────┐
                │ BTP Approuter                          │
                │   /clearledgr-api/* → destination ──┐  │
                └─────────────────────────────────────┼──┘
                                                      │
                                                      ▼
                                  Clearledgr backend (api.clearledgr.com)
                                  ├─ /extension/sap/exchange         (XSUAA → CL JWT)
                                  ├─ /extension/ap-items/by-sap-invoice
                                  ├─ /extension/route-low-risk-approval
                                  └─ /extension/reject-invoice

           Cloud Event Mesh / ABAP BAdI ── HMAC-signed POST ──▶ /erp/webhooks/sap/<orgId>
                                                                  └─ dispatcher → Box created
                                                                                 → Slack routed
```

## Status

| Phase | What it does | Done? | Hours |
|-------|--------------|-------|-------|
| 1 (read) | SAPUI5 app boots, reads invoice from URL params, shows "Hello" panel | ✅ scaffolded | 3 |
| 2 (read) | XSUAA→Clearledgr token exchange + Box render (state, timeline, exceptions) | ✅ scaffolded | 3.5 |
| 3 (read) | Real BTP deploy: MTA + Approuter + Destination + HTML5 Repo | ✅ scaffolded | 5 |
| 1 (write) | Webhook dispatcher accepts BTP Event Mesh CloudEvents + ABAP-BAdI shapes; creates/advances Boxes | ✅ scaffolded | 3 |
| 2 (write) | Slack approval card on payment block (reuses NetSuite path); approve clears block via OData; reject cancels via OData action | ✅ scaffolded | 3 |
| 4 (read) | Manifest Extension on `Display Supplier Invoice` standard app | not started | weeks (customer-side) |
| 4 (write) | Customer-tenant deploy: BTP subaccount, Cloud Connector, IDP federation, role assignments | not started | 3-6 weeks (customer-side) |

The code on disk is Phase-1+2+3 (read) + Phase-1+2 (write) ready. What
still needs human action: a BTP trial subaccount, an XSUAA service
binding, a Destination pointing at `api.clearledgr.com`, and the
event-firing path on the customer's S/4HANA.

## Repo layout

```
integrations/sap-fiori-extension/
├── README.md                                   # this file
├── package.json                                # workspace root (mbt + npm workspaces)
├── mta.yaml                                    # MTA descriptor
├── xs-security.json                            # XSUAA scopes
├── .gitignore
├── deployer/
│   └── package.json                            # placeholder for HTML5 deployer module
├── approuter/
│   ├── package.json                            # @sap/approuter dependency
│   └── xs-app.json                             # routes /clearledgr-api/* → destination
└── webapp/
    ├── package.json                            # SAPUI5 dev deps + build scripts
    ├── ui5.yaml                                # UI5 tooling config
    ├── manifest.json                           # Fiori app metadata + cross-nav intent
    ├── Component.js                            # SAPUI5 component
    ├── index.html                              # bootstrap
    ├── i18n/i18n.properties
    ├── css/style.css                           # mint + navy brand tokens; rest from Horizon
    ├── view/BoxPanel.view.xml                  # XML view (Page + sections + actions)
    └── controller/BoxPanel.controller.js       # XSUAA exchange + Box load + actions
```

## Deploy

### Prerequisites

1. **BTP trial subaccount** at <https://account.hanatrial.ondemand.com>
   (free; choose a region close to where Booking.com / Cowrywise run S/4HANA — `us10` for trial defaults).
2. **Cloud Foundry environment** enabled on that subaccount, with a
   `dev` org + `dev` space.
3. **CLI tools** installed locally:
   ```bash
   npm install -g mbt @ui5/cli
   # Cloud Foundry CLI from https://docs.cloudfoundry.org/cf-cli/install-go-cli.html
   cf install-plugin multiapps
   ```
4. **Service entitlements** added to the subaccount (BTP cockpit →
   Entitlements):
   - `html5-apps-repo` (plans `app-host`, `app-runtime`)
   - `xsuaa` (plan `application`)
   - `destination` (plan `lite`)
5. **Destination** named `clearledgr-api` configured in the subaccount
   (BTP cockpit → Connectivity → Destinations → New):
   - URL: `https://api.clearledgr.com`
   - Type: HTTP
   - ProxyType: Internet
   - Authentication: NoAuthentication (Approuter forwards the
     XSUAA JWT in the Authorization header; the Clearledgr exchange
     endpoint pulls the JWT from the request body, not the header,
     so we don't need OAuth2 token-exchange auth at the Destination
     layer in Phase 1-3).
   - Additional properties:
     - `HTML5.DynamicDestination` = `true`
     - `HTML5.ForwardAuthToken` = `true`

### One-time setup

```bash
cd integrations/sap-fiori-extension/

# Install workspace deps
npm install
npm install --workspace=webapp
npm install --workspace=approuter

# Log in to Cloud Foundry
cf login -a https://api.cf.us10-001.hana.ondemand.com -o <your-org> -s dev
```

### Build + deploy

```bash
npm run build            # produces mta_archives/com.clearledgr.s4hana.boxpanel_0.1.0.mtar
npm run deploy           # cf deploy
```

After deploy completes, the Approuter URL is in the deploy log
(something like `https://<account>-clearledgr-boxpanel-approuter.cfapps.us10-001.hana.ondemand.com`).
Open that URL with the supplier-invoice composite key in the query
string:

```
https://<approuter-url>/?CompanyCode=1010&SupplierInvoice=5105600123&FiscalYear=2026
```

You'll be redirected to BTP login; after signing in, the panel
renders the Clearledgr Box for that invoice.

### Backend per-tenant config (Clearledgr side) — **multi-tenant by default**

Each SAP customer has their own BTP subaccount with their own XSUAA
service, JWKS URL, and `xsappname`. The `/extension/sap/exchange`
endpoint resolves these *per-tenant* by:

1. Reading the JWT's ``iss`` claim (unverified parse — safe; only
   used for lookup).
2. Matching the issuer against the org's
   ``erp_connections.credentials.s4hana_xsuaa_issuer``.
3. Verifying the JWT against the matched org's
   ``credentials.s4hana_xsuaa_jwks_url`` + ``s4hana_xsuaa_audience``.
4. Pinning the resolved org_id from the matched row — caller cannot
   override (cross-tenant guard).

Provision per tenant once during onboarding:

```sql
-- Example for booking-corp's S/4HANA connection
UPDATE erp_connections
SET credentials = credentials || jsonb_build_object(
    's4hana_xsuaa_issuer',   'https://booking.authentication.eu10.hana.ondemand.com/oauth/token',
    's4hana_xsuaa_jwks_url', 'https://booking.authentication.eu10.hana.ondemand.com/token_keys',
    's4hana_xsuaa_audience', 'clearledgr-boxpanel-prod',
    'webhook_secret',        '<HMAC secret used for outbound webhook verification>'
)
WHERE organization_id = 'booking-corp' AND erp_type = 'sap_s4hana';
```

Look these up in BTP cockpit:

* **Issuer / JWKS URL** → Subaccount → Service Marketplace → XSUAA
  → Service Instance (Show Sensitive Data) → ``url`` field. The
  issuer is ``<url>/oauth/token``; the JWKS URL is ``<url>/token_keys``.
* **Audience** → the ``xsappname`` from `xs-security.json` after MTA
  deploy resolves the ``${space}`` placeholder (visible in the
  service binding's ``credentials.xsappname``).
* **webhook_secret** → generate with ``openssl rand -base64 48``;
  store the same value on the BTP Event Mesh subscription / ABAP
  BAdI side.

**Single-tenant fallback** (dev / staging only): if no tenant config
matches the JWT's issuer, the endpoint falls through to env vars
``SAP_XSUAA_JWKS_URL`` + ``SAP_XSUAA_AUDIENCE``. Production should
always use per-tenant config.

### Write-direction setup

For ERP-native bill events to flow back, configure the customer-side
event source. Two paths depending on their S/4HANA flavour:

**S/4HANA Cloud — BTP Event Mesh:**

1. Subscribe to the SAP business events under namespace
   `sap.s4.beh.supplierinvoice.v1.*` (Created, Posted, Blocked,
   Cancelled, Paid).
2. Configure a webhook target pointing at
   `https://api.clearledgr.com/erp/webhooks/sap/<orgId>` with the
   shared HMAC secret from `erp_connections.credentials.webhook_secret`.

**S/4HANA on-premise — ABAP enhancement:**

1. Implement a BAdI on `BUS2081` (or post-`MIRO` enhancement) that
   POSTs the event payload through SAP Cloud Connector.
2. Use the same JSON shape the dispatcher accepts (the
   `event_type` / `invoice` form documented in
   `clearledgr/services/sap_webhook_dispatch.py`).
3. Sign with the same HMAC secret + `X-SAP-Signature: v1=<hex>` /
   `X-SAP-Timestamp: <unix>` headers the existing webhook verifier
   in `clearledgr/core/erp_webhook_verify.py` expects.

## Backend dependencies

This integration expects on the Clearledgr side:

* **`POST /extension/sap/exchange`** — XSUAA → Clearledgr JWT bridge.
  Implemented in [`clearledgr/api/sap_extension.py`](../../clearledgr/api/sap_extension.py).
* **`GET /extension/ap-items/by-sap-invoice`** — Box read by composite
  key (`?company_code=&supplier_invoice=&fiscal_year=`).
* **CORS regex** allows `*.hana.ondemand.com`, `*.s4hana.cloud.sap`,
  `*.fiori.cloud.sap` — see [`main.py`](../../main.py)
  `_resolve_cors_policy`.
* **Strict-profile allowlist** includes the new endpoints — same
  pattern as Gmail extension, NetSuite panel.
* **Approve/Reject actions** call existing
  `/extension/route-low-risk-approval` and `/extension/reject-invoice`
  endpoints; the AP item's `metadata.source == "sap_native"` triggers
  the SAP write-back path in
  [`clearledgr/services/erp_native_approval.py`](../../clearledgr/services/erp_native_approval.py).
* **SAP write-back helpers** in
  [`clearledgr/integrations/erp_sap_s4hana.py`](../../clearledgr/integrations/erp_sap_s4hana.py):
    * `release_payment_block(...)` — PATCH `A_SupplierInvoice` with
      `PaymentBlockingReason=""`.
    * `cancel_supplier_invoice(...)` — POST
      `SupplierInvoiceCancellation` action; falls back to PATCH with
      `ReverseDocument=True` for accounts that don't expose the
      action over REST.

## Phase 4 — what's still in the customer's court

* Provision their own BTP subaccount (or extend an existing one) with
  the same MTA descriptor.
* Set up SAP Cloud Connector if they're on-prem.
* Trust their corporate IDP via SAP IAS in the BTP subaccount.
* Configure the Destination in their subaccount pointing at
  `api.clearledgr.com`.
* Assign role collections (`Clearledgr Box Panel — Reader` / `Approver`)
  to their AP team in BTP cockpit.
* For the Manifest Extension upgrade: enable UI5 flexibility services
  on their S/4HANA tenant and deploy the extension as an ABAP transport.

Realistic elapsed: 3-6 weeks from "Clearledgr has a working BTP demo"
to "Booking.com's AP team uses it on their real S/4HANA tenant." That's
not Clearledgr writing code — it's their IT change-management.

## Why this exists

Per the deck:

> *"ONE TRUTH · MANY WINDOWS"*

The Clearledgr Box is the source of truth. Gmail (sidebar), Slack
(approvals), NetSuite (panel + intake), and now SAP S/4HANA (panel +
intake) are the windows. A bill that arrives via EDI in Booking.com's
S/4HANA never touches Gmail, but flows through the same coordination
pipeline — exception detection, Slack approval routing, payment-block
release, audit trail — as a Gmail-arrived bill at Cowrywise.

Booking.com is the launch design partner.
