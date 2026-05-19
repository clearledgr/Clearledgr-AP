# Design System — Solden

## Product Context
- **What this is:** Solden is an embedded finance-ops execution layer. It coordinates work across the systems finance teams already use instead of forcing them into a new standalone back office.
- **Product analogy:** The current Gmail/AP wedge should feel like Streak for finance ops, but that is the MVP interaction model, not the full product boundary.
- **Who it's for:** Finance teams at growing companies who need execution, follow-up, approvals, and system-of-record updates to happen across inbox, chat, ERP, and other finance surfaces.
- **Primary product truth:** Solden is broader than Gmail and broader than AP. Gmail-first AP is the first production wedge.
- **Core promise:** Work gets identified, routed, executed, and audited where finance already operates.
- **Primary surfaces today:**
  - **Render targets** (where decisions happen): Gmail thread panel, Slack and Teams approval cards, NetSuite SuiteApp, SAP Fiori extension, ERP-native follow-ons.
  - **Coordination layer** (`workspace.clearledgr.com`): Home (live agent activity ribbon), Activity, Exceptions, Records (read-only directory), Vendors, Reports, Audit log, Approval rules, Connections, API keys, Settings.
- **Broader surface model:** Every render target inherits the same embedded-work doctrine. The workspace is not a render target — it's the control center that watches the render targets and intervenes when the agent escalates.

## Core UX Doctrine
1. **Embedded work, not dashboard migration.** Solden lives inside the systems where finance work already happens — Gmail, Slack, Teams, the ERP.
2. **Streak is the Gmail interaction model.** The Gmail/AP wedge should feel like the finance-operations version of Streak inside Gmail.
3. **The thread panel is for execution.** It handles the current record only: state, blockers, evidence, one primary action, a few secondary actions.
4. **The workspace SPA is the coordination-layer control center.** The hero is the live agent activity ribbon. The workspace is not a workflow desktop and not a second pipeline UI; routine approve/reject/post/snooze decisions belong in the render targets (Slack/Teams/Gmail/ERP). The workspace exists for (1) live situational awareness, (2) policy + identity configuration, (3) intervention when the agent escalates, (4) audit + governance, (5) connections to render targets.
5. **Home is a hub, not a dashboard.** It is for quick access, recent work, upcoming follow-ups, and secondary tools. It should not lead with KPI cards or setup sprawl.
6. **Admin tools stay secondary.** Connections, rules, team, plan, status, and similar pages should be discoverable but never dominate the main work path.
7. **Copy should be operational and plain.** Use short labels and direct task language. Avoid internal platform wording or technical explanations.
8. **Each surface should feel native to its host.** Gmail pages should feel Gmail-native; Slack/Teams approvals should feel chat-native; ERP follow-ons should feel system-native.

## Aesthetic Direction
- **Direction:** Embedded operational software for finance teams.
- **Mood:** Fast, calm, precise, trustworthy.
- **Decoration level:** Minimal. Flat surfaces, strong typography, quiet borders, extremely light shadow.
- **Reference hierarchy:**
  - Primary for Gmail surfaces: Streak Home, Streak AppMenu, Streak queue/list patterns
  - Secondary: Stripe Dashboard typography discipline, Ramp finance semantics, Mercury restraint
- **Visual goal:** A user should feel like they are still inside the host tool, just with a much better operating system for finance work.

## Brand Identity
- **Logomark:** Three stacked slabs forming a stylized "S" — two navy horizontal bars top + bottom with a teal middle stripe running upper-right to lower-left. The slants on the navy bars feed visually into the diagonal so the silhouette reads as a continuous S. Implemented as inline SVG in [`ui/web-app/src/shell/BrandMark.js`](ui/web-app/src/shell/BrandMark.js).
- **Wordmark:** "solden" — Inter, weight 700, lowercase, letter-spacing -1% to -2%.
- **Brand color (primary accent):** Teal `#18BFB0` (`--cl-teal-500`).
- **Brand dark (primary ink):** Navy `#0A1F44` (`--cl-navy`).
- **Variants:** Primary lockup (navy + teal-stripe on white) for light surfaces; one-color white lockup for the dark sidebar rail and any teal-fill hero treatment.
- **Personality:** Practical, reliable, efficient. The product should feel more like an operator's workspace than a marketing surface.

## Typography
- **Brand wordmark:** Inter (700), lowercase, letter-spacing -1% to -2% (`--cl-font-brand`)
- **Display/Headings:** Instrument Sans (600/700)
- **Body:** DM Sans (400/500), or Inter (500/600) on workspace surfaces (`--cl-font-body`)
- **Data/Numbers:** Geist Mono (400/500/600)
- **Code:** Geist Mono
- **Scale:**
  - H1: 36-40px / 600-700 / -0.03em
  - H2: 28px / 700 / -0.02em
  - H3: 20px / 600 / -0.01em
  - Body: 14px / 400
  - Small: 13px / 400
  - Caption: 12px / 500
  - Micro: 11px / 600 / uppercase
  - Data large: 28-32px / 600 / tabular-nums
  - Data inline: 13-14px / 500 / tabular-nums

