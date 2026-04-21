# Product

What ships, what's deferred, why. Written for engineers AND non-engineers — the story you'd tell a new hire on day one.

*Last verified: 2026-04-21 against commit `94c98eb`.*

---

## What is Clearledgr

An **embedded coordination layer for finance teams.** The first workflow class is accounts payable: invoices arrive via email, need approval, get posted to the ERP.

The product is not a new app finance teams learn. It lives inside the tools they already use: Gmail (where invoices arrive), Slack (where approvals happen), ERP (where bills land). The admin view also lives inside Gmail — Streak pattern, not a separate console.

---

## The Box

The core abstraction. Every workflow instance is a **Box** — a persistent, attributable record of:

1. **State** — where in the lifecycle (received / validated / needs_approval / approved / posted_to_erp / ...).
2. **Timeline** — the ordered audit of everything that happened, human and agent alike.
3. **Exceptions** — what went wrong and whether it was resolved. First-class, queryable, attributable.
4. **Outcome** — the terminal result. Posted, rejected, reversed, closed.

All four parts are read together via `GET /api/ap_items/{id}/box`. The deck slide 3 makes this promise; the database schema (`ap_items` + `audit_events` + `box_exceptions` + `box_outcomes`) delivers it.

Two Box types are registered today:

| Box type | Source table | Lifecycle |
|---|---|---|
| `ap_item` | `ap_items` | Invoice received → approved → posted to ERP → paid → closed |
| `vendor_onboarding_session` | `vendor_onboarding_sessions` | Invited → KYC → bank verify → active |

Commission clawback is the third Box type (frozen spec, V1.2).

---

## Four surfaces, one truth

Every Box renders across four surfaces, all reading the same source of truth:

| Surface | Where | What it shows |
|---|---|---|
| **Gmail sidebar** | Inside Gmail, via InboxSDK extension | The Box for the thread the user is viewing. Details, exceptions, waiting-on. |
| **Slack card** | In a configured channel | Approve/Reject/NeedsInfo buttons. Exception notifications. |
| **ERP** | The customer's accounting system (QBO, Xero, NetSuite, SAP) | The posted bill with our `erp_reference`. |
| **Admin console** | Gmail-native (Streak pattern) — ExceptionsPage, PipelinePage, etc. | Cross-Box views: the queue of unresolved exceptions, the pipeline Kanban, activity log. |

None of these are the system of record. All four read the Box. The Box is the product.

**Note:** "Backoffice" in older docs refers to our internal Clearledgr-staff cross-tenant ops console, not a customer surface. That's a separate, future concern. Customer admin lives inside Gmail.

---

## Rules decide, LLM describes

The core architectural principle:

- **Rules decide routing.** The 10-step policy cascade in `ap_decision.py:_compute_routing_decision` picks approve / needs_info / escalate / reject deterministically from validation gate, vendor history, risk score, confidence threshold. No model judgment in the decision path.
- **LLM describes.** Claude is called at exactly five points ([spec §7.1](../AGENT_DESIGN_SPECIFICATION.md)):
  1. `classify_email` — is this an invoice, credit note, query, or irrelevant?
  2. `extract_invoice_fields` — pull vendor, amount, invoice number, due date, with confidences.
  3. `generate_exception_reason` — write the human-readable exception narrative.
  4. `classify_vendor_response` — does this reply address the missing info?
  5. `draft_vendor_response` — compose the outbound vendor email body.

No financial write is at the mercy of model judgment. The LLM's output never branches a routing decision.

---

## What ships in V1

The current scope:

- **Accounts payable full lifecycle** — invoice received in Gmail, extracted, validated, routed for approval (Slack), posted to ERP, payment confirmation polled, reconciled.
- **Four ERPs** — QuickBooks Online, Xero, NetSuite, SAP S/4HANA.
- **Gmail as the work surface** — MV3 Chrome extension, seven InboxSDK injection points.
- **Slack as the decision surface** — approval cards, exception cards, digest summaries.
- **Vendor portal** — magic-link unauthenticated HTML for KYC + bank details submission.
- **Multi-tenant isolation** — every row org-scoped, every route org-guarded.
- **Audit trail + customer webhooks** — Box contract (state/timeline/exceptions/outcome) fully wired.

---

## What's deferred

Flagged off or explicitly out of scope for V1:

- **Teams** — `FEATURE_TEAMS_ENABLED=false`. Code exists; register routes by flipping the flag. V1.1.
- **Outlook** — `FEATURE_OUTLOOK_ENABLED=false`. Same story. V1.1.
- **Commission clawback** — second workflow class. Spec frozen pending V1 launch. V1.2.
- **KYC + open-banking providers** — stubbed; adapters in place, real provider integration pending contracts. Q3 2026.
- **Additional ERPs** (Sage Intacct, MYOB, others) — per-customer if they ask, not pre-built.
- **Temporal** — we ripped it out (migration v32). The deterministic planning + coordination engines + Redis Streams event queue are the durable runtime. No workflow engine abstraction.
- **Payment execution** — we coordinate payment, we don't execute it. ERPs + banks own the money movement. See ADR 006.

