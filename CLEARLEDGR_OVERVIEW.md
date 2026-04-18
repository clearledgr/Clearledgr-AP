# Clearledgr

**The coordination layer for finance operations.**

---

## What We Build

Finance teams run their workflows on human memory. The ERP holds results. Email holds fragments. Spreadsheets hold trackers. What's in flight, what's blocked, what's next — that lives in the AP Clerk's head, the Controller's head, the CFO's head. Every morning, the workflow is reconstructed from memory and inbox archaeology.

Clearledgr is the missing layer: a persistent, attributable home for every workflow instance, with an agent that advances each one where it can and escalates to a human only on the exception. We call each instance a **Box**. The Box holds state, timeline, exceptions, and outcome. An AP invoice is a Box. A vendor onboarding is a Box. A commission clawback is a Box. The Box is the product. Gmail, Slack, and the ERP are how the Box is rendered.

Finance teams interact with Clearledgr inside the tools they already use. There is no new portal to log into, no new interface to learn, no migration. AP work happens in Gmail — the sidebar renders the Box, the custom route shows the queue, the exceptions surface in the inbox. Approvals happen in Slack. Posted records live in the ERP. Every non-AP action — billing, seat management, audit export — also happens inside Gmail as a native route, following the Streak architectural pattern precisely. Clearledgr has no customer-facing web product surface.

The Box is channel-agnostic by design. Email is the primary ingestion channel for AP in V1 because it's where most AP volume arrives at our wedge customers. Portal-submitted invoices (Ariba, Coupa), EDI feeds, and ERP-direct uploads are covered in V1.5 via ERP-side invoice watching — the Box opens around the document regardless of how it arrived. The coordination layer's value is independent of ingestion channel.

## The Architectural Discipline

The architecture is structured around a single rule: **rules decide, LLM describes.** Every financial calculation, match result, KYC disposition, reversal entry, and ERP write is deterministic. The language model is called only to classify, extract, and summarize — never to decide. This is the line that separates an agent platform from an experiment. A finance team can trust an agent that cannot possibly post the wrong amount. They cannot trust one that might, however rarely.

Clearledgr is built on real engineering discipline. The Chrome extension runs on InboxSDK — the same library Streak uses to sustain multi-million-user Gmail-native workflows across years of Gmail DOM changes without user reinstall. The ERP layer is abstracted: SAP, NetSuite, Xero, and QuickBooks all plug into the same write interface, with idempotency, reversibility, and audit trail guarantees at the agent level, not the connector level. Vendor verification uses open banking, not micro-deposits, which collapses the trust-building step from five days to minutes.

## What's Live

Clearledgr is in production pilot with two design partners:

- **Cowrywise** — Nigeria's leading digital investment platform. Live AP automation going into production before May 2026. Our first paying customer.
- **Booking.com** — Enterprise design partner. Commission clawback pipeline specified to engineering depth against SAP. V1.2 pilot target Q3 2026. Proves the platform thesis on a non-AP workflow.

Across the pilot environment: 500+ real invoices processed, 99% extraction confidence, 3 days of AP time recovered per week for the finance operator.

---

## Why Now

Three shifts are converging.

**Agents crossed the capability threshold.** Until recently, LLMs could read a PDF and describe what was in it, but could not reliably handle the full workflow — match, validate, route, escalate, post. That threshold was crossed in 2024-2025. The window for a new category of finance operations tooling is open now. It won't stay open long.

**CFOs concluded specialist tools didn't solve coordination.** A mid-market finance team typically runs five to ten specialist finance tools alongside the ERP. Each holds one workflow. None hold the state that sits between workflows. The result: the finance team remains the integration layer — now across specialist tools rather than just across raw data. CFOs are actively looking for the consolidator. The question is who.

**Finance headcount is frozen while invoice volume grows.** The trade nobody wants to name: headcount is flat, invoice volume is up, approval cycles are stretching, close timelines are slipping, and audits are reconstructed under duress. Finance teams need leverage. Not a better tool — structural leverage.

## The Team

Three founders, each has built distribution into this surface before.

- **Mo Mbalam**, CEO & Co-founder. Led GTM for Paystack in Ghana (acquired by Stripe). Led BD for Anchor (YC-backed BaaS). 2x founder across Ghanaian fintech. 3 years accounting associate at the University of Ghana. Relocated to the Netherlands in 2024.
- **Joseph Isiramen**, CRO & Co-founder. Enterprise sales at Datadog (EMEA). Revenue motion across European B2B SaaS mid-market. Plauti, Salesmanago — deep network across the target buyer segment.
- **Suleiman Mohammed**, CTO & Co-founder. Repeat founder, prior venture with Mo. Engineering at Siglar Carbon (Norway). Agent architecture depth. Owns the full Clearledgr technical stack.

## Where This Goes

AP is the entry point. Finance coordination is the destination.

| Stage | Workflow | Target |
|-------|----------|--------|
| Live | Accounts Payable | Now |
| V1.2 | Commission Clawback (Booking.com) | Q3 2026 |
| Next | Accounts Receivable | Q4 2026 |
| Then | Reconciliation | H1 2027 |
| Then | Month-End Close | H2 2027 |

Each workflow earns the data and trust to run the next. The Box model generalizes: the same agent architecture, the same deterministic discipline, the same rendering surfaces. What compounds is the customer's dependence — by the time Clearledgr is running AP, clawback, AR, and close, replacing it requires replacing the finance function's entire coordination layer.

---

## Contact

**Mo Mbalam** · CEO & Co-founder
mo@clearledgr.com · +31 6 18 84 81 96 · clearledgr.com

Clearledgr Ltd · Registered in England & Wales

<!-- ─────────────────────────────────────────────────────────────
     INVESTOR CONTEXT
     Include in the investor / partner version.
     Omit for the website About page and general sales outreach.
     ───────────────────────────────────────────────────────────── -->

## Current Raise

Clearledgr is raising a €2M pre-seed round on a €18M post-money SAFE cap. The round funds the team to seed-ready milestones: first paying customer live (Q3 2026), ten paying customers (Q1 2027), commission clawback V1.2 in production with Booking.com, and €1M ARR with the AR module scoped from six months of AP data (Q2 2027).

The round is the minimum capital required to build the platform to a clean seed story. Investor contact: mo@clearledgr.com.
