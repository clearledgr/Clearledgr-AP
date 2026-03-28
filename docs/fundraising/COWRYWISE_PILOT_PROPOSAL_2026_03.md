# Cowrywise Pilot Proposal

Date: 2026-03-25

Status: Draft for next commercial conversation

## Summary

This pilot is designed to prove one thing:

Clearledgr can run AP from Gmail for Cowrywise without manual approval chasing and without duplicate ERP entry.

The pilot is not meant to prove the entire Clearledgr vision. It is meant to prove one narrow, painful workflow with a clear operator, a clear success definition, and a clear path to paid rollout.

## Pilot Goal

For Cowrywise's finance team, Clearledgr will:

- triage AP emails from Gmail
- route approvals automatically
- validate invoice data against NetSuite
- write approved invoices back into NetSuite

The operational outcome is:

- less manual AP handling
- less approval chasing
- less manual data entry after approval

## Primary User

Primary day-to-day operator:

- Finance Manager

Executive sponsor:

- VP Finance

## Current Workflow

Cowrywise's current AP workflow is spread across:

- Gmail
- Slack
- Excel
- NetSuite

Today, the team manually:

- monitors AP emails in Gmail
- triages invoices to the right workflow or entity
- routes and chases approvals
- re-enters approved data into NetSuite

Cowrywise has said the team spends roughly 3 days per week on reconciliation and AP work, and that avoiding further hiring is an important buying trigger.

## In-Scope Pilot Workflow

The pilot should stay narrow.

In scope:

- one AP inbox or clearly defined AP email flow
- one finance manager as primary operator
- one approval-routing workflow
- NetSuite validation and writeback
- multi-entity triage logic as needed for the initial Cowrywise entities in scope

Out of scope for the first pilot:

- full reconciliation workflow
- broad FP&A workflows
- non-AP finance operations
- custom long-tail exception handling outside the agreed pilot cases

## What Clearledgr Will Handle

During the pilot, Clearledgr will:

- process invoice emails from Gmail
- extract invoice data
- triage invoices into the right workflow or entity path
- route invoices for approval
- track approval state
- validate invoice data against NetSuite rules or records
- write approved invoices back into NetSuite
- maintain the workflow and audit trail inside Clearledgr

## What Cowrywise Will Provide

Cowrywise will provide:

- one named day-to-day operator
- one executive sponsor
- access to the AP inbox or forwarding setup
- NetSuite access appropriate for validation and writeback
- Slack approval path and approver mapping
- one weekly operating review during the pilot
- feedback on exceptions, misses, and workflow friction

## Pilot Timeline

Recommended structure: 8 weeks

Recommended kickoff: May 2026

### Week 0

- confirm pilot owner and sponsor
- confirm in-scope entities
- confirm inbox, Slack, and NetSuite setup
- confirm pilot metrics

### Weeks 1-2

- configure Gmail, approval routing, and NetSuite validation
- map entity and approval rules
- run initial dry runs on real AP examples

### Weeks 3-6

- live pilot on in-scope AP workflow
- weekly review of routed approvals, exceptions, and writeback performance
- tighten workflow logic based on real usage

### Weeks 7-8

- measure pilot outcomes
- compare against baseline workflow
- decide on paid rollout and expanded scope

## Success Criteria

The pilot should be judged on operational outcomes, not feature completion.

Recommended pilot metrics:

- % of AP emails triaged correctly
- % of approvals routed without manual follow-up
- % of approved invoices written back to NetSuite without duplicate manual entry
- median approval turnaround time before vs after
- finance-operator hours saved per week

Recommended target framing:

- Clearledgr should materially reduce manual approval chasing
- Clearledgr should materially reduce duplicate ERP entry
- Cowrywise should be able to identify clear weekly time savings for the finance team

## Proposed Commercial Structure

Recommended draft structure:

### Paid pilot

- Duration: 8 weeks
- Fee: $24,000 total
- Payment structure: 50% at kickoff, 50% at start of live pilot

This is the right draft structure if the goal is to keep the pilot real, scoped, and commercially meaningful without forcing a full annual rollout decision before value is proven.

### Rollout after successful pilot

- Initial production workflow: $12,000-$15,000 per month
- Expansion through additional workflows, entity coverage, or adjacent finance operations

This should be positioned as workflow software that removes labor and coordination cost, not as a per-seat reporting tool.

## Recommended Negotiation Posture

### Anchor

- 8-week paid pilot
- $24,000 total
- tightly scoped to one AP workflow and one primary operator

### Acceptable fallback

- 8-week paid pilot
- $18,000-$20,000 total
- only if the scope stays narrow and the path to production pricing is discussed in the same conversation

### Avoid

- free pilot
- vague "let's test it first" language without dates, owner, and metric sheet
- broad custom work before the AP wedge is proven

The point of the pilot is not just product validation. It is to establish that Cowrywise treats this as real operating software with real economic value.

## Decision Framework for Cowrywise

At the end of the pilot, Cowrywise should answer three questions:

1. Did Clearledgr reduce approval chasing?
2. Did Clearledgr remove duplicate ERP entry for approved invoices?
3. Is the value strong enough that this should replace the current Gmail-plus-Slack-plus-Excel workflow for AP?

If the answer is yes to those three questions, the next step is production rollout, not another design-partner cycle.

## Next Meeting Objectives

The next Cowrywise meeting should not end with a vague "let's continue talking."

It should end with:

- one named pilot operator
- one named executive sponsor
- one confirmed pilot start date
- one confirmed in-scope workflow
- one agreed metric sheet
- one pricing discussion anchored to the pilot structure above

If those are not agreed, the relationship is still pre-pilot discovery rather than a committed pilot motion.

## Suggested Close for the Meeting

"We should treat this as a narrow paid pilot, not an open-ended discovery cycle. The goal is simple: prove that Cowrywise can run AP from Gmail without manual approval chasing and without duplicate NetSuite entry. If we agree the workflow, owner, start date, metrics, and pilot fee, we can use the next 8 weeks to prove that clearly."
