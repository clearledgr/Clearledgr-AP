# Clearledgr Gmail Extension - Design Specification v1.2

> **Update (Mar 23, 2026):** Clearledgr is an embedded finance-ops execution layer. The **Gmail/AP wedge** is designed as **Streak for finance ops**. [`DESIGN.md`](/Users/mombalam/Desktop/Clearledgr.v1/DESIGN.md) is the primary source of truth for overall product design doctrine, while this file remains the Gmail-extension-specific spec.

> **Current Gmail doctrine:**  
> - the `Clearledgr AP` thread panel is the daily execution surface  
> - `Home` is the start page and hub  
> - `Pipeline` is the hero queue/work surface  
> - `Review` and `Upcoming` are focused follow-up surfaces  
> - `Connections` and admin tools are secondary pages, not peers in the main work path

> **Historical note:** Part A FAB/batch-queue ideas and any dark-mode references below are legacy exploration. They should not guide new implementation unless explicitly revived.

> **Purpose:** Run finance work from inbox to ledger within Gmail.  
> **Philosophy:** Finance teams should work from the inbox, not leave it.

---

## Table of Contents

### Part A: Intake Layer (Inbox Automation)
1. [System Overview](#1-system-overview)
2. [Background Processing](#2-background-processing)
3. [FAB & Queue Badge](#3-fab--queue-badge)
4. [Batch Processing View](#4-batch-processing-view)
5. [Queue Management](#5-queue-management)

### Part B: Execution Layer (Sidebar)
6. [Sidebar Structure](#6-sidebar-structure)
7. [Sidebar Cards](#7-sidebar-cards)
8. [Visual Hierarchy](#8-visual-hierarchy)
9. [Color Palette](#9-color-palette)
10. [Typography](#10-typography)
11. [Interaction Model](#11-interaction-model)
12. [Mobile Behavior](#12-mobile-behavior)
13. [Loading States](#13-loading-states)
14. [Dark Mode](#14-dark-mode)
15. [Accessibility](#15-accessibility)
16. [Implementation Checklist](#16-implementation-checklist)

---

# PART A: INTAKE LAYER (Inbox Automation)

---

## 1. System Overview

### The Problem

```
CURRENT (Broken) Flow:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Finance person opens inbox
        ↓
Manually scans for financial emails (WASTE)
        ↓
Clicks email to open
        ↓
Sidebar shows
        ↓
Post to ledger
        ↓
Repeat 50+ times per day (BURNOUT)
```

### The Solution

```
CLEARLEDGR Flow:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Emails arrive
        ↓
Clearledgr auto-identifies financial ones (BACKGROUND)
        ↓
FAB badge shows: "12 invoices pending"
        ↓
Click FAB → Opens batch queue view
        ↓
Process all 12: triage → match → post (SEQUENTIAL)
        ↓
Auto-archives as "Posted"
        ↓
Next batch when new emails arrive (CONTINUOUS)
```

### Two Layers

| Layer | Purpose | Trigger |
|-------|---------|---------|
| **Intake** | Find & queue financial emails | Automatic (background) |
| **Execution** | Process individual email → ledger | User clicks from queue |

---

## 2. Background Processing

### Auto-Identification Engine

Runs automatically when:
- Gmail tab is open (active or background)
- New emails arrive
- User navigates to inbox
- Periodic scan (every 5 minutes)

### Scan Logic

```
For each unread email in Inbox:
  1. Check if already processed (has Clearledgr label)
  2. If not, run financial detection:
     - Subject line patterns (invoice, receipt, payment, statement)
     - Sender patterns (known vendors, @billing., @invoices.)
     - Attachment types (PDF, CSV with financial names)
     - Body content (amounts, dates, PO numbers)
  3. Calculate confidence score (0-100%)
  4. If confidence > 50%:
     - Add to pending queue
     - Apply label: Clearledgr/Pending
     - Update FAB badge count
```

### Detection Patterns

| Signal | Weight | Examples |
|--------|--------|----------|
| Subject contains "invoice" | +30 | "Invoice #12345" |
| Subject contains "payment" | +25 | "Payment confirmation" |
| Subject contains "receipt" | +20 | "Your receipt from..." |
| Known vendor sender | +40 | stripe.com, quickbooks.com |
| PDF attachment | +15 | invoice.pdf, receipt.pdf |
| Amount in body ($X,XXX) | +20 | "$2,450.50 due" |
| Date pattern in body | +10 | "Due: Jan 30, 2026" |

### Confidence Thresholds

| Score | Action |
|-------|--------|
| 95%+ | Auto-queue, auto-match in background |
| 70-94% | Queue for review, show in batch view |
| 50-69% | Queue with "?" badge, needs manual triage |
| <50% | Ignore, don't queue |

### Labels Applied

| Stage | Label |
|-------|-------|
| Detected | `Clearledgr/Pending` |
| Triaged | `Clearledgr/Invoices/Unmatched` |
| Matched | `Clearledgr/Invoices/Matched` |
| Posted | `Clearledgr/Invoices/Posted` |
| Exception | `Clearledgr/Exceptions` |
| Skipped | `Clearledgr/Skipped` |

---

## 3. FAB & Queue Badge

### FAB States

#### State 1: Empty Queue (Idle)

```
┌─────────┐
│   [C]   │  ← Clearledgr logo only
│         │  ← Subtle, doesn't distract
└─────────┘
```

- No badge
- Muted appearance (60% opacity)
- Click opens empty state sidebar

#### State 2: Pending Items

```
┌─────────┐
│   [C]   │  ← Full opacity
│    12   │  ← Red badge with count
└─────────┘
```

- Badge: Red circle, white text
- Count: Number of pending financial emails
- Pulse animation on new items (subtle)
- Click opens batch view

#### State 3: Processing

```
┌─────────┐
│   [↻]   │  ← Spinner replacing logo
│    8    │  ← Remaining count
└─────────┘
```

- Shows during batch processing
- Count decrements as items complete

### Badge Behavior

| Count | Display |
|-------|---------|
| 0 | No badge |
| 1-99 | Exact number |
| 100+ | "99+" |

### Badge Animation

- New item detected → Badge scales up 1.2x, then back (200ms)
- Item processed → Count decrements with fade transition

---

## 4. Batch Processing View

### Layout

When FAB is clicked with pending items, show batch view instead of single-email sidebar:

```
┌────────────────────────────────────────────┐
│ [C] Clearledgr              [−] [⚙] [X]   │ ← Header
├────────────────────────────────────────────┤
│                                            │
│ 12 invoices pending                        │ ← Queue title
│ ━━━━━━━━━━━━━━━━━━━━━━━━━━━                │ ← Progress bar
│ Processed 0 of 12                          │
│                                            │
│ ┌────────────────────────────────────────┐ │
│ │ ● Acme Corp Invoice #2847              │ │ ← Current item
│ │   $2,450.50 · PDF attached             │ │   (highlighted)
│ │   98% confidence                       │ │
│ └────────────────────────────────────────┘ │
│                                            │
│ ┌────────────────────────────────────────┐ │
│ │ ○ Stripe Receipt                       │ │ ← Queue item
│ │   $129.00 · Dec 15                     │ │
│ └────────────────────────────────────────┘ │
│                                            │
│ ┌────────────────────────────────────────┐ │
│ │ ○ AWS Invoice                          │ │
│ │   $847.23 · PDF attached               │ │
│ └────────────────────────────────────────┘ │
│                                            │
│ ┌────────────────────────────────────────┐ │
│ │ ? Unknown Sender                       │ │ ← Low confidence
│ │   Needs review                         │ │   (yellow badge)
│ └────────────────────────────────────────┘ │
│                                            │
│ ... (scrollable)                           │
│                                            │
├────────────────────────────────────────────┤
│ [▶ Start Processing]        [Skip All]    │ ← Actions
└────────────────────────────────────────────┘
```

### Queue Item States

| State | Indicator | Color |
|-------|-----------|-------|
| Pending | ○ Empty circle | Gray |
| Current | ● Filled circle | Teal |
| Completed | Check Checkmark | Green |
| Skipped | − Dash | Gray |
| Exception | ! Exclamation | Red |

### Batch Actions

| Button | Action |
|--------|--------|
| **Start Processing** | Begin sequential processing, opens first item in execution sidebar |
| **Skip All** | Mark all as skipped, close batch view |
| **Process Selected** | Only process checked items |

---

## 5. Queue Management

### Processing Flow

```
User clicks "Start Processing"
        ↓
Navigate to first email in Gmail
        ↓
Open execution sidebar (single-email view)
        ↓
User completes triage → match → post
        ↓
Auto-archive email, apply "Posted" label
        ↓
Auto-navigate to next email in queue
        ↓
Repeat until queue empty
        ↓
Show completion summary
```

### Auto-Navigation

After each email is processed:
1. Current email archived (moves out of inbox)
2. Label applied: `Clearledgr/Invoices/Posted`
3. Sidebar auto-loads next email from queue
4. Gmail view navigates to next email
5. No user clicks needed between items

### Completion Summary

When queue is empty:

```
┌────────────────────────────────────────────┐
│ Check Batch Complete                           │
├────────────────────────────────────────────┤
│                                            │
│         🎉                                 │
│                                            │
│    12 invoices processed                   │
│    $24,847.50 total posted                 │
│    ~18 minutes saved                       │
│                                            │
│ ┌────────────────────────────────────────┐ │
│ │ Posted: 10                             │ │
│ │ Exceptions: 1                          │ │
│ │ Skipped: 1                             │ │
│ └────────────────────────────────────────┘ │
│                                            │
│ [View Posted]      [View Exceptions]       │
│                                            │
│ [Done]                                     │
└────────────────────────────────────────────┘
```

### Queue Persistence

- Queue stored in `chrome.storage.local`
- Survives tab refresh, browser restart
- Clears only when items are processed/skipped
- Syncs across Gmail tabs (if multiple open)

---

# PART B: EXECUTION LAYER (Sidebar)

The following sections describe the single-email processing sidebar, which opens when:
- User clicks an item from batch queue
- User manually clicks an email that Clearledgr detected
- Processing flow auto-advances to next email

---

---

## 6. Sidebar Structure

### Core Principles

| Principle | Description |
|-----------|-------------|
| **One job, done perfectly** | Execute finance workflows from email to ledger. No distractions. |
| **Zero context switching** | Everything visible in one glance. Never ask user to leave Gmail. |
| **Trust through transparency** | Show confidence scores, match quality, audit trail. User always knows why. |
| **Progressive disclosure** | Basic info prominent, details expandable. Scan in 3 seconds. |
| **Invisible when working** | Sidebar feels native to Gmail, not bolted-on. |

---

## 2. Dimensions & Layout

### Sidebar Dimensions

| Property | Value |
|----------|-------|
| Width (desktop) | 420px |
| Width (mobile) | 100% |
| Height | 100vh (full viewport) |
| Mobile breakpoint | 768px |
| Resize minimum | 280px |
| Resize maximum | 600px |
| Resize handle | 8px drag zone on left edge |

### Spacing Standards

| Element | Value |
|---------|-------|
| Internal padding | 16px per section |
| Card gap | 12px between sections |
| Text line height | 1.5 |
| Icon size (actions) | 16px |
| Icon size (card titles) | 20px |

---

## 3. Structure

### Overview (Top to Bottom)

```
┌─────────────────────────────────┐
│ A. STICKY HEADER (64px)         │ ← Always visible
├─────────────────────────────────┤
│ B. SCROLLABLE CONTENT           │
│   ┌─────────────────────────┐   │
│   │ Card 1: Triage          │   │ ← Classification
│   ├─────────────────────────┤   │
│   │ Card 2: Email Context   │   │ ← Source details
│   ├─────────────────────────┤   │
│   │ Card 3: Transaction     │   │ ← HERO SECTION
│   │         Match           │   │
│   ├─────────────────────────┤   │
│   │ Card 4: Journal Entry   │   │ ← Preview
│   ├─────────────────────────┤   │
│   │ Card 5: Actions         │   │ ← Primary CTA
│   ├─────────────────────────┤   │
│   │ Card 6: Audit Trail     │   │ ← Compliance
│   └─────────────────────────┘   │
├─────────────────────────────────┤
│ C. RESIZE HANDLE (8px)          │ ← Left edge
└─────────────────────────────────┘
```

---

### A. Sticky Header (64px)

**Layout:**
```
┌──────────────────────────────────────────┐
│ [Logo] Clearledgr          [−] [⚙] [X]  │
└──────────────────────────────────────────┘
```

**Left side:**
- Clearledgr logo: 24×24px, square, teal gradient
- "Clearledgr" text: 15px, weight 600, black

**Right side (3 buttons):**
- Minimize: Collapse sidebar (toggle with Alt+K)
- Settings: Open extension options
- Close: Close on mobile, minimize on desktop

**Style:**
- Background: `#ffffff`
- Bottom border: 1px `#e8e8e8`
- Backdrop blur: 10px (glassmorphic)
- No shadow

**Behavior:**
- Stays fixed at top
- Buttons always accessible
- Settings opens popup, doesn't navigate

---

### B. Scrollable Content (6 Cards)

#### Card 1: Triage Classification

**Purpose:** Confirm email is financial and classify type.

##### State 1 — High Confidence (95%+) [Collapsed]

```
┌────────────────────────────────────────┐
│ Detected as Invoice       [Invoices]   │
└────────────────────────────────────────┘
```

- Single line display
- Green checkmark icon
- Auto-applies label: `Clearledgr/Invoices/Unmatched`
- User doesn't interact; auto-collapses
- Shows label badge

##### State 2 — Moderate Confidence (70-95%) [Expanded]

```
┌────────────────────────────────────────┐
│ Likely Invoice (73%)                   │
├────────────────────────────────────────┤
│ Confirm Classification                 │
│                                        │
│ Vendor name found: Acme Corp           │
│ Amount detected: $2,450.50             │
│ PDF attachment present                 │
│ PO number not found                    │
│ Due date not found                     │
│                                        │
│ [Confirm & Continue]                   │
│ [Not Financial]                        │
└────────────────────────────────────────┘
```

- Yellow warning badge
- Bulleted detected attributes
- Two action buttons (full width, 40px each)
- On confirm → Apply label, continue
- On reject → Close sidebar, no label

##### State 3 — Low Confidence (<70%) [Manual Input]

```
┌────────────────────────────────────────┐
│ ? Unable to Classify                   │
├────────────────────────────────────────┤
│ Classify This Email                    │
│                                        │
│ ◯ Invoice (AP document)                │
│ ◯ Payment (cash movement)              │
│ ◯ Statement (reconciliation doc)       │
│ ◯ Skip (not financial)                 │
│                                        │
│ [Continue] (disabled until selection)  │
└────────────────────────────────────────┘
```

- Gray question badge
- Radio button group (4 options)
- Must select before proceeding
- Visual feedback on hover

---

#### Card 2: Email Context

**Purpose:** Show source email details.

```
┌────────────────────────────────────────┐
│ 📧 Email Context                       │
├────────────────────────────────────────┤
│ From:    vendor@acme.com               │
│ Subject: Invoice #12345 - Services     │
│ Date:    Jan 25, 2026 at 2:14 PM       │
│ Files:   [📎 Invoice.pdf]              │
│                                        │
│ ████████████░░░░░░ (confidence bar)    │
│                                        │
│ [Doc Clearledgr/Invoices/Unmatched]     │
│ Classified & Ready for Analysis        │
└────────────────────────────────────────┘
```

**Elements:**
- From, Subject (truncated if long), Date, Attachments
- Confidence bar: 8px height, teal fill, no text %
- Label badge with icon
- Status text: gray, 12px

---

#### Card 3: Transaction Match (HERO)

**Purpose:** Show ERP/bank match and confidence. **Most visual space.**

```
┌────────────────────────────────────────┐
│ Check Transaction Match                    │
├────────────────────────────────────────┤
│ ┌──────────┐ Check Matched                 │
│ └──────────┘                           │
│                                        │
│ Vendor          Amount                 │
│ Acme Corp       $2,450.50              │
│ 96% match       USD - 100% match       │
│                                        │
│ Date            GL Code                │
│ Jan 25, 2026    5010                   │
│ 100% match      Services & Consulting  │
│                                        │
│ ─────────────────────────────────────  │
│ Match Quality                          │
│ Vendor Match  ████████████░░░░  96%    │
│ Amount Match  ████████████████  100%   │
│ Date Match    ████████████████  100%   │
│ ─────────────────────────────────────  │
│ Bank Ref: WIRE-00982456                │
└────────────────────────────────────────┘
```

**Status Badge Colors:**
- Check Matched: Green `#34a853`
- Warning Review: Yellow `#fbbc04`
- ✗ No Match: Red `#ea4335`

**4-Column Grid:**
| Column | Label | Value | Meta |
|--------|-------|-------|------|
| Vendor | 12px gray | 15px 500 black | "96% match confidence" 11px |
| Amount | 12px gray | 18px 700 teal | "USD - 100% match" 11px |
| Date | 12px gray | 15px 500 black | "Same day - 100% match" 11px |
| GL Code | 12px gray | 15px mono 500 | "Services & Consulting" 11px |

**Match Quality Bars:**
- 4px height
- Color: 95%+ green, 80-95% yellow, <80% red
- Right-aligned percentage

**Label Updates:**
- Match found → `Clearledgr/Invoices/Matched`
- No match → `Clearledgr/Exceptions`

---

#### Card 4: Journal Entry Preview

**Purpose:** Show how transaction will post to ledger.

```
┌────────────────────────────────────────┐
│ Preview Journal Entry Preview               │
├────────────────────────────────────────┤
│ ┌────────────────────────────────────┐ │
│ │ Acme Corp Invoice      $2,450.50  │ │
│ │   AP/Accrual (Account 2100)       │ │
│ │                        $2,450.50  │ │
│ │   GL 5010 (Services & Consulting) │ │
│ └────────────────────────────────────┘ │
│                                        │
│ Post date: Jan 25, 2026                │
│ Auto-memo: Cleared from email invoice  │
│            (Match ID: CL-1737-abc9ef)  │
└────────────────────────────────────────┘
```

**Styling:**
- Debit: left-aligned, black
- Credit: left-aligned, teal
- Account names: 14px, 500 weight
- Account codes: 12px, gray, monospace
- Amounts: 15px, 700 weight, teal

---

#### Card 5: Actions

**Purpose:** Primary CTA and secondary options.

```
┌────────────────────────────────────────┐
│ 🎯 Actions                             │
├────────────────────────────────────────┤
│ ┌────────────────────────────────────┐ │
│ │ Check Post to Ledger           Cmd+↵  │ │ ← Primary
│ └────────────────────────────────────┘ │
│ ┌────────────────────────────────────┐ │
│ │ Warning Flag Exception                  │ │ ← Secondary
│ └────────────────────────────────────┘ │
│ ┌────────────────────────────────────┐ │
│ │ ↩ Request More Info               │ │ ← Secondary
│ └────────────────────────────────────┘ │
└────────────────────────────────────────┘
```

**Primary Button (Post to Ledger):**
- Full width, 44px height
- Background: Teal `#1abc9c`, white text, 600 weight
- Icon: Check checkmark (left)
- Shortcut hint: "Cmd+↵" (right, 11px, gray)

**Primary Button States:**
| State | Appearance |
|-------|------------|
| Default | Teal background, clickable |
| Hover | Dark teal `#16a085`, translateY(-2px), shadow |
| Active | scale(0.98) |
| Processing | Spinner, "Processing...", disabled |
| Success | Green `#34a853`, "Check Posted to Ledger #JE-12345" |
| Error | Red `#ea4335`, "Failed. [Retry]" |

**Secondary Buttons:**
- Full width, 40px height
- White background, gray border 1px `#dadce0`
- Hover: Light gray `#f8f9fa`

**Button Gap:** 8px

---

#### Card 6: Audit Trail

**Purpose:** Transaction metadata for compliance.

```
┌────────────────────────────────────────┐
│ ℹ️ Audit Trail                         │
├────────────────────────────────────────┤
│ Match ID:   CL-1737-abc9ef             │
│ Email ID:   msg_id_12345               │
│ Status:     [Ready to Post]            │
│ Extracted:  Jan 26, 2026 at 3:14 PM    │
│                                        │
│ [Doc Clearledgr/Invoices/Matched]       │
└────────────────────────────────────────┘
```

**Details:**
- Match ID: monospace, 11px
- Email Message ID: monospace, 11px
- Status: colored badge (Ready/Posted/Exception)
- Extracted: regular text, 12px
- Label badge: updates in real-time

---

### C. Resize Handle

- 8px drag zone on left edge
- Background: transparent
- Hover: `rgba(26, 188, 156, 0.3)`
- Active: `rgba(26, 188, 156, 0.6)`
- Cursor: `col-resize`
- No visible element, pure interaction zone

---

## 4. Visual Hierarchy

### Largest (Most Attention)
- Transaction amount: 18px, 700 weight, teal `#1abc9c`
- Primary action button: 44px, full width, teal background
- Status badge: colored (green/yellow/red)

### Medium (Quick Scan)
- Card titles: 14px, 600 weight
- Vendor name: 15px, 500 weight
- Match quality bars: visual width

### Small (Reference)
- Field labels: 12px, gray `#5f6368`
- Confidence percentages: 12px, right-aligned
- Audit trail: 12px, monospace

---

## 5. Color Palette

| Element | Hex | Usage |
|---------|-----|-------|
| Primary (Teal) | `#1abc9c` | Buttons, badges, highlights, amount |
| Secondary (Dark Teal) | `#16a085` | Hover states, active states |
| Success (Green) | `#34a853` | Match found, checkmarks |
| Warning (Yellow) | `#fbbc04` | Review needed, caution |
| Error (Red) | `#ea4335` | No match, exceptions |
| Neutral (Gray) | `#5f6368` | Labels, secondary text |
| Background | `#fafafa` | Sidebar base |
| Surface | `#ffffff` | Cards, header, buttons |
| Border | `#e0e0e0` | Dividers, card edges |

---

## 6. Typography

| Element | Size | Weight | Line Height |
|---------|------|--------|-------------|
| Header title | 15px | 600 | 1.2 |
| Card title | 14px | 600 | 1.4 |
| Data label | 12px | 500 | 1.5 |
| Data value | 14px | 500 | 1.5 |
| Amount | 18px | 700 | 1.2 |
| Button text | 14px | 600 | 1.2 |
| Audit ID | 11px | 400 | 1.4 (monospace) |
| Meta text | 11px | 400 | 1.4 |

**Font Stack:**
```css
-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", sans-serif
```

---

## 7. Interaction Model

### Post to Ledger Lifecycle

```
Default → Hover → Click → Processing → Success/Error
   ↓        ↓       ↓          ↓           ↓
  Teal   Dark teal Spinner  Disabled   Green/Red
                   overlay  grayed     auto-dismiss
```

1. **Default:** Teal, clickable, shows shortcut
2. **Hover:** Dark teal, translateY(-2px), shadow
3. **Click:** Spinner overlay, "Processing..."
4. **Processing:** Disabled, no clicks
5. **Success:** Green, "Check Posted #JE-12345", auto-dismiss 3s
6. **Error:** Red, "Failed. [Retry]"

### Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| Cmd+Enter (Mac) / Ctrl+Enter (Win) | Post to Ledger |
| Escape | Close sidebar (mobile only) |
| Alt+K | Toggle sidebar visibility |
| Tab | Navigate buttons |

### Resize Behavior

- Drag left edge to widen/narrow
- Minimum: 280px, Maximum: 600px
- Gmail content adjusts in real-time (`margin-right`)
- Width persists in localStorage

### Confidence Indicators

- 3 bar charts (Vendor, Amount, Date)
- No text percentages; visual only
- Colors: 95%+ green, 80-95% yellow, <80% red

---

## 8. Mobile Behavior (< 768px)

### Dimensions
- Width: 100% (full screen)
- Entry: Slides from right (`translateX(100%) → 0`)
- Overlay: 50% black behind sidebar

### Close Methods
- X button
- Escape key
- Click overlay

### Changes
- No resize handle (hidden)
- No drag-to-widen
- FAB button: bottom-left, 56px, teal background
- Badge count on FAB for pending emails

### Same as Desktop
- Header: 64px, same 3 buttons
- Cards: same layout, just full width

---

## 9. Loading States

### Email Loading
- Skeleton cards (gray placeholder bars, 12px height)
- Shimmer animation: horizontal pulse
- Duration: until data loads

### Match Analysis
- Spinner icon: teal, 16px, rotating
- Text: "Analyzing..." or "Searching bank feeds and ERPs"

### Post in Progress
- Button disabled, grayed
- Spinner overlaid
- Text: "Processing..."

### Post Success
- Toast slides up: "Check Posted to Ledger #JE-12345"
- Green background, white text
- Auto-dismiss: 3 seconds
- Button: "Check Posted"

### Post Error
- Toast: "Failed to post. [Retry]"
- Red background, white text
- Button resets to "Post to Ledger"

---

## 10. Dark Mode

Applied when OS is set to dark mode.

| Element | Dark Value |
|---------|------------|
| Sidebar background | `#1e1e1e` |
| Card background | `#2d2d2d` |
| Header background | `#1e1e1e` |
| Text (primary) | `#e0e0e0` |
| Text (secondary) | `#999` |
| Border | `#333` |
| Primary button | Teal `#1abc9c` (unchanged) |
| Match quality bars | Slightly brighter |
| Skeleton loading | Darker gray pulses |

---

## 11. Accessibility

### Color
- Never rely on color alone
- Always pair with icons and text
- Example: Status badges use color + icon (Check/Warning/X)

### Contrast
- All text meets WCAG AA minimum (4.5:1 ratio)
- Verified on light and dark modes

### Focus States
- Visible focus ring: 2px teal outline `#1abc9c`
- Clearly distinct from default

### Labels
- All buttons have `aria-label` attributes
- Form inputs have associated labels

### Keyboard Navigation
- Tab through all elements in logical order
- Enter to activate buttons
- Escape to close (mobile)
- Alt+K to toggle

### Screen Readers
- Semantic HTML (sections, headings, roles)
- Heading hierarchy (h1 sidebar title, h2 card titles)
- ARIA labels for icon-only buttons
- List structure for audit trail

---

## 12. Micro-interactions

| Trigger | Animation | Duration | Easing |
|---------|-----------|----------|--------|
| Button hover | translateY(-2px) | 150ms | ease-out |
| Button click | scale(0.98) | 100ms | ease-out |
| Sidebar open (mobile) | translateX | 300ms | cubic-bezier(0.4, 0, 0.2, 1) |
| Card expand | Height transition | 200ms | ease-out |
| Toast appear | Fade + slide up | 300ms | ease-out |
| Toast disappear | Fade + slide down | 300ms | ease-in |
| Spinner | Rotation 360deg | 1s | linear |
| Confidence bar fill | Width transition | 400ms | ease-out |

---

## 13. Content Tone

| State | Message |
|-------|---------|
| Match found | "Check Ready to Post" |
| Match uncertain | "Warning Review Before Posting" |
| No match | "X No Match Found" |
| Success | "Posted to Ledger #JE-12345" |
| Error | "Failed to post. [Retry]" |
| Triage confident | "Detected as Invoice" |
| Triage uncertain | "Likely Invoice (73%)" |
| Triage low | "Unable to Classify" |

---

## 14. What's NOT Included

- No Chat interface (Vita bot)
- No Vendor 360 view
- No Settings/preferences in sidebar
- No Help text or tooltips
- No Multiple tabs
- No Animations beyond hover/load states
- No Floating notifications (only toast at bottom)

---

## 16. Implementation Checklist

### Part A: Intake Layer

#### Background Processing
- [ ] Auto-scan inbox on tab open
- [ ] Detect financial emails (subject, sender, attachments)
- [ ] Calculate confidence scores
- [ ] Add to pending queue (chrome.storage)
- [ ] Apply `Clearledgr/Pending` label
- [ ] Periodic rescan (every 5 minutes)

#### FAB & Badge
- [ ] Badge shows pending count
- [ ] Badge animation on new items
- [ ] Empty state (no badge, muted)
- [ ] Processing state (spinner)

#### Batch View
- [ ] Queue list display
- [ ] Current item highlight
- [ ] Item states (pending, current, done, skipped, exception)
- [ ] Progress bar
- [ ] "Start Processing" button
- [ ] "Skip All" button

#### Queue Management
- [ ] Auto-navigate to next email
- [ ] Auto-archive after posting
- [ ] Queue persistence (survives refresh)
- [ ] Completion summary screen
- [ ] Stats (count, total $, time saved)

---

### Part B: Execution Layer

#### Structure
- [x] Sticky header (64px)
- [x] Scrollable content area
- [x] 6 cards in correct order
- [x] Resize handle (8px left edge)

### Card 1: Triage
- [x] High confidence state (95%+) - collapsed
- [x] Moderate confidence state (70-95%) - expanded with confirm
- [x] Low confidence state (<70%) - manual radio selection
- [x] Label badge display
- [x] Auto-apply labels (visual state tracking)

### Card 2: Email Context
- [x] From, Subject, Date, Attachments
- [x] Confidence bar (visual only, no %)
- [x] Label badge
- [x] Status text

### Card 3: Transaction Match
- [x] Status badge (3 colors)
- [x] 4-column grid (Vendor, Amount, Date, GL) — 2x2 grid
- [x] Match quality bars (3 metrics, visual only)
- [x] Bank reference line
- [x] Label updates on match state

### Card 4: Journal Entry
- [x] Debit/Credit format
- [x] Account codes
- [x] Post date
- [x] Auto-memo with Match ID

### Card 5: Actions
- [x] Primary button (44px, teal)
- [x] Primary button states (hover, processing, success, error)
- [x] Shortcut hint on primary button
- [x] Secondary buttons (40px, white)
- [x] Flag Exception
- [x] Request More Info

### Card 6: Audit Trail
- [x] Match ID (monospace)
- [x] Email ID (monospace)
- [x] Status badge
- [x] Extracted timestamp
- [x] Label milestone display

### Layout
- [x] 420px default width
- [x] 280-600px resize range
- [x] Gmail content push (margin-right on html)
- [x] Width persistence (localStorage)

### Interactions
- [x] Alt+K toggle
- [x] Cmd+Enter post
- [x] Escape close (mobile only)
- [x] Tab navigation (basic)
- [x] Resize drag

### States
- [x] Loading skeletons with shimmer
- [x] Processing spinner
- [x] Toast notifications
- [x] Error handling

### Responsive
- [x] Mobile full-screen overlay
- [x] FAB trigger with badge
- [x] Hide resize handle on mobile

### Visual
- [x] Color palette applied
- [x] Typography scale
- [x] Dark mode support
- [x] Focus states

### Accessibility
- [x] ARIA labels (header buttons)
- [x] Semantic HTML (basic)
- [x] Keyboard navigation (Alt+K, Cmd+Enter, Escape)
- [ ] Screen reader support (needs verification)

---

## Summary

Clearledgr is a **two-layer automation system**:

### Intake Layer (Background)
- Check Auto-scans inbox for financial emails
- Check Queues invoices, receipts, payments without user action
- Check Shows pending count on FAB badge
- Check Enables batch processing (12 invoices in one flow)

### Execution Layer (Sidebar)
- Check Processes one transaction from triage through posting
- Check Classifies automatically, asks only when uncertain
- Check Displays confidence transparently (bars, not percentages)
- Check Surfaces primary action (Post button) at eye level
- Check Auto-archives and advances to next item
- Check Shows completion summary with stats

### The Result
Finance teams go from **hunting for invoices** to **approving a queue**.

```
Before: 50 emails × 3 min each = 2.5 hours of clicking
After:  50 emails × 30 sec each = 25 minutes of approving
```

---

*Document Version: 1.1*  
*Last Updated: January 26, 2026*
