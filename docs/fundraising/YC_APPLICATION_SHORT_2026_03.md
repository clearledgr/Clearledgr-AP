# YC Application Short Answers

Date: 2026-03-25

Use this version for short YC form fields and interview prep. Answers are intentionally compressed.

## 50-character description

Finance execution layer, starting with AP

## 1-line description

Clearledgr is an AI execution layer for finance teams; the first skill runs AP from Gmail.

## What are you building?

We are building an AI execution layer for finance teams. The first production skill runs AP from Gmail. Clearledgr reads invoice emails, triages them into the right workflow or entity, routes approvals, checks invoices against ERP, and writes approved invoices back. The goal is to remove approval chasing and duplicate ERP entry.

## What problem are you solving?

Finance teams still run AP across Gmail, Slack, spreadsheets, and ERP. Invoices arrive in email, approvals happen in chat, and approved work still gets keyed into ERP by hand. The real bottlenecks are chasing approvers and entering data twice.

## Why now?

LLMs now make it practical to work directly from messy email and document inputs. At the same time, finance teams are leaner, and ERP still does not run the actual workflow.

## Why this team?

Mo Mbalam has worked with finance teams for 10 years and spent 4 years as an Accounting Associate at the University of Ghana finance directorate. He is now Business Development Lead at Anchor (YC S'22), was formerly Country Sales Manager at Paystack, and previously founded a credit scoring / microlending platform and a digital savings platform in Ghana. Joseph Isiramen brings enterprise sales experience from Datadog, Plauti, and SalesManago. Suleiman Mohammed is CTO, a former engineering consultant, and Mo's prior co-founder.

## How far along are you?

We have a real product and the current wedge is already working in software. Clearledgr already supports Gmail-native AP intake, invoice processing, review surfaces, approval routing, ERP validation and writeback, and audit trail / workflow state control. The current work is pilot hardening and turning design-partner interest into repeatable proof.

## Do people want it?

We have 3 design partners and 1 committed pilot. Cowrywise is in pricing discussions with us and starts a pilot in May. After our demo, their VP Finance said: "wow, I know how I'm already going to use this" and "if you can do this without us hiring more people, we'd use it." We have also heard broader market-validation feedback from Booking.com that this pain exists beyond one company, but Cowrywise is the stronger commercial signal because it is tied to an active buying motion.

## Who is the customer?

The first user is the finance manager or AP owner running AP from Gmail in a multi-entity finance team. The first buyer is usually the VP Finance or CFO who already knows the team is losing time to approval chasing and manual ERP entry.

## What is the narrowest wedge?

Gmail-native AP triage, invoice processing, approval routing, ERP validation, and ERP writeback.

## What are customers doing today instead?

Today they watch AP emails in Gmail, manually triage invoices, manually route and chase approvals in Slack, and manually re-enter approved work into ERP. The real incumbent is Gmail plus Slack plus Excel plus ERP plus human follow-up.

## What did you learn from users that surprised you?

The biggest surprise was that the real bottleneck was not invoice processing itself. It was chasing approvers and entering data into ERP again.

## Why could this be a large company?

AP is the first wedge because it is frequent, painful, inbox-native, and directly tied to ERP outcomes. If we win that workflow, we can expand into vendor issues, finance exceptions, reconciliation, and close workflows that have the same pattern: work starts in email or chat, gets coordinated manually, and only gets recorded later.

## Revenue / pilot answer

We are discussing pricing with Cowrywise now. Our first committed pilot starts in May. Current pricing thesis is an 8-week paid pilot, then $10K-$15K per month for the first AP workflow.

## Interview soundbites

- Business: first skill is AP from Gmail
- Company: finance execution layer, starting with AP
- Real pain: approval chasing and duplicate ERP entry
- Best proof: Cowrywise spends roughly 3 days a week on reconciliation and AP
