# Streak Architecture Analysis for Clearledgr

## Overview

Streak is a CRM built natively into Gmail. This document analyzes how Streak integrates with Gmail to inform Clearledgr's implementation.

---

## 1. AppMenu (Left Rail)

### What It Is
The vertical icon strip on the far left of Gmail (where Mail, Chat, Meet icons appear).

### Streak's Implementation
- **Icon**: Orange Streak logo
- **Click behavior**: Expands a navigation panel
- **Panel contents**:
  - `+ New pipeline` button (primary action)
  - Home
  - Contacts
  - Organizations  
  - Mail Merges
  - Tracked emails
  - **Pipelines** section (expandable)
    - Individual pipelines with sub-stages

### Key Behavior
**Clicking an item navigates to a ROUTE, not a sidebar.**

| Item Clicked | URL Changes To | What Renders |
|--------------|----------------|--------------|
| Home | `/Home` | Dashboard in main content area |
| Contacts | `/Contacts` | Table view in main content area |
| Tracked emails | `?q=has:tracking` | Gmail search results |
| Pipeline | `/Pipeline-Name` | Pipeline board in main content area |

---

## 2. Routes (Main Content Area)

### What It Is
Custom full-page views that replace Gmail's inbox/thread view.

### Streak's Routes

#### Home (`/Home`)
- Welcome message
- Quick Access shortcuts (pipelines, create box, create task, etc.)
- Recent Boxes (Kanban cards)
- Won Boxes
- Product Updates feed
- Streak Upcoming

#### Contacts (`/Contacts`)
- Full-width table
- Columns: Name, Email, Company, Pipelines, Mail Merges, Role, Phone
- Sortable, filterable
- Click row → opens contact detail

#### Pipeline View (`/Pipeline-Name`)
- Stage headers (colored badges): Cold Outreach → Intro Warmup → Discovery → etc.
- Table below with rows for each "box" (deal/item)
- Columns: Name, Stage, Contacts, Lead Source, Target Stage, etc.
- Kanban-style organization

### How Streak Implements Routes
Uses InboxSDK's `Router` namespace:
```javascript
sdk.Router.handleCustomRoute('home', (routeView) => {
  // Render dashboard HTML into routeView.getElement()
});

sdk.Router.goto('home'); // Navigate programmatically
```

---

## 3. Sidebar (Right Side)

### What It Is
A context-aware panel that appears when viewing an email thread.

### When It Appears
- Only when viewing a specific email/thread
- Shows information ABOUT that email/contact

### Streak's Sidebar Contents
When viewing an email from a contact:
- Contact name & photo
- Company info
- Pipeline stage (which box they're in)
- Recent activity
- Notes
- Tasks
- Related boxes

### Key Insight
**The sidebar is NOT for navigation. It's for context about the current email.**

---

## 4. Toolbar Buttons

### What It Is
Buttons that appear in Gmail's toolbar when viewing/selecting emails.

### Streak's Implementation
- "Add to Streak" button when selecting emails
- Quick actions for the selected thread

---

## 5. Inbox Tags

### What It Is
Colored labels that appear in the inbox list view next to email subjects.

### Streak's Implementation
- Shows pipeline stage as a colored tag
- Shows contact info inline
- Visual at-a-glance status

---

## Clearledgr Implementation Plan

### AppMenu Items → Routes

| AppMenu Item | Route ID | View Content |
|--------------|----------|--------------|
| Home | `clearledgr-home` | Dashboard: stats, recent activity, quick actions |
| Vendors | `clearledgr-vendors` | Vendor table: name, total spend, invoice count, status |
| Analytics | `clearledgr-analytics` | Charts: spend over time, by vendor, by category |
| Invoice Pipeline | `clearledgr-pipeline` | Pipeline board with stages |

### Pipeline Stages (for Invoice Pipeline route)

| Stage | Color | Description |
|-------|-------|-------------|
| Detected | Blue | Auto-detected from inbox |
| Needs Review | Orange | Awaiting human review |
| Approved | Green | Approved, ready to post |
| Posted | Purple | Successfully posted to ERP |
| Exception | Red | Error or flagged |

### Sidebar (Email Context)

When viewing an email that's an invoice:
- **Invoice Details**: Amount, vendor, due date, line items
- **Vendor 360**: Total spend, invoice history, payment terms
- **Actions**: Approve, Reject, Post to ERP, Flag
- **Status History**: Timeline of processing steps

### What NOT to Do
- No AppMenu items opening the sidebar
- No Duplicate navigation (AppMenu + NavMenu)
- No Sidebar for global navigation

---

## Implementation Steps

### Step 1: Register Custom Routes
```javascript
sdk.Router.handleCustomRoute('clearledgr-home', renderHomeDashboard);
sdk.Router.handleCustomRoute('clearledgr-vendors', renderVendorTable);
sdk.Router.handleCustomRoute('clearledgr-analytics', renderAnalytics);
sdk.Router.handleCustomRoute('clearledgr-pipeline', renderPipeline);
```

### Step 2: AppMenu Navigates to Routes
```javascript
panel.addNavItem({
  name: 'Home',
  routeID: 'clearledgr-home',
  routeParams: {}
});
```

### Step 3: Sidebar for Email Context Only
```javascript
sdk.Conversations.registerThreadViewHandler((threadView) => {
  if (isInvoiceEmail(threadView)) {
    threadView.addSidebarContentPanel({
      title: 'Invoice Details',
      el: createInvoicePanel(threadView)
    });
  }
});
```

### Step 4: Remove FAB/Global Sidebar
The floating action button and global sidebar should be removed or repurposed since:
- Navigation → AppMenu + Routes
- Email context → InboxSDK sidebar

---

## File Structure

```
src/
├── inboxsdk-layer.js      # Main InboxSDK initialization
├── routes/
│   ├── home.js            # Home dashboard view
│   ├── vendors.js         # Vendor table view
│   ├── analytics.js       # Analytics charts view
│   └── pipeline.js        # Invoice pipeline board
├── sidebar/
│   └── invoice-panel.js   # Email-specific sidebar
└── components/
    ├── stage-badge.js     # Pipeline stage badges
    └── vendor-row.js      # Vendor table row
```

---

## Summary

| Component | Streak Uses For | Clearledgr Should Use For |
|-----------|-----------------|---------------------------|
| AppMenu | Navigate to routes | Navigate to routes |
| Routes | Full-page views (Home, Contacts, Pipeline) | Full-page views (Home, Vendors, Analytics, Pipeline) |
| Sidebar | Email-specific context | Invoice details, vendor 360, actions |
| Toolbar | Quick actions on selected emails | Process with Clearledgr |
| Inbox Tags | Visual status in inbox list | Invoice/Receipt/Statement tags |
