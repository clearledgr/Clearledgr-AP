# Workspace SPA — Critical UX Audit (2026-04-27)

**Scope:** `ui/web-app/src/` — all 14 pages + shell components
**Standard:** Linear / Notion / Mercury-grade product UX (dense, calm, professional)
**Trigger:** Mo flagged the Settings page on `workspace.clearledgr.com` as not customer-ready and asked for a real review of the rest before any "GA-ready" claim is repeated.

---

## Executive summary

The workspace is **architecturally sound but visually half-finished**. HomePage, PipelinePage, and ReviewPage are production-quality. SettingsPage is the worst offender (exactly as Mo reported): scattered inline styles, fake tab buttons, no cards, bare labels running into values. Connections and Status pages have high-friction setup flows. Missing across the board: consistent empty states, mobile responsiveness on secondary pages, global error retry UX, a11y patterns.

Major pattern failures:
- **Settings**: 80+ hardcoded inline styles, label/value collision on rows, no visual separation, fake tab bar at top with no active state.
- **Connections**: three integration cards with inconsistent UX language ("Not connected" labels instead of CTA buttons).
- **Secondary pages** (Vendors / Activity / Exceptions): flat walls of text, no card structure on first glance.
- **Mobile**: AppShell drawer animation is instant (jarring), Topbar has fixed-width elements that crowd on phones, SettingsPage GL grid is hardcoded `1fr 1fr` with no media query.
- **Error states**: silent failures on API timeouts; "Could not load" toasts with no retry UI.
- **A11y**: missing aria-labels on icon buttons, divs with `role="button"` but no keyboard support.

---

## Page-by-page

Star ratings: ★ broken / ★★ poor / ★★★ acceptable / ★★★★ good / ★★★★★ excellent

### HomePage.js — READY
- **Structure** ★★★★ — clear h1 → h2 hierarchy, 4 KPI tiles, two-column activity + vendors grid, quick-actions row.
- **Empty state** ★★★★ — "No invoices yet" with "Connect a source" CTA; onboarding banner shows when relevant.
- **Loading** ★★★ — KPI tiles show "…", activity panel shows "Loading…" text. Acceptable; could be skeleton.
- **Error** ★★★★ — `Promise.allSettled()` partial-fail; KPI tiles show fallback values on metrics failure.
- **Mobile** ★★★ — auto-fit grid + flex-wrap on actions row.
- **Code** — no dead code, no console.logs. State labels (line 39-53) hardcoded → P2 i18n debt.

**Verdict: ready.** No P0/P1 issues.

### PipelinePage.js — READY
- **Structure** ★★★★★ — Streak-pattern Kanban, scope toggles (All / Exceptions / Overdue), filters in modal, batch ops.
- **Empty** ★★★★ — per-column empty cards, queue-level "no matches" with reset button.
- **Loading** ★★★★ — first-mount skeleton, batch ops disabled during load, smooth transitions.
- **Error** ★★★ — silent toasts, fallback UI on failure.
- **Mobile** ★★★ — `overflow-x:auto` on Kanban, hamburger toggle, ellipsis on cards. **But filters panel is 380px fixed width — broken on small phones.**
- **Code** ★★★★ — 1200 lines, well-structured. Blocker chips use inline styles → P2 extract to CSS.
- **A11y** ★★ — Kanban cards use `role="button"` + `onClick` with **no keyboard support** (Tab/Enter/Space). Modal overlays don't trap focus.

**Verdict: ready, with P2 polish needed.**

### ReviewPage.js — READY
- **Structure** ★★★★ — section headers with count pills, ReviewCard well-factored, dynamic bulk-actions panel.
- **Empty** ★★★★ — "Nothing needs review" + "No items match this search". Both have explanatory copy.
- **Loading** ★★★ — text only, no skeleton.
- **Error** ★★★★ — `loadItems` catches silently by default, toast on manual refresh failure, field-resolution errors include reason.
- **Mobile** ★★★ — flex-wrap on badge rows, bulk-action buttons fill width. **Metric pills don't stack.**
- **Code** ★★★★ — clean. console.errors are inside error boundaries only.
- **A11y** ★★★ — checkboxes have labels; missing keyboard nav between cards.

