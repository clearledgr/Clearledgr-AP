# YC Application Draft

Date: 2026-03-25

This is a working draft for a YC application based on the current Clearledgr story.
It is optimized for clarity, specificity, and wedge-first framing.

For compressed form-field answers, use [`YC_APPLICATION_SHORT_2026_03.md`](/Users/mombalam/Desktop/Clearledgr.v1/docs/fundraising/YC_APPLICATION_SHORT_2026_03.md).

## Company

Clearledgr

## Founders

- Mo Mbalam
- Joseph Isiramen
- Suleiman Mohammed

## One-line description

Clearledgr is an AI execution layer for finance teams. The first production skill runs AP from Gmail.

## What are you building?

We are building an AI execution layer for finance teams. The first production skill runs AP from Gmail.

Today, invoices arrive in Gmail, approvals happen in Slack, exceptions get tracked in spreadsheets, and ERP only gets updated at the end. Finance teams still have to coordinate all of that manually.

Clearledgr reads invoice emails, triages them into the right workflow or entity, routes approvals, checks invoices against ERP, and writes approved invoices back. The immediate wedge is AP from Gmail, but the company is broader than AP.

## What is the problem and why is it important?

The problem is not just invoice capture. The real problem is that finance teams still run critical AP work manually across too many tools.

The two biggest bottlenecks we keep seeing are:

- chasing approvers
- entering approved data into ERP again by hand

That matters because finance teams are already lean, and this work absorbs real time every week. At Cowrywise, the team said they spend roughly 3 days a week on reconciliation and AP work. This is repetitive operational work that should not still depend on Gmail, Slack, Excel, and ERP being stitched together by hand.

## Why are you the right people to build this?

We are not coming at this from the outside.

Mo Mbalam has worked with finance teams for 10 years and spent 4 years as an Accounting Associate at the University of Ghana finance directorate. He is currently Business Development Lead at Anchor (YC S'22), was previously Country Sales Manager at Paystack, and previously founded an alternative credit scoring / microlending platform and a digital savings platform in Ghana.

That matters because the key insight here is operational, not theoretical. The painful part of AP is not just parsing invoices. It is chasing approvals, triaging work across entities and systems, and then entering the same data into ERP again.

The founding team is also balanced across domain, sales, and product execution:

- Mo Mbalam: finance workflow domain context, sales, GTM, and founder experience
- Joseph Isiramen: enterprise sales experience from Datadog, Plauti, and SalesManago
- Suleiman Mohammed: CTO, former engineering consultant, and Mo's co-founder on the digital savings platform

## How long have you been working on this? How far along are you?

We have a real product and the current wedge is already working in software.

The first production skill already supports:

- Gmail-native AP intake and work surfaces
- invoice processing and extraction
- review surfaces for uncertain fields and exceptions
- approval routing in Slack and Teams
- ERP validation and writeback
- audit trail and workflow state control

This is not a concept or prototype. The current stage is turning design-partner signal into paid pilot proof and tightening the wedge into something repeatable.

## Do people want it?

We currently have 3 design partners and 1 committed pilot.

The strongest signal is Cowrywise. After seeing the Gmail-native AP triage and approval-routing demo, their VP Finance said:

- "wow, I know how I'm already going to use this. we have different entities in Africa and US and the triage will be useful."
- "if you can do this without us hiring more people, we'd use it"

We are already discussing pricing with them, and the pilot is scheduled to start in May.

We also had a conversation with Booking.com where the reaction was:

- "if you can build this, every organisation would want it"

Booking.com is useful market validation. Cowrywise is the stronger commercial signal because it is tied to a real workflow and an active buying motion.

## Who is the customer?

The first user is the finance manager or AP owner running AP from Gmail in a multi-entity finance team.

The first buyer is usually the VP Finance or CFO who already knows the team is losing time to approval chasing and manual ERP entry.

## What is the narrowest wedge?

Gmail-native AP triage, invoice processing, approval routing, ERP validation, and ERP writeback.

## What are customers doing today instead?

Today they use a stack like:

- Gmail
- Slack
- Excel
- NetSuite or another ERP

The current workaround is manual and fragmented:

- watch AP emails in Gmail
- manually triage invoices
- manually route and chase approvals
- manually re-enter approved data into ERP

That patched-together workflow is the real incumbent.

## Why now?

Three things are true at once:

1. LLMs now make it practical to work directly from messy email and document inputs.
2. Finance teams are leaner and cannot keep adding headcount to coordination-heavy work.
3. ERP is still necessary, but it still does not run the actual workflow.

That makes this the right time to build software that runs AP from the inbox where the work already starts.

## What did you learn from users that surprised you?

The biggest surprise was that the real bottleneck was not invoice processing itself. It was chasing approvers and entering data into ERP again.

That changed the product framing. We still handle invoice processing, but we no longer think of the wedge as generic invoice automation. We think of it as running AP from Gmail without manual approval chasing and duplicate ERP entry.

## Why could this become a large company?

AP is the first wedge because it is frequent, painful, inbox-native, and directly tied to ERP outcomes.

But the same pattern exists across other finance workflows: work starts in email or chat, gets coordinated manually, and only gets recorded later in a system of record. If Clearledgr wins AP from Gmail, it can expand naturally into vendor issues, finance exceptions, reconciliation, and close workflow coordination.

## Revenue / pilots

We are discussing pricing with Cowrywise now.

The first committed pilot is scheduled to start in May.

Our current commercial plan is:

- paid pilot first
- then production rollout as workflow software

Working pricing thesis:

- 8-week paid pilot
- then $10K-$15K/month for the first AP workflow

## Anything else worth saying

The sharpest way to understand Clearledgr right now is:

- sell one painful workflow first: AP from Gmail
- win by removing approval chasing and duplicate ERP entry
- expand later into adjacent finance workflows with the same manual coordination problem

## Shorter backup answers

### 50-character version

Finance execution layer, starting with AP

### 1-sentence version

Clearledgr is an AI execution layer for finance teams whose first production skill runs AP from Gmail by processing invoices, routing approvals, validating against ERP, and writing approved work back without manual chasing or duplicate entry.

### Why now in one sentence

LLMs now make it practical to work directly from messy email and document inputs at exactly the moment finance teams are leaner and ERP still does not run the actual workflow.

### Why us in one sentence

We combine direct finance-ops experience, enterprise sales experience, and engineering execution, and we know from firsthand experience that the real pain is approval chasing and duplicate ERP entry, not just invoice capture.