## Color

### Brand
| Token | Hex | Usage |
|-------|-----|-------|
| `--cl-teal-500` | `#18BFB0` | Primary CTA, active state, brand accent (flat) |
| `--cl-teal-400` | `#1FC7B6` | Gradient start, hover-light variant |
| `--cl-teal-600` | `#12B3A6` | Gradient end, hover-deep variant |
| `--cl-teal-soft` | `#DDF7F3` | Light status fills, supportive emphasis |
| `--cl-navy` | `#0A1F44` | Primary ink, dark controls, sidebar rail, logo navy bars |
| `--cl-navy-light` | `#1E293B` | Dark hover states |
| `--cl-mint*` (legacy) | aliased | Old `--cl-mint` / `--cl-mint-strong` / `--cl-mint-soft` tokens are aliased to the teal palette so existing call sites keep working until renamed. |

### Surfaces
| Token | Hex | Usage |
|-------|-----|-------|
| `--surface` | `#FFFFFF` | Cards, panels, inputs |
| `--bg` | `#FAFAF8` | Warm Gmail route background |

### Text
| Token | Hex | Usage |
|-------|-----|-------|
| `--ink` | `#0F172A` | Primary text |
| `--ink-secondary` | `#475569` | Supporting text |
| `--ink-muted` | `#94A3B8` | Timestamps, tertiary labels |

### Borders
| Token | Hex | Usage |
|-------|-----|-------|
| `--border` | `#E2E8F0` | Default borders |
| `--border-hover` | `#CBD5E1` | Hover borders, separators |

### Semantic
| Token | Hex | Soft | Usage |
|-------|-----|------|-------|
| `--success` | `#16A34A` | `#F0FDF4` | Approved, posted, connected |
| `--warning` | `#CA8A04` | `#FEFCE8` | Needs review, pending, setup incomplete |
| `--error` | `#DC2626` | `#FEF2F2` | Rejected, failed, blocked |
| `--info` | `#2563EB` | `#EFF6FF` | Informational state, system guidance |

## Spacing
- **Base unit:** 4px
- **Density:** Compact in the thread panel, comfortable in Gmail full-page routes
- **Scale:** 4, 8, 12, 16, 20, 24, 32, 48, 64
- **Default panel padding:** 24px
- **Default card gap:** 12-20px
- **Default route padding:** 20-32px depending on breakpoint

## Layout
- **Approach:** Grid-disciplined and list-first.
- **Sidebar/thread panel:** compact, single-column, optimized for “current record” decisions.
- **Full-page Gmail routes:** wide, flat, and scan-friendly.
- **Content widths:**
  - Hub and queue pages target roughly `1200px` usable width and should never exceed about `1240px`
  - Form/setup/admin pages can narrow to `880-960px`
- **Border radius:** 6px / 8px / 12px only
- **Shadows:** extremely subtle; borders should do most of the structural work

## Gmail/AP MVP Information Architecture

### Primary Work Path
- `Pipeline`
- `Home`
- `Review`
- `Upcoming`

### Secondary Tools
- `Connections`
- `Activity`
- `Vendors`
- `Templates`
- `Approval Rules`
- `Team`
- `Company`
- `Plan`
- `Reconciliation`
- `System Status`
- `Reports`

### Navigation Rules
- Default pinned Gmail nav stays intentionally sparse: `Pipeline` and `Home`
- `Review` and `Upcoming` are part of the core work path, but should not crowd the default left nav for every role
- Secondary tools should live under Home, in secondary navigation, or behind role gates
- Dynamic detail pages never appear as peers in the primary nav

## Home Pattern
- Home is a **lightweight foyer**, not the default control center.
- It should use this order:
  1. centered welcome / identity
  2. thin setup or status banner if needed
  3. horizontal quick-access strip
  4. broad 2-column panels for recent work, upcoming work, saved views, and tools
- Home should feel open and light.
- Home should not lead with:
  - KPI dashboards
  - big setup cards
  - long explanatory copy
  - admin/settings sprawl

