# Clearledgr Fundraising Memo

Date: 2026-03-25

## Raise Summary

- Round: Pre-seed
- Amount: $2M-$2.5M
- Instrument: YC post-money SAFE
- Target cap: $15M
- Stretch cap: $17M-$18M if the round is competitive
- Target runway: 18-24 months

## One-Line Pitch

Clearledgr is the AI execution layer for finance teams. Its first production skill runs AP from Gmail: triage invoices, route approvals, validate against ERP, and write approved invoices back without manual approval chasing or duplicate data entry.

Longer term, Clearledgr will expand into adjacent finance workflows with the same manual coordination problem.

## The Problem

Finance work still starts in email, gets coordinated in Slack, gets patched together in spreadsheets, and only gets recorded at the end in ERP.

That creates three persistent problems:

1. AP work starts in the inbox, but finance teams still have to recreate the work somewhere else.
2. The most painful operational bottleneck is chasing approvals, not just parsing invoices.
3. Approved work still has to be keyed into ERP manually, which wastes time and creates errors.

This is why finance teams do not need another dashboard. They need a system that runs the workflow where the work already starts.

## The Product Thesis

Clearledgr is not an AP suite. It is the execution layer for finance work.

The right hierarchy is:

- The company: an AI execution layer for finance teams
- The first production skill: accounts payable
- The current wedge: run AP from Gmail without manual approval chasing or duplicate ERP entry
- The expansion path: adjacent finance workflows like vendor issues, finance exceptions, reconciliation, and close coordination

That hierarchy matters. The wedge has to be sharp enough to buy now. The company has to be broad enough to matter over time.

## What Buyers Actually Buy First

The first thing a finance team buys is not "finance automation" or "invoice processing."

They buy a much more specific outcome:

- no manual approval chasing
- no duplicate ERP entry
- AP handled from Gmail instead of across 4-20 tools

Invoice processing is part of the wedge. It is not the whole wedge.

The strongest initial use case is multi-entity finance teams where invoices arrive in one inbox but need to be triaged, routed, approved, and written back across more than one entity or ERP context.

## Why Gmail

Gmail is the right starting surface because that is where the work already starts.

- invoices already arrive in Gmail
- finance teams already scan and forward AP mail there
- entity and workflow context often starts in the thread
- approvals spill into Slack, but the operator still starts in the inbox
- ERP records the outcome, but it is not where the work begins

The more important point is that the market already admits this workflow exists.

- Ramp and BILL use dedicated AP inboxes and forwarding addresses to pull invoices out of email into their own systems
- Tipalti supports approval by email, but email is still a side channel to the main product
- Stampli explicitly frames fragmented email-thread coordination as the broken old way and centralizes it inside Stampli

So the wedge is not based on an invented workflow. AP already runs through email in practice. The difference is that incumbents treat email as intake, notification, or something to escape from. Clearledgr treats Gmail as the operating surface.

Clearledgr is not forcing finance into a new place. It is putting the workflow into the place they already work from and making that workflow actually run end to end.

## What Clearledgr Is Not

Clearledgr is not:

- a generic OCR tool
- another AP dashboard
- a replacement for ERP
- an "AI accounting" product

The product wins if finance can stay in email while Clearledgr handles triage, approval routing, ERP checks, and writeback.

## Product Today

The product is real.

Current shipped capabilities for the first production skill include:

- Gmail-native AP intake and work surfaces
- invoice processing and extraction
- deterministic validation and policy checks
- confidence-gated human review
- Slack and Teams approval routing
- ERP validation and writeback
- audit trail and canonical AP state transitions

This is not a prototype. The product risk is no longer "can this exist?" The next risk is turning a real wedge into repeatable customer proof.

## Why This Wedge

AP is the right first skill because it is:

- frequent
- painful
- already inbox-native
- operationally repetitive
- directly tied to ERP outcomes

Most importantly, AP exposes the real bottlenecks clearly:

- approval chasing
- duplicate ERP entry
- fragmented work across email, chat, spreadsheets, and ERP

This is also why the positioning is clean. Everyone in AP acknowledges that email is part of the workflow. No one really chooses it as the control surface.

Winning AP from Gmail is the fastest path to proving the broader finance execution thesis.

## Demand Evidence

We have real early signal, but we should describe it honestly.

Current evidence:

- 3 design partners
- 1 committed pilot
- pilot starts in May
- real product, with the current wedge moving from design-partner use into pilot hardening

The strongest current partner is Cowrywise.

What matters about Cowrywise:

- Current workflow: Excel, Gmail, NetSuite, Slack
- Current burden: roughly 3 days per week spent on reconciliation and AP work
- Buyer reaction after seeing the Gmail-native AP triage and approval-routing demo:
  - "wow, I know how I'm already going to use this. we have different entities in Africa and US and the triage will be useful."
  - "if you can do this without us hiring more people, we'd use it"