**Verdict: ready.** Minor: metric pills should stack at narrow widths.

### SettingsPage.js — **P0 BLOCKER**
This is the page Mo flagged. Confirmed bad.

- **Structure** ★ — every block is a `<div class="panel">` but content is a wall of labels + inputs. No card-row pattern, no consistent spacing, no h2 visual weight. The 8-button "tab" bar at the top doesn't update active state and the entire page renders as one long scroll.
- **Empty** ★★ — "ERP Not connected", "Gmail Not connected", "Approval surface Not connected" rendered as bold-label badges, not buttons. Should be CTAs ("Connect Gmail →").
- **Loading** ★ — GL mapping fetches on mount (line 174), chart-of-accounts lazy-loads (line 188), neither shows a spinner or skeleton.
- **Error** ★★ — toast-only ("Could not load chart of accounts"). No inline error state under inputs.
- **Mobile** ★ — GL mapping uses `style="display:grid;grid-template-columns:1fr 1fr"` inline at line 446, no media query. Text overlaps on phone.
- **Code** ★★ — **80+ inline `style=` attributes** at lines 66, 270, 340, 359, 381, 446, 462, 476, 480, 527, etc. Same input styling repeated four times. Segment-button row at line 348 has no keyboard support. Dead code: `activeAlias` at line 320, unused `saveOrg` action at 141-156.

Specific lines that need refactor:
- 340-357: segment button row → CSS class
- 359-376: settings summary grid → responsive breakpoint
- 387-420: ERP Connection section → reusable card component
- 446-509: GL mapping form → inline styles on every input
- 521-562: AP Policy inputs → same inline-style repetition

**Verdict: cannot ship to a prospect in current state.**

### ConnectionsPage.js — P1
- **Structure** ★★★ — sections for ERP / Slack / Teams / Webhooks; ConnectionRow component uses inline styling.
- **Empty** ★★ — "Not connected" labels with no CTA on ERP card (line 200), "Set up Slack or Teams" is a label not a button (line 55).
- **Loading** ★★★ — Connect buttons show "Working…" during OAuth.
- **Error** ★★ — generic "Could not finish the ERP connection" toast (line 342), no inline help.
- **Mobile** ★★ — webhook input has `min-width:240px`, too wide on phone.
- **Code** — `console.warn('Add webhook failed:', e)` at line 413 ships to prod console. Webhook URL has no validation before submit.

**Verdict: P1 — replace "Not connected" labels with CTAs, add inline validation, remove `console.warn`.**

### ExceptionsPage.js — P1
- **Structure** ★★★ — banner with unresolved count, sidebar breakdown by severity/type, flat list of exceptions.
- **Empty** ★★★ — "No exceptions match the current filters."
- **Loading** ★ — no skeleton, just `items === null ? "Loading…"`.
- **Error** ★★ — inline secondary-note on error, but no retry.
- **Mobile** ★★ — sidebar covers main content on narrow screens.
- **Code** — **`window.prompt()` at line 46 for resolution notes**. Looks like 2005 UI. ReviewPage already has `openDialog()` pattern — copy it.

**Verdict: P1 — replace `window.prompt()` with the modal-dialog pattern from ReviewPage.**

### VendorsPage.js — READY
- **Structure** ★★★★ — overview chips (vendors / open invoices / spend), card grid, dedup banner.
- **Empty** ★★★★ — "No vendors yet" with onboarding copy.
- **Loading** ★★★ — "Loading vendor directory…"
- **Error** ★★ — silent on load failure (`setVendors([])`), dedup fetch fails silently.
- **Mobile** ★★★ — CSS grid, action buttons wrap.
- **Code** — exception tags at line 133 use hardcoded colors → P2 constants.

**Verdict: ready.** Minor: error state should toast on load failure.