## Workspace Surface Pattern
- The Workspace Home (`workspace.clearledgr.com/`) is the **coordination-layer control center** — the leader's daily landing page where they see what the agent is doing across every surface (Gmail, Slack, Teams, NetSuite SuiteApp, SAP Fiori extension) right now, what needs human judgment, what just shipped to ERP. It is **not** a foyer (Gmail-era doctrine, retained for the Gmail surface), and it is **not** a BILL.com / Ramp / Mixmax admin overview.
- Reference hierarchy:
  - **Linear** — sticky command-center feel, real-time activity, dense lists with status indicators
  - **Vercel deployments** — live activity stream is the page; metrics are sidecar
  - **Datadog overview** — professional density, real-time pulse, restrained typography
  - **Modal jobs** — running work primary, history secondary
  - **Stripe Dashboard** — typography discipline + tabular numerals (carries over from prior doctrine)
  - **Anti-references**: BILL.com, Ramp admin, Mixmax overview, generic SaaS dashboards.
- The **hero** of the page is the **agent activity ribbon** — a live SSE-driven stream of recent agent / operator actions across every surface. Each row: tone-dot + verb + subject + timestamp + actor + surface. The page literally changes while the leader watches. The ribbon renders **above** the stat strip so render order matches its hero status; stats are context for the live signal, not the lead.
- Stat tiles return as a compact control-center row (four dense tiles, each ~90px tall, tabular-nums, with a small live-pulse dot in the corner). Not a big BILL.com KPI row — a calm Linear / Vercel-style strip.
- Order on the workspace Home:
  1. Welcome header with date eyebrow, name, "coordination layer" sub, and one secondary + one primary action button (default secondary: `Open activity`; default primary: `Review exceptions`)
  2. Onboarding banner (only when `onboarding.completed === false`)
  3. Implementation checklist (only when setup is incomplete)
  4. **Agent activity ribbon (hero)** — live stream of last ~20 agent / operator actions
  5. **Compact stat strip** — 4 tiles: In flight · Awaiting approval · Processed this week · Agent exceptions
  6. Two-column main panels: Exception queue (1.4fr) + Top vendors (1fr)
  7. Approver workload (logistics, not scoring)
  8. System status footer (agent + Gmail + approval surface + ERP)
- The Workspace Home does **not** carry a horizontal "quick-access cards" strip. Quick navigation lives in the header buttons + the `⌘K` palette. Linear / Vercel / Datadog do not surface a quick-action card row on their landing pages.
- Anti-patterns specific to the workspace Home:
  - **Foyer framing** — calling the page a "lightweight foyer" or "hub" understates what it does. The workspace is where the leader watches the coordination layer; framing it as a foyer leads to a static page with no live signal.
  - **BILL.com KPI tile row** — big static numbers leading the page with no live pulse, no activity context. The numbers belong as compact tiles, not as the hero element.
  - **Sticky `Loading…` placeholders** — every panel falls through to an empty or error state. Each panel fetches independently; one slow endpoint never gates the rest of the page.
  - **Static-only data** — if the page doesn't change while the leader watches, the live SSE stream is broken or under-used. The activity ribbon is the canary for "is the control center actually live?"

## Home Pattern (Gmail surface only)
- The §Home Pattern above (welcome → quick-access strip → 2-col panels) applies to the **Gmail extension's Home route**, where Streak's foyer model is the right reference. The Workspace Surface Pattern (this section) supersedes it for `workspace.clearledgr.com/`.

## Records Pattern (workspace)
- The workspace `/records` page is a **read-only directory** of AP records. It is for search, filter, and inspection, not for batch decisions. The workspace doesn't run the workflow — it surfaces state and intervenes when the agent escalates.
- Keep: search, filter sheet (vendor / due / blocker / ERP status / amount / approval age / sort), saved views, scope toggle (All open / Exceptions / Overdue), click-to-detail.
- Strip (do not reintroduce): Kanban columns, bulk-action toolbars, per-row Approve / Reject / Post / Snooze / Retry-post / Escalate, "route low-risk approval" inline actions. Those decisions live in Slack and Teams (approval cards) and in Gmail (vendor follow-up). The earlier "Live AP queue" Kanban with a `BatchOps` toolbar was a Streak / BILL-shaped workflow desktop and is the anti-pattern.
- Density target: flat table or list, monospace where it helps, one row per record with vendor / reference / amount / state / blocker / age / due. Linear / Datadog grammar.
- The detail page (`/records/:id`) is the intervention surface — read-only by default, mutating action bar only when the agent has escalated the record (state ∈ {needs_info, needs_approval, needs_second_approval, pending_approval, failed_post} or open exception) or the 15-minute override window is still open for an autonomous ERP post.
- Render-target handoff strip sits above the action bar with deep links to Slack / Teams / Gmail / ERP when those URLs are on the payload — the durable answer to "where do decisions actually live?"
- Anti-patterns specific to the records detail page:
  - **Approve / Reject buttons in the workspace.** Those are Slack and Teams affordances; the workspace shouldn't double as a third approval surface.
  - **Workflow-desktop ergonomics** (session timers, cleared-counters, auto-advance, "burst mode"). The workspace is a control center, not an inbox-zero processor.