- Pricing discussions are already underway

Booking.com is useful validation of the market pain, but not yet equivalent buying signal:

- Current environment: roughly 20 internal and external tools
- Reaction: "if you can build this, every organisation would want it"

That is strong category signal, not yet strong product commitment.

## Status Quo

For teams like Cowrywise, the status quo is:

- invoices come into Gmail
- finance manually triages which entity or workflow they belong to
- finance manually routes and chases approvals
- finance manually re-enters approved work into NetSuite

For teams like Booking.com, the status quo is the same problem at larger scale:

- fragmented execution across too many internal and external systems
- unclear control surface
- expensive manual coordination

The real incumbent is not another startup. It is Gmail plus Slack plus Excel plus ERP plus human follow-up.

## Why Now

Three things are true at once:

1. LLMs make inbox-native finance workflow software viable.
2. Finance teams are leaner and cannot keep adding headcount to manual coordination work.
3. ERP systems are still necessary but still not where the work actually gets executed.

The next useful finance software layer is the one that runs the workflow where the work already starts.

## Go-To-Market

The GTM has to stay narrow and operational.

Initial target user:

- finance manager or AP owner
- working inside Gmail today
- collaborating in Slack
- using ERP as system of record, not system of execution

Initial deployment shape:

- one finance team
- one inbox
- one ERP
- one in-scope AP workflow
- high-touch founder involvement

Near-term GTM objective:

- convert design-partner excitement into paid pilot motion
- prove weekly usage by finance operators
- prove measurable time and workflow savings

## Why Us

Clearledgr is not being built by someone who discovered finance ops from the outside, and it is not being sold by a team that does not know how to win enterprise workflows.

The founding-team advantage is unusually direct:

- Mo Mbalam: currently Business Development Lead at Anchor (YC S'22), former Country Sales Manager at Paystack, former Accounting Associate at the University of Ghana finance directorate, and founder of an alternative credit scoring / microlending platform and a digital savings platform in Ghana
- Joseph Isiramen: former account executive at Datadog and former sales manager at Plauti and SalesManago
- Suleiman Mohammed: CTO, former engineering consultant, and Mo's co-founder on the digital savings platform

That matters because the key insight is operational, not abstract:

- the real bottleneck is chasing approvers
- and then entering data again into ERP

That is not the kind of insight most software founders start with. It comes from having lived close enough to the workflow to know what actually hurts and what is just software-category noise.

The second advantage is execution speed. The product is already built far enough to demo the wedge credibly, support design partners, and move into committed pilot conversations. This is not a story deck without a product behind it.

## Business Model

Recommended commercial motion:

- paid pilot first
- then convert into recurring workflow software

Working pricing thesis:

- pilot fee: paid, scoped, and time-bound
- production rollout: $10K-$15K per month for the first AP workflow
- expansion through workflow depth, entity coverage, and adjacent finance workflows

The core economic idea is that Clearledgr is bought to remove labor and coordination cost, not to add another layer of reporting.

## What This Round Must Buy

This round should buy proof.

By the end of this round, Clearledgr should have:

1. 3-5 strong design partners using the AP wedge
2. 2-3 paid pilots or paid conversions
3. repeat weekly operator usage
4. measurable workflow throughput:
   - AP emails triaged
   - approvals routed
   - invoices written back to ERP
   - hours of manual work removed
5. one clear post-AP expansion wedge

## Use of Funds

Recommended use of $2M-$2.5M:

- 50% product and engineering
- 20% product design and workflow polish
- 15% partner deployment and support
- 10% founder-led GTM
- 5% security, infrastructure, and legal

The company does not need to scale headcount broadly yet. It needs to get the wedge working repeatedly in live customer environments.

## Risks

The real risks are not conceptual. They are execution risks:

- turning design partners into paid pilots
- proving workflow reliability across real Gmail and ERP environments
- avoiding a fuzzy "finance platform" story before the AP wedge is truly won
- resisting premature expansion into too many finance workflows at once

## Why $2M-$2.5M

$2M-$2.5M is the right amount because it gives Clearledgr enough runway to:

- finish polishing the Gmail-native AP wedge
- convert design-partner signal into paid pilot proof
- build repeatable deployment confidence
- identify and validate the second wedge

It is large enough to matter and small enough to preserve discipline.

## Closing

Clearledgr should be pitched now as:

- the company: the AI execution layer for finance teams
- today: the first production skill is Gmail-native AP
- over time: adjacent finance workflows with the same manual coordination problem

That is sharp enough to sell, broad enough to matter, and consistent with the strongest evidence in hand.
