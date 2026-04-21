# ADR-005: Why four ERPs shipped at V1 (not one)

Status: Accepted
Date: 2026-04-20 (decision re-ratified this session after Mo pushback on my earlier "over-invested" framing)
Author: Mo

## Context

An obvious pre-seed-company heuristic is "build for the customer in front of you." The naive version of that heuristic says: you have one prospect on NetSuite (Cowrywise) and one on SAP (Booking.com). Build those two ERPs. Build QuickBooks and Xero when you have a QuickBooks or Xero prospect.

The heuristic is wrong for ERP breadth in AP. Here's why.

## Decision

**Ship four ERPs at V1: QuickBooks Online, Xero, NetSuite, SAP.** Each is wired at AP-v1 scope (auth + bill post + webhook verification + refresh).

## Consequences

**Wins:**

1. **Sales cycle asymmetry.** Mid-market B2B AP sales cycles run 3-6 months. When a prospect in month 2 of that cycle says "we're on Xero," answering "give us 3 weeks to build the connector" is deal-death. "We support Xero" is deal-life.

2. **Pipeline filter.** Cold outbound opens 10 conversations. 3 are on QuickBooks, 3 on Xero, 2 NetSuite, 2 SAP. With one ERP, 7 self-disqualify. With four, zero do. At pre-seed pipeline volumes, this matters more than feature breadth.

3. **Product-promise anchor.** The pitch is "AP coordination for mid-market finance teams." Finance teams on mid-market-popular ERPs expect integration. One ERP is a different product positioning ("Clearledgr for QuickBooks users").

4. **Architectural forcing function.** Building four forced a real abstraction: `ERPConnection` dataclass (`erp_router.py:158`), uniform webhook contract (`erp_webhook_verify.py`), shared OAuth helpers (`integrations/oauth.py`). Building one would have let QBO-specific assumptions leak into core paths; the second-ERP refactor would have been painful.

5. **Webhook security uniformity.** Each ERP follows its documented signature standard (QBO's `intuit-signature`, Xero's `x-xero-signature` + ITR handshake, timestamp+HMAC for NetSuite/SAP). Doing all four forced us to write a uniform verifier module (`clearledgr/core/erp_webhook_verify.py`) that future ERPs will plug into. Doing one would have left it as a one-off.

**Costs:**

1. **Engineering time.** Approximately 5-6 weeks across all four ERPs (estimate: QBO 1-2 weeks, Xero 1 week, NetSuite 2 weeks for TBA, SAP 1-2 weeks for S/4HANA CPI). Real time, but not four months.

2. **Maintenance surface.** Each ERP has auth refresh logic, sandbox drift against production behavior, API version bumps, rate-limit nuances. Four vendors' APIs to track. Bounded but real.

3. **Test surface.** Every ERP path has a test suite that has to stay green. This session's suite: 2375 tests, a nontrivial fraction covering ERP code.

4. **Sandbox credential costs.** Each ERP sandbox typically has annual or free-tier limits. Four sets of sandbox credentials to maintain (four dev accounts).

## Alternatives considered

- **Ship only QuickBooks at V1, add others when customers require.** Rejected for the sales-cycle reasons above. A V1 with only one ERP is positioned narrower than the product actually is.
- **Ship only NetSuite (Cowrywise) + SAP (Booking.com).** Considered — these are the two active pipeline ERPs. Rejected because Cowrywise isn't closed and cold outbound hasn't finished producing the second customer; pre-scoping to only the two known prospects bets on those two closing.
- **Build a generic "ERP adapter" SDK and ship one reference implementation.** Rejected — over-engineering. The ERPs differ enough in auth models (OAuth2 vs TBA vs OAuth2-with-tenant vs S/4HANA CPI) that a generic SDK would either be paper-thin or leaky. Four concrete adapters + a shared contract (`ERPConnection` + webhook verifier module) is the right shape.

## The meta-principle this ADR encodes

**Build breadth across the integrations your product promise covers. Build depth only for the customer in front of you.**

Four ERPs is product-promise breadth. Each ERP's bill-post is at AP-v1 scope (not the 50 other QBO features we could wire). The first customer who signs will push us to deepen one ERP (their ERP) — that's the right time for depth. V1 breadth today is the table stakes that gets you to the conversation where depth requests arrive.

## Reference

Primary surface: `clearledgr/integrations/erp_router.py` (dispatcher, `ERPConnection`), `clearledgr/integrations/erp_quickbooks.py`, `erp_xero.py`, `erp_netsuite.py`, `erp_sap.py` (per-ERP implementations).
Webhook verification: `clearledgr/core/erp_webhook_verify.py`.
Regression fence: `tests/test_erp_webhook_security.py` (34 tests).