## Gmail Pipeline Pattern (Gmail extension only)
- The Gmail extension still ships a Pipeline route (Streak's foyer + queue model is right for the Gmail surface). The workspace Records page is **not** that — it's the coordination-layer companion, not the daily processing surface.

## Thread Panel Pattern
- One record at a time
- One clear primary action
- Status and blockers above everything else
- Evidence and audit are visible but compact
- No dashboards, debug panels, or generic assistant chatter

## Motion
- **Approach:** Minimal and functional
- **Durations:** 100-150ms for hover/focus/route transitions
- **Rules:**
  - No decorative animation
  - No bounces or springs
  - Route changes can fade lightly
  - Quick-access and row hovers can lift by 1px at most
  - Respect `prefers-reduced-motion`

## Component Patterns

### Buttons
- **Primary:** mint background, navy text
- **Secondary:** white background, border, dark text
- **Ghost:** transparent background, muted ink or brand-muted text
- **Destructive:** red background, white text

### Status Pills
- 11px, uppercase, 600 weight
- Soft fill + semantic text color
- Used sparingly for state, readiness, and concise context

### Quick Access Cards
- Flat cards with clear labels and one-line descriptions
- Designed for horizontal scanning
- No big icon circles or decorative illustration

### Panels
- White surface, 1px border, 12px radius
- Title + optional small action in the header
- Empty states should be calm, centered, and non-alarming

### Tables and Lists
- Instrument Sans uppercase headers where needed
- DM Sans body copy
- Geist Mono for money, IDs, and timestamps
- Hover should be subtle and quiet

## Voice and Copy
- Use short action-first labels: `Open pipeline`, `Review`, `Upcoming`, `Connect Gmail`
- Prefer plain English over system terms
- Explain only when necessary
- Avoid words like `operator surface`, `workflow object`, `execution layer`, or `finance artifact` in UI copy
- The UI should sound like a capable tool, not an internal demo

## Anti-Patterns
- Treating Gmail/AP as the entire product
- Treating Streak as the whole company instead of the Gmail wedge interaction model
- Dashboard-heavy Home pages
- Setup cards dominating the first screen
- Too many top-level nav items
- Long explanatory subtitles everywhere
- Decorative gradients, glossy shadows, purple accents, or bubbly shapes
- Turning Gmail routes into a separate admin console
- Defaulting to dark mode in Gmail

## Decisions Log
| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-03-18 | Initial design system created | Established initial typography, brand colors, and embedded-product framing |
| 2026-03-23 | Repositioned Clearledgr as an embedded finance-ops execution layer | The company is broader than the Gmail/AP MVP wedge |
| 2026-03-23 | Defined “Streak for finance ops” as the Gmail/AP interaction model | Streak is the right model for the first wedge, not the full product boundary |
| 2026-03-23 | Home redefined as a hub, not a dashboard | The product should resume work quickly instead of explaining itself |
| 2026-03-23 | Primary Gmail work path narrowed to Home, Pipeline, Review, Upcoming | Keeps the product legible and operational inside Gmail |
| 2026-05-02 | Workspace Home defined as a foyer, not a BILL.com / Ramp dashboard | Page was leading with a big KPI tile row, modeled on Bill / Ramp / Mixmax. Mo flagged it. Numbers belong as a thin glance line under the welcome; quick access lifts to position #3; two-column panels carry the work. Streak / Stripe / Mercury references stand. |
| 2026-05-02 | Workspace Home recalibrated: coordination-layer control center, not foyer | Mo: DESIGN.md was Gmail-era (Streak foyer). Once Solden broadened to a coordination layer with Gmail / Slack / Teams / NetSuite / SAP as render targets and the workspace as the control center, the foyer doctrine no longer fits the workspace surface. Reference hierarchy moves to Linear / Vercel / Datadog / Modal (still anti-Bill / anti-Ramp). Hero becomes the live agent activity ribbon (SSE-driven); stat tiles return as a compact control-center row with a live-pulse dot; quick-access cards drop (header + ⌘K cover navigation). Foyer pattern preserved for the Gmail Home only. |
| 2026-05-02 | Solden rebrand applied | Mo lifted the rebrand hold and shipped the brand kit: navy `#0A1F44`, teal palette `#1FC7B6 / #18BFB0 / #12B3A6`, white. Wordmark "solden" in Inter 700 lowercase, tracking -1% to -2%. Logomark is a three-slab stylized S (navy bars + teal middle stripe). DESIGN.md, the SidebarNav, login + invite-accept cards, footer, page title, legal copy, and operational status strings all swept from "Clearledgr" → "Solden". Domain stays at clearledgr.com for now (cookie domain + SAML SP URLs unchanged); `@clearledgr.com` mailto addresses moved to `@soldenai.com`. Old `--cl-mint*` tokens aliased to the new teal palette so unfinished call sites keep compiling. |
