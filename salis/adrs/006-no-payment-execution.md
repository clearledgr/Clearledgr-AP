# ADR-006: Why Clearledgr does not move money

Status: Accepted
Date: 2026-04-11 (stated this session; earlier doctrine was implicit)
Author: Mo

## Context

"We automate AP" can mean different things. One interpretation: automate from invoice intake all the way through to paying the bill — initiating the bank transfer, matching the confirmation, closing the loop. That's the Bill.com / Ramp model for AP.

Another interpretation: automate the coordination around AP — get the invoice understood, route approvals, post the bill to the ERP, and stop. The ERP + the customer's AR/banking system handles payment execution downstream.

These are very different products with very different risk profiles.

## Decision

**Clearledgr does not initiate payments. Not in V1. Not in V2.**

The agent's authority ends at "bill posted to ERP." From there, the customer's existing AP-payable process (ERP-side scheduling, bank transfer, whatever they already do) takes over.

Specifically:
- No bank integration for the purpose of initiating a transfer.
- No "schedule payment" action on the coordination engine. (There's a `schedule_payment` handler registered, but it currently only advances Box state and writes audit — it does not actually instruct a bank to transfer funds. See `coordination_engine._handle_schedule_payment`.)
- No payment confirmation polling against a bank; polling is for ERP confirmation of posted bills, not for bank-side settlement.
- No customer-trust-account. We're not a money-movement company.

## Consequences

**Wins:**

1. **Regulatory posture.** Moving money in a regulated way requires licensing: FinCEN registration in the US, MSB licensing in various states, FCA authorization in the UK, similar elsewhere. We don't need any of that. We're a B2B SaaS tool, not a money transmitter.

2. **Liability.** A misrouted payment is a customer-catastrophe event. If we don't initiate payments, we can't cause that catastrophe. The customer's existing bank + bank rails handle that risk on rails the customer already trusts.

3. **SOC 2 scope.** Without money movement, our SOC 2 can stay Type 1 → Type 2 at a normal pace. Payment-moving companies typically need additional attestations (PCI-adjacent, bank-partner requirements).

4. **Sales cycle.** Finance teams ask "does Clearledgr touch our bank account?" If the answer is "no, we only write to your ERP, same as your accountants do," the security-review conversation is dramatically shorter.

5. **Clarity of job-to-be-done.** Clearledgr's value is coordination. Payment execution is a solved problem (every ERP does it, every bank does it). Adding ourselves to that layer doesn't add value proportional to the risk.

**Costs:**

1. We leave revenue on the table from customers who want end-to-end "invoice-to-paid" automation. That's a post-V2 product decision, deliberately deferred.

2. The customer's AR reconciliation still requires a separate tool or a manual step. We don't close the loop to "bill is paid."

3. When someone asks "so does your product actually pay our vendors?", the answer is no. That's a sales honesty cost and occasionally a disqualifying answer for prospects who specifically want a Bill.com replacement.

## Alternatives considered

- **Build payment execution in V2 or V3.** Possible, not committed. Would require: money-movement licensing (or partnership with Wise / Modern Treasury / similar), custodial trust accounts, PCI scope, dedicated incident response for payment failures. All doable, all expensive. Correct product decision deferred until we have revenue justifying it.

- **Partner with a payment rail (Wise, Modern Treasury, Unit) and pass-through.** Partial middle ground. Considered — we'd brand them, they'd carry the regulatory lift. Rejected for V1 because we don't yet know which rail customers prefer; committing to one partner pre-PMF is a bet we don't need to make.

- **Only show customers suggested payments; make them click-through to their bank.** Soft version. Considered and rejected — it's the worst of both worlds (we have to model payment amounts + dates + compliance, but we don't get the value of actually executing). Either we move money or we don't.

## Reference

Confirmed in `main.py` strict runtime profile (no payment-initiation endpoints in allow-lists), `DESIGN_THESIS.md` §4 ("Clearledgr writes, does not own money movement"), and `commission-clawback-spec.md` §Scope ("out-of-scope: initiating recovery transfers").

If this decision is ever reversed, the reversal ADR should address: regulatory licensing plan, PCI scope expansion, bank-partner contract, incident-response runbook for stuck/misrouted payments, insurance coverage expansion.
