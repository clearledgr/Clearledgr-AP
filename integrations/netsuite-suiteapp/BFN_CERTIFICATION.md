# Built-for-NetSuite Certification — Solden SuiteApp

Oracle's Built-for-NetSuite (BFN) program is the certification layer
that gates marketplace listing. Without BFN, the Solden SuiteApp
can be sideloaded into a customer's account but cannot be discovered
or installed via the SuiteApp Marketplace.

This document is the prep checklist + test plan + security
questionnaire framing the Solden team needs before submitting.

| Phase | Owner | Time |
|---|---|---|
| **A. Submission package** — manifest, screenshots, listing copy | Solden ops + eng | 1 week |
| **B. Functional review** — Oracle reviewer drives the SuiteApp through scripted scenarios | Oracle | 2–4 weeks |
| **C. Security questionnaire** — Oracle reviews data handling, auth, secrets | Oracle | 2–4 weeks (parallel with B) |
| **D. Performance review** — Oracle confirms the SuiteApp doesn't degrade NetSuite throughput | Oracle | 1–2 weeks |
| **E. Customer reference** — Oracle interviews a deployed customer (Cowrywise) | Solden + customer | 1–2 hours of customer time |

Allow **6–12 weeks total** between submission and approval. Most
back-and-forth happens in Phase B + C.

---

## Phase A — Submission package

### A.1 SDF project review

Before submission, walk the SDF project once to confirm BFN-compliant
patterns:

- [x] **No hard-coded credentials.** Every secret reads from
      `customrecord_cl_settings` (per-tenant) or environment, never
      embedded in `.js` source.
- [x] **No `eval()`, `Function()`, or dynamic script generation** in
      any SuiteScript. The panel iframe loads pre-built HTML/JS/CSS
      from the FileCabinet; nothing is generated at request time.
- [x] **Explicit `@NApiVersion 2.1`** + `@NScriptType` JSDoc on every
      script entry point. SuiteScript 2.0 is deprecated; 2.1 is the
      current supported version.
- [x] **No `N/runtime.executeAsync`** outside legitimate background
      tasks. The afterSubmit hook is synchronous; the iframe load is
      lazy via the Suitelet.
- [x] **All HTTPS endpoints in a documented allow-list** (the
      `validDomains`-equivalent for SuiteApps is the bundle-secret-
      keyed `apiBase` field; only `api.solden.com` resolves).
- [x] **`isinactive='F'` filter** on every search of
      `customrecord_cl_settings` (already applied in
      `ue_clearledgr_panel.js:loadSettings`).
