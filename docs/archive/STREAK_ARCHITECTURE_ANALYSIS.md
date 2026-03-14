# Streak UX Pattern Analysis (Internal Reference, Not Product Positioning)

## Status and Purpose

This document is an **internal UX pattern analysis** of Streak’s Gmail-native behavior.

It is useful as a design reference for:
1. inbox-native workflow principles
2. context-in-thread UI decisions
3. reducing context switching

It is **not** the canonical Clearledgr product spec and it is **not** external positioning.

Canonical doctrine for Clearledgr AP v1 lives in:

- `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md`

## Why This Document Exists

Streak is a strong reference for one key reason:

> It proves users will adopt serious workflow software inside Gmail when the product respects Gmail’s native interaction model.

That is relevant to Clearledgr because AP work begins in email.

## What We Borrow from Streak (AP v1-Relevant)

### 1. Context in the thread matters

Streak’s strongest pattern is contextualizing the current email/thread, rather than forcing users into a separate workflow app for routine decisions.

For Clearledgr AP v1, that translates to:
1. Gmail thread as AP control surface
2. Invoice status visible in context
3. Exceptions and next action visible without leaving Gmail
4. Lightweight audit breadcrumbs in-context

### 2. Embedded adoption is faster than dashboard-first workflow

Streak shows that workflow adoption improves when:
1. users keep their existing habits
2. the product augments the inbox rather than replacing it
3. context is preserved

For Clearledgr AP v1, this supports the doctrine:
- Gmail = intake/triage and operational context
- Slack/Teams = decisions
- ERP = record system

### 3. Visual status in-list and in-thread is powerful

Streak’s labels/tags/stages are useful patterns.

For Clearledgr AP v1, the analogous pattern is:
1. AP state/status badges
2. exception indicators
3. next-action hints
4. inbox-visible signals (where supported) without overwhelming the thread UI

## What We Do NOT Copy from Streak (AP v1)

### 1. Route-heavy CRM application model (for daily AP)

Earlier versions of this analysis proposed Streak-style full-page routes (Home, Vendors, Analytics, Pipeline) as the primary product shape.

That is **not** the AP v1 doctrine.

Why:
1. It risks turning Clearledgr into another platform/dashboard.
2. AP v1 should be inbox-native and decision-first.
3. Admin/setup views belong in the Admin Console, not the daily AP operator workflow.

### 2. Sidebar as global product navigation

Streak’s patterns can tempt teams to build navigation-heavy embedded chrome.

For Clearledgr AP v1:
1. Gmail sidebar/panel should be **contextual AP workspace**, not global navigation.
2. Global admin/config navigation belongs in `/console`.

### 3. CRM-style pipeline board as the core AP experience

AP v1 should not require users to operate from a board/dashboard view.

A queue/worklist exists, but the operator experience is:
1. one active invoice at a time
2. clear state
3. clear exception
4. clear next action

## Current Clearledgr Interpretation of "Streak-like" (Internal Doctrine)

When Clearledgr uses "Streak-like" internally, it means:
1. workflow in context
2. no daily context switching to another app
3. light UI, heavy backend reliability
4. progressive disclosure
5. embedded status + actions, not generic automation panels

It does **not** mean:
1. "Streak for finance" as external positioning
2. copying Streak’s CRM information architecture
3. route-first dashboard UX for AP v1 daily work

## AP v1 Design Implications (Actionable)

### Gmail (primary operator surface)
Do:
1. Show invoice summary, status, exceptions, next action
2. Keep technical details collapsed
3. Show audit breadcrumbs in-context
4. Keep queue navigation compact

Do not:
1. rebuild a dashboard in the Gmail panel
2. add global nav menus/floating controls that compete with Gmail
3. overload the thread card with admin/configuration controls

### Slack / Teams (approval surfaces)
Do:
1. use clear approval cards with concise context
2. preserve common action semantics across channels
3. deep-link back to Gmail/AP context

Do not:
1. use Slack/Teams as the full workflow system of record UI
2. hide the state of the item after actions are taken

### Admin Console (setup/ops)
Do:
1. centralize setup, integration management, policies, health, and subscriptions

Do not:
1. turn it into the mandatory daily AP work surface for AP v1

## Historical Notes About This Analysis

Earlier versions of this document included architecture recommendations such as:
1. AppMenu route navigation for Home/Vendors/Analytics/Pipeline
2. full-page Gmail route views as primary AP UI
3. route-centric information architecture mirroring Streak CRM

These recommendations are now superseded by the AP v1 doctrine in:

- `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md`

They remain useful only as historical exploration of design options.

## Summary

Streak is a valuable **UX pattern reference** for inbox-native software.

For Clearledgr AP v1, the key lesson is:

> Embed the workflow where finance work begins, but differentiate on execution reliability, policy enforcement, ERP write-back, and auditability.

That is the part Streak-like UX alone cannot provide, and it is where Clearledgr wins.