---

## What we've explicitly NOT built

Things that look relevant but we chose not to:

- **A general-purpose agent platform.** This is a finance coordination layer, not a finance agent kit. One opinionated flow, not a toolbox.
- **A "new" dashboard app.** No separate web app for customers. Everything is Gmail-native or Slack-native.
- **A database-per-tenant architecture.** Single Postgres, `organization_id` on every row, tenant isolation enforced at the route + store layer. Simpler, audit-trail is uniform.
- **Feature flags for core product flows.** Flags are for deferred surfaces (Teams, Outlook). The AP flow itself is either shipped or not; we don't run A/B routing experiments on financial writes.

---

## Competitive positioning (one paragraph)

Reference patterns we draw from:

- **Streak** — CRM inside Gmail. The admin surface is native to the work surface. Clearledgr applies this to AP.
- **Fyxer** — invisible AI. Email auto-replies drafted in the background, user keeps their existing workflow. Clearledgr applies this: the agent handles the mechanical work; the human makes the decisions at the right moments.

What we don't want to be:

- **Bill.com** — they own the full invoice + payment stack. We don't. We coordinate across what the customer already has.
- **A generic "AP automation" vendor** — the market has 20 of these. They're batch-processing software with a Slack bot glued on. We're Gmail-native coordination with a Box abstraction.

The positioning story lives in `CLEARLEDGR_MEMO.md` and the pitch deck. This doc just names the references.

---

## Product principles (informal but operative)

1. **The Box is the product.** If an engineering choice makes Boxes harder to reason about, we're choosing wrong.
2. **Work where the customer already works.** Don't build a new app. Extend Gmail. Push decisions to Slack. Write to the ERP.
3. **Trust is built by the audit trail.** Every side effect is auditable. The customer can reconstruct any Box's history from the database.
4. **Rules decide. LLM describes.** No financial write gated on model judgment. Ever.
5. **Ship the boring parts first.** State machine, audit trail, tenant isolation, retry logic. Before fancy demos.
6. **Don't normalize sloppy software.** Flaky tests, silent excepts, unhandled exceptions — these are bugs, not flavor. Fix the defect, not the symptom.

---

## For new engineers: what the deck promises customers

Short version (read the deck for full): "Clearledgr is where your AP runs. Every invoice becomes a Box with full state, timeline, exceptions, and outcome. Rules decide. Claude describes. Your team keeps working in Gmail and Slack; nothing changes except that the boring part takes care of itself."

If your code moves against any of those commitments, you're building the wrong thing. Flag it; don't hide it.

---

## Where this is in the codebase

| Theme | File(s) |
|---|---|
| Box abstraction | `clearledgr/core/box_registry.py`, `clearledgr/core/stores/box_lifecycle_store.py` |
| AP state machine | `clearledgr/core/ap_states.py` |
| Vendor onboarding state machine | `clearledgr/core/vendor_onboarding_states.py` |
| Rules-based routing | `clearledgr/services/ap_decision.py` |
| Five LLM actions | `clearledgr/core/coordination_engine.py` handlers + `clearledgr/core/llm_gateway.py` |
| Audit trail | `clearledgr/core/stores/ap_store.py:append_audit_event` (the funnel) |
| Gmail surface | `clearledgr/api/gmail_*`, `clearledgr/services/gmail_*`, `ui/gmail-extension/` |
| Slack surface | `clearledgr/api/slack_invoices.py`, `clearledgr/services/slack_*` |
| ERP surface | `clearledgr/integrations/erp_*` |
| Customer admin (inside Gmail) | `clearledgr/api/box_exceptions_admin.py`, `ui/gmail-extension/src/routes/pages/ExceptionsPage.js` |

---

## Related reading

- `../DESIGN_THESIS.md` — the full §1–§19 product doctrine.
- `../CLEARLEDGR_MEMO.md` — external-facing identity, positioning, how we talk about the product.
- `../AGENT_DESIGN_SPECIFICATION.md` — the agent architecture spec.
- [`architecture.md`](./architecture.md) — where the pieces live.
- [`adrs/001-box-abstraction.md`](./adrs/001-box-abstraction.md) — why Box-first.
- [`adrs/003-gmail-slack-erp-surfaces.md`](./adrs/003-gmail-slack-erp-surfaces.md) — why these three (plus admin-inside-Gmail).