- [x] **Bundle ID + Application ID reserved** from
      [partners.netsuite.com](https://partners.netsuite.com) — fill in
      the manifest's bundle ID before submission.

### A.2 Screenshots

NetSuite requires 5–8 screenshots showing the SuiteApp's UX. Take
each in a clean sandbox demo tenant with realistic test data:

1. **Vendor Bill record with the Clearledgr subtab visible, Box state badge "needs_approval"**.
   - Highlight the subtab in the screenshot annotation.
2. **Subtab expanded showing the full panel: state badge, vendor +
   amount + invoice number summary, exception list, timeline, action buttons**.
3. **Panel after Approve clicked — state badge advanced to
   "approved", action buttons hidden, audit timeline shows the
   transition with the operator's email**.
4. **Empty-state panel** ("This Bill was not processed through
   Clearledgr") — for a vendor bill that bypassed Solden entirely.
   Demonstrates the SuiteApp degrades gracefully on out-of-scope bills.
5. **Tenant config record** (`customrecord_cl_settings`) showing the
   per-account API base + bundle secret + org ID fields blank-but-
   labelled, demonstrating the install flow.
6. **Slack approval card** for an ERP-native bill (write-direction
   evidence — a vendor-bill-arrived-in-NetSuite that Solden tracked
   back into an approval flow). Shows the bidirectional integration.
7. **Optional: Vendor Bill list view** with a "Solden state" custom
   column showing bulk Box state across the AP queue — only if
   Phase 4 ships the list view custom column.

Save as PNG, 1280×800 minimum, no NetSuite-internal data, no real
customer identities. Use Solden's own demo tenant or a sanitised
Cowrywise sandbox.

### A.3 Listing copy

NetSuite's marketplace listing has a strict character budget. Drafts:

#### Tagline (max 80 chars)

> Coordination layer for AP — approve, exception-triage, audit-trace from NetSuite

#### Short description (max 250 chars)

> Solden brings the Clearledgr coordination layer into the NetSuite
> Vendor Bill record. Approve, reject, or escalate AP exceptions
> directly from NetSuite — every decision lands in the audit chain
> with the operator's surface, the agent's reasoning, and the
> validation gate verdict captured.

#### Long description (no hard cap, but keep under 1500 chars for the marketplace summary box)

> Solden is the embedded coordination layer for finance teams. The
> NetSuite SuiteApp closes the loop between NetSuite (the system of
> record for the General Ledger) and Solden (the system of record
> for finance work-in-progress). On every Vendor Bill, the Solden
> subtab surfaces the Box state, the agent's reasoning, the
> validation gate verdict, the timeline, and one-click action
> buttons (Approve / Reject / Request info). For ERP-native bills
> that arrive into NetSuite via EDI, vendor portal, or AP-clerk-
> typed entry, Solden's afterSubmit hook fires an HMAC-signed
> webhook to the Clearledgr coordination layer so the bill becomes
> a Box, runs through validation + exception detection, and routes
> to Slack approval — without leaving NetSuite as the source of
> truth for the GL. Every approval landed from inside the NetSuite
> panel records ``ui_surface=erp_native_netsuite`` on the audit
> chain, so an auditor can prove which surface every decision came
> from. One Box. Many windows.

Pricing: per Solden's product policy, marketplace listings should
say **"Talk to sales for enterprise pricing"** — no specific dollar
figures unless the public pricing page has them. (CLAUDE.md rule:
no fabricated pricing on marketing surfaces.)

### A.4 Demo video

Optional but raises the listing's discovery rank. 90–120 seconds:

1. (0–10s) Open Vendor Bill in NetSuite. Click Clearledgr subtab.
2. (10–30s) Panel loads, show state badge + summary + exceptions +
   timeline.
3. (30–50s) Click Approve. Panel updates to show "approved" state.
4. (50–70s) Switch to Slack — show the corresponding approval card
   updated to "Approved by …". Same workflow, two windows.
5. (70–100s) Show a Vendor Bill that originated in NetSuite (ERP-
   native) — Solden auto-created the Box, routed the approval to
   Slack with the right approver pinged.
6. (100–120s) Cut to Solden's audit-trail UI showing the
   `state_transition` audit row with `ui_surface=erp_native_netsuite`,
   actor email, agent recommendation, validation gate verdict.

Hosted on Solden's own video CDN or YouTube unlisted. Submit URL with
the listing.

---

## Phase B — Functional review (test scenarios)

Oracle's reviewer drives the SuiteApp through these scenarios in a
sandbox you provision. Each scenario should pass without manual
intervention.

### B.1 Read direction (Vendor Bill panel)

| # | Scenario | Expected | Pass |
|---|---|---|---|
| B1 | Open a Vendor Bill **created via Clearledgr's email-arrival flow** (i.e., posted to NetSuite by Solden). Click Clearledgr subtab. | Panel loads in <2s. State badge shows current Solden state (e.g., `posted_to_erp`). Vendor + amount + invoice number match the NetSuite fields. Timeline shows >=1 audit event. Action buttons hidden (post-approval state). | ☐ |
| B2 | Open a Vendor Bill that was **NOT processed through Clearledgr** (e.g., a manually-entered bill from before the SuiteApp was installed). Click subtab. | Panel renders the empty state: *"This Bill was not processed through Clearledgr."* No errors in the browser console. No 500 in the Solden API logs. | ☐ |
| B3 | Open a Vendor Bill where the underlying AP item is at `needs_approval`. | Action buttons (Approve / Reject / Request info) visible. | ☐ |
| B4 | Click **Approve**. | Panel buttons disabled during dispatch. State badge updates to `approved` within 5s. Solden audit log records `state_transition` with `ui_surface=erp_native_netsuite`. NetSuite payment hold (if any) released via REST. | ☐ |
| B5 | Click **Reject** with reason "duplicate". | State badge → `rejected`. Audit row carries the reason text + `ui_surface=erp_native_netsuite`. Bill in NetSuite voided (Phase 2 write-back). | ☐ |
| B6 | Click **Request info**. | State badge → `needs_info`. Audit row records `ui_surface=erp_native_netsuite`, `actor_type=user`. | ☐ |
| B7 | Toggle to **dark mode** in NetSuite (Setup → User Preferences). | Panel CSS still readable. State badge contrast meets WCAG AA. | ☐ |
| B8 | Open the panel on a Vendor Bill in **EDIT** mode (not view). | Panel renders. Action buttons may be disabled (edit mode is for invoice fields, not approval workflow). No errors. | ☐ |
| B9 | Open the panel on a Vendor Bill **CREATE** form (no record id yet). | Subtab is **not** present. (UE script's `beforeLoad` skips when `context.type === CREATE`.) | ☐ |
| B10 | Wait until the JWT in the iframe expires (default 15 min in production). Click Approve. | Panel surfaces the auth error gracefully ("Could not reach Clearledgr"). Refreshing the bill page mints a new JWT. | ☐ |

### B.2 Write direction (afterSubmit webhook)

| # | Scenario | Expected | Pass |
|---|---|---|---|
| W1 | **Create** a new Vendor Bill in NetSuite (without going through Clearledgr). | Solden receives `vendorbill.create` webhook with HMAC signature. New AP item created in `ap_items` with `state=needs_approval` (if NetSuite payment hold) or `posted_to_erp` (no hold). | ☐ |
| W2 | **Edit** an existing Vendor Bill — change the amount. | Solden receives `vendorbill.update` webhook with both the old and new bill summary. AP item's `amount` updated; audit row records the change. | ☐ |
| W3 | **Mark a bill paid** in NetSuite (post a Vendor Payment against it). | Solden receives `vendorbill.paid` webhook. AP item state advances to `paid` / `closed` per the Box state machine. | ☐ |
| W4 | **Delete** a Vendor Bill. | Solden receives `vendorbill.delete` webhook. AP item state advances to `closed` (Box marked terminal-not-paid). | ☐ |
| W5 | Trigger `afterSubmit` for a Vendor Bill where the tenant has **not** provisioned `customrecord_cl_settings`. | UE script logs an audit-level message + skips the webhook silently. NetSuite save is **not** rolled back (must not block the user's save). | ☐ |
| W6 | Trigger `afterSubmit` when Solden's API is **unreachable** (simulate by pointing `apiBase` at a domain that 502s). | UE script logs an error + does not retry inside the same SuiteScript invocation. NetSuite save still completes. (Solden's webhook-retry queue picks up later.) | ☐ |

### B.3 Tenant config

| # | Scenario | Expected | Pass |
|---|---|---|---|
| C1 | Install the SuiteApp on a fresh tenant. Open a Vendor Bill before creating the `customrecord_cl_settings` row. | Panel renders the empty state (no settings → no API base → fallback). No 500. No exception in the logs. | ☐ |
| C2 | Create the `customrecord_cl_settings` row with a valid API base + bundle secret + org ID. Open a Vendor Bill. | Panel loads the Box state. JWT validates server-side. Audit log on Solden's side records the read. | ☐ |
| C3 | Rotate the `bundle_secret` (paste a new value into the custom record + update Solden's `erp_connections.credentials.webhook_secret` simultaneously). Open a Vendor Bill. | Panel still loads — the new secret is picked up on next Suitelet invocation. The previous JWT is rejected with a clear error. | ☐ |

---

## Phase C — Security questionnaire

Oracle's questionnaire is several pages of yes/no with follow-up
detail prompts. Pre-fill the answers below in your submission so
Oracle's reviewer can move quickly.

### C.1 Authentication + authorization

| Question | Solden's answer |
|---|---|
| Does the SuiteApp store any secrets in client-readable JavaScript? | **No.** All secrets live in `customrecord_cl_settings` (server-side custom record) or environment variables on the Solden API. The panel iframe receives only a short-lived JWT (15-minute TTL) minted server-side by the Suitelet. |
| What authentication does the SuiteApp use to call back to Solden's API? | HMAC-SHA256 signed JWT minted by the Suitelet using the per-tenant `bundle_secret` from `customrecord_cl_settings`. The same secret signs the outbound `afterSubmit` webhook. JWT carries `accountId`, `billId`, `userEmail` claims with a 15-minute `exp`. |
| Is the JWT verified server-side on every request? | **Yes.** `clearledgr/api/netsuite_panel.py:_verify_panel_jwt` validates the HMAC signature, the `exp` claim, and cross-checks `accountId`/`billId` claims against the request's path + query params. Rejects otherwise. |
| What happens if the secret is leaked? | Operator rotates the secret in `customrecord_cl_settings` AND in Solden's `erp_connections.credentials.webhook_secret`. All previously-issued JWTs become invalid (signature mismatch). No replay window beyond the 15-minute JWT TTL. |

### C.2 Data handling

| Question | Solden's answer |
|---|---|
| What customer data leaves the NetSuite tenant? | The HMAC-signed `afterSubmit` webhook payload contains the Vendor Bill's structured fields (vendor, amount, currency, invoice number, due date, posting status, payment hold, approval status). No attachments, no narrative notes, no PII beyond what's already on the bill. |
| Is data encrypted in transit? | **Yes.** All API calls are HTTPS with TLS 1.2+. Solden's API enforces HSTS. |
| Is data encrypted at rest? | **Yes** on Solden's side: bank details are Fernet-encrypted before persistence (`ap_items.bank_details_encrypted`); the per-tenant `webhook_secret` is encrypted via `_encrypt_secret`. NetSuite-side: standard NetSuite at-rest encryption applies to `customrecord_cl_settings`. |
| Does the SuiteApp store data outside NetSuite? | **No** records are written outside NetSuite by the SuiteApp itself. Solden's coordination layer stores the AP item Box record + audit chain on Solden's infrastructure (the customer's chosen Solden tenant). |
| What's the data retention policy? | Audit chain is append-only and retained for the life of the Solden tenancy. Operators can export via `/api/audit/export`. AP items are retained per the customer's contracted retention period. |
| GDPR / data-subject requests? | Solden honours data-subject delete + export requests on the AP item layer. Vendor identities embedded in `vendor_intelligence` can be redacted via the platform admin tools. |

### C.3 Operational

| Question | Solden's answer |
|---|---|
| What's the SuiteApp's failure mode if Solden's API is down? | The `beforeLoad` panel renders the error state ("Could not reach Clearledgr"). The Vendor Bill is fully usable in NetSuite — Clearledgr's read panel is purely additive. The `afterSubmit` webhook fires and logs on failure, but does **not** roll back the user's save. Solden's webhook-retry queue picks up missed events later. |
| Does the SuiteApp throttle / rate-limit anything in NetSuite? | **No.** The `afterSubmit` webhook is a single HTTPS POST per Vendor Bill event. The `beforeLoad` adds one Suitelet round-trip per bill view. Neither approaches NetSuite's governance limits. |
| What's the audit chain shape? | Every state transition on a Solden AP item produces an immutable `audit_events` row capturing: actor (user / agent), source channel (slack / teams / gmail / **erp_native_netsuite** for panel decisions), decision context (validation gate verdict, agent recommendation, vendor profile snapshot), timestamp, correlation id. Append-only enforced by DB trigger. |
| How do you handle PII in logs? | Solden's `safe_error()` helper redacts known PII patterns before logging. NetSuite-side, the `log.audit` calls record only the Vendor Bill internal id + the event type, not the bill's monetary fields. |

### C.4 Incident response

| Question | Solden's answer |
|---|---|
| What's the disclosure timeline for a security incident? | Solden notifies affected customers within 24 hours of confirming an incident affecting their tenant, per Solden's standard MSA. |
| Where do customers report security issues? | `security@soldenai.com` — monitored 24/7 by the Solden security oncall. |
| Penetration testing cadence? | Annual third-party pentest (most recent: Q1 of [year]; report available under NDA). |

---

## Phase D — Performance benchmarks

Oracle expects the SuiteApp to add no more than 200ms of perceived
latency per Vendor Bill page load and no more than 500ms per
afterSubmit save.

### Solden's measured performance

Run these in a NetSuite sandbox before submission. Update with real
numbers.

| Operation | Target | Measured |
|---|---|---|
| `beforeLoad` overhead (panel iframe injection) | <100ms | ___ |
| Suitelet first-byte response | <300ms | ___ |
| Solden API `GET /extension/ap-items/by-netsuite-bill/{id}` p50 | <200ms | ___ |
| Solden API p95 | <500ms | ___ |
| Panel render (DOM ready) p50 | <500ms | ___ |
| `afterSubmit` webhook + HMAC sign + POST p50 | <300ms | ___ |
| `afterSubmit` impact on user-perceived save latency | <200ms additional | ___ |

Methodology: 100 sequential Vendor Bill saves + page loads in a
sandbox with 10 concurrent users simulated. Report p50 + p95 + p99.

---

## Phase E — Customer reference

Oracle interviews one deployed customer for 30–60 minutes. The
reviewer asks the customer:

- How long has the SuiteApp been in production?
- What's the deployment scope (single subsidiary, multi-subsidiary,
  multi-currency)?
- How many Vendor Bills/month flow through the SuiteApp?
- Has the SuiteApp caused any NetSuite-side performance issues?
- How was Solden's support during install + day-to-day?
- Are you a reference customer Oracle can list publicly?

Cowrywise is the launch design partner per the README. Brief them
1 week before the call:
- Send the question list.
- Offer a dry-run with Solden's CSM.
- Confirm whether they're willing to be listed publicly (separate
  decision from being a private reference).

---

## After approval

1. Marketplace listing is live → marketing announces it on
   soldenai.com + LinkedIn + the customer newsletter.
2. Update `manifest.json`'s `developer.mpnId` (NetSuite-equivalent) +
   the BFN badge URL.
3. Move the Phase 4 follow-ups (OneWorld subsidiary, credit
   application erp_reference shape, subaccount provisioning UX) into
   the next milestone.