### ActivityPage.js — P2
- **Structure** ★★★ — summary cards on top, recent updates list below. No time-series visualization.
- **Empty** ★★★ — "No recent activity yet."
- **Loading** ★★ — none.
- **Error** ★ — silent if `bootstrap.recentActivity` is null/undefined.
- **Code** — `eventBadge()` helper imported but no graceful fallback if it throws.

**Verdict: P2 polish.** Add loading skeleton + error fallback.

### StatusPage.js — READY
- **Structure** ★★★★ — overall-status header, component list with color-coded dots, runtime profile section.
- **Empty** ★★★ — "No component data available."
- **Loading** ★★★ — header shows "…" during load, polls every 30s.
- **Error** ★★★ — "Service unreachable" status dot when API down.
- **Mobile** ★★★★ — text-based, wraps naturally.
- **Code** ★★★ — clean, `statusToTone()` helper, error state in try/catch.

**Verdict: ready.**

### ReconciliationPage.js — P2
- **Structure** ★★★ — form on left, steps panel on right, result callout below.
- **Loading** ★★★ — "Starting…" button text during submit.
- **Error** ★★ — generic "Failed: " + e.message toast.
- **Mobile** ★★ — 2-col layout stacks but form gets narrow.
- **Code** — regex for spreadsheet ID extraction (line 28) is fragile; should validate URL before submit.

**Verdict: P2.** Not customer-critical (read-only feature for now).

### TemplatesPage.js / OnboardingPage.js / PlanPage.js / HealthPage.js
**Not deeply audited.** Tomorrow's pass should cover these too. If they follow the secondary-page pattern (Activity / Reconciliation), they're likely P2.

---

## Shell components

### AppShell.js — fine
Sidebar + main + footer. Mobile drawer + backdrop. Error boundary around content. Hamburger has `aria-label`. **No P0/P1 issues.** Drawer toggle is instant — could use a 150ms transition for polish.

### Topbar.js — P1
Workspace + role on left, Cmd-K hint center, user menu right. **Hamburger crowds the org name on phone** because the org block is full-width without a media-query collapse. Cmd+K dispatch via synthetic KeyboardEvent (lines 100-104) is clever and works.

**Fix:** add a `@media (max-width: 480px)` rule that hides the Workspace/role text and shows just the avatar + name.

### SidebarNav.js — not deeply read
Tomorrow.

### CommandK.js — fine
Earlier verification confirmed the palette + debounced search works. Tomorrow: re-confirm keyboard shortcuts (↑↓ navigate, Enter activate, Esc close) under the new layout.

### EntitySwitcher.js — fine
Localstorage-backed, click-outside dismiss, renders nothing for orgs with 0-1 entities.

### AppFooter.js — bug just fixed
"Partially degraded" was caused by `/health` not being in web-app's proxy paths; fix is in commit `16d39c9`.

---

## Cross-cutting issues

### Empty states
**Problem:** SettingsPage / Connections / Exceptions all use "Not connected" / "No data" labels without CTAs.
**Fix:** Create a reusable `<EmptyState>` component with optional CTA. Apply to all secondary pages.

### Loading states
**Problem:** Most pages show "Loading…" plain text. Only PipelinePage has a real skeleton.
**Fix:** Extract PipelinePage skeleton into a shared component, apply to Settings + Activity + Vendors.

### Error handling
**Problem:** API failures mostly toast silently; no inline retry.
**Fix:** Standardize an error-state component with retry button. Use under any panel that depends on a fetch.

### Mobile responsiveness
**Problem:** Secondary pages assume desktop (fixed columns, 380px fixed widths).
**Fix:** Pass through every page's CSS, replace fixed widths with `auto-fit` grids, add `@media (max-width: 768px)` rules where needed.

### Inline styles
**Blocker:** SettingsPage has 80+ inline `style=` attributes; ConnectionsPage and Pipeline blocker chips also inline.
**Fix:** One-day refactor. Extract `.input-sm`, `.label-sm`, `.card-row`, `.stat-pill` into shared CSS. Move per-page styles into `pages/*.css`.

### A11y
- Divs with `role="button"` need keyboard listeners (Tab, Enter, Space).
- Color-only severity badges need text fallback.
- Modals don't trap focus.

---

## Top-priority fixes (one day)

If we have one day to fix the most impactful issues, in order:

### P0 — blocks prospect demo

1. **SettingsPage: extract inline styles + replace "Not connected" with CTA buttons**
   - 2-3h. Settings is the page Mo specifically called out.
   - Create `settings.css` with `.gl-field-row`, `.input-sm`, `.label-sm`, `.connection-card`, `.connection-cta`.
   - Replace all 8 "Not connected" labels with primary buttons.

2. **Fix SettingsPage GL mapping mobile collapse** (line 446)
   - 30m. Add `@media (max-width: 768px) { grid-template-columns: 1fr; }`.

3. **Make the SettingsPage tab bar actually work**
   - 1h. Either wire the buttons to filter/scroll (state-driven), or remove the bar and use anchor links to in-page sections.

4. **Remove dead code from SettingsPage** (`activeAlias`, `saveOrg`)
   - 30m.

### P1 — visible roughness

5. **ExceptionsPage: replace `window.prompt()` with modal dialog** (line 46)
   - 1.5h. Copy the `openDialog()` pattern from ReviewPage line 717.

6. **ConnectionsPage: replace "Not connected" labels with CTA buttons**
   - 1h.

7. **Topbar: media-query the org context block on phone**
   - 30m.

8. **ConnectionsPage: inline error message on webhook URL** + remove `console.warn`
   - 30m.

### P2 — polish

9. **Build reusable `<EmptyState>` + `<LoadingSkeleton>` + `<ErrorRetry>` components**
   - 2h. Apply across Activity, Vendors, Settings.

10. **Mobile pass on Topbar / SettingsPage / ReconciliationPage**
   - 1h. CSS-only changes.

---

## Page status table

| Page | Structure | Empty | Loading | Error | Mobile | Code | Verdict |
|------|-----------|-------|---------|-------|--------|------|---------|
| Home | ★★★★ | ★★★★ | ★★★ | ★★★★ | ★★★ | ★★★★ | **Ready** |
| Pipeline | ★★★★★ | ★★★★ | ★★★★ | ★★★ | ★★★ | ★★★★ | **Ready** |
| Review | ★★★★ | ★★★★ | ★★★ | ★★★★ | ★★★ | ★★★★ | **Ready** |
| Settings | ★ | ★★ | ★ | ★★ | ★ | ★★ | **P0 fix** |
| Connections | ★★★ | ★★ | ★★★ | ★★ | ★★ | ★★★ | **P1 fix** |
| Exceptions | ★★★ | ★★★ | ★ | ★★ | ★★ | ★★★ | **P1 fix** |
| Vendors | ★★★★ | ★★★★ | ★★★ | ★★ | ★★★ | ★★★ | **Ready** |
| Activity | ★★★ | ★★★ | ★★ | ★ | ★★★ | ★★ | **P2 polish** |
| Status | ★★★★ | ★★★ | ★★★ | ★★★ | ★★★★ | ★★★ | **Ready** |
| Reconciliation | ★★★ | — | ★★★ | ★★ | ★★ | ★★★ | **P2 polish** |
| Templates / Onboarding / Plan / Health | not audited | | | | | | tomorrow |

---

## Verdict

**SettingsPage is the main blocker.** Once inline styles are extracted and "Not connected" becomes actual CTAs, it's prospect-ready.

**Pipeline + Review + Home + Vendors + Status** are already customer-ready.

**Secondary pages** (Activity, Connections, Exceptions, Reconciliation) need the empty-state / error-state / loading-state cleanup to feel finished.

**Architecture is sound** (Preact, file-based routing, modular pages, shell context). The gaps are presentation-layer: missing CSS organization, empty-state patterns, and mobile refinement. Typical 80% → 100% polish.

Day-1 plan: do the P0 + P1 list above (~10h). End state: a workspace that survives a prospect demo without me hand-waving "we're going to fix that."
