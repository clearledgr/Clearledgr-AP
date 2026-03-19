# Design System — Clearledgr

## Product Context
- **What this is:** The execution layer for finance operations — AI agents embedded in the tools finance teams already use
- **Who it's for:** Finance teams at growing companies (50-500 employees)
- **Space/industry:** Fintech / finance operations / embedded tools
- **Project type:** Embedded product (lives inside Gmail, Slack, Google Sheets, ERPs — not a standalone app)
- **Surfaces:** Gmail sidebar, full-page views inside Gmail (via InboxSDK Router), inbox row decorations, thread banners, compose enhancements, Gmail Settings tab, Slack/Teams cards

## Aesthetic Direction
- **Direction:** Industrial/Utilitarian
- **Decoration level:** Minimal — typography and color do the work. No gradients, no shadows beyond subtle elevation. Borders and whitespace create structure.
- **Mood:** Precision instrument for finance. Data-dense, function-first, trustworthy. The warm mint-on-navy brand prevents it from being cold or corporate.
- **Reference sites:** Stripe Dashboard (typography clarity), Ramp (restrained palette), Mercury (minimal data presentation), Streak (Gmail-native integration)

## Brand Identity
- **Logo:** Two vertical bars (ledger icon) — mint green on navy rounded square
- **Brand color:** Mint green `#00D67E`
- **Brand dark:** Navy `#0A1628`
- **Personality:** Precise, modern, trustworthy. Agents that execute, not dashboards that display.

## Typography
- **Display/Headings:** Instrument Sans (600/700) — geometric, clean, modern without being generic. Distinctive alternative to Inter.
- **Body:** DM Sans (400/500) — excellent readability at small sizes, slightly warmer than Inter, supports tabular-nums.
- **Data/Numbers:** Geist Mono (400/500/600) — for amounts, invoice numbers, timestamps. Monospace signals precision.
- **Code:** Geist Mono
- **Loading:** Google Fonts for Instrument Sans + DM Sans. Geist Mono via jsdelivr CDN.
- **Scale:**
  - H1: 36px / 700 / -0.02em (page titles)
  - H2: 28px / 700 / -0.02em (section headings)
  - H3: 20px / 600 / -0.01em (panel headings)
  - Body: 14px / 400 / normal
  - Small: 13px / 400 (secondary text)
  - Caption: 12px / 500 (labels, meta)
  - Micro: 11px / 600 (pills, badges, uppercase)
  - Data large: 28-32px / 600 / -0.02em / tabular-nums (KPI values)
  - Data inline: 13-14px / 500 / tabular-nums (table amounts)

## Color

### Brand
| Token | Hex | Usage |
|-------|-----|-------|
| `--brand` | `#00D67E` | Primary CTA buttons, active states, logo, app menu icon |
| `--brand-hover` | `#00BC6E` | Button hover state |
| `--brand-soft` | `#ECFDF5` | Light brand background (badges, highlights) |
| `--brand-muted` | `#10B981` | Softer green for links, posted status, KPI accents |
| `--navy` | `#0A1628` | Sidebar headers, dark contexts, secondary buttons |
| `--navy-light` | `#1E293B` | Navy hover state, dark panels |

### Surfaces
| Token | Hex | Usage |
|-------|-----|-------|
| `--surface` | `#FFFFFF` | Cards, panels, inputs |
| `--bg` | `#FAFAF8` | Page background (warm off-white) |

### Text
| Token | Hex | Usage |
|-------|-----|-------|
| `--ink` | `#0F172A` | Primary text (near-navy, not pure black) |
| `--ink-secondary` | `#475569` | Secondary text, descriptions |
| `--ink-muted` | `#94A3B8` | Tertiary text, timestamps, placeholders |

### Borders
| Token | Hex | Usage |
|-------|-----|-------|
| `--border` | `#E2E8F0` | Default borders, dividers |
| `--border-hover` | `#CBD5E1` | Hover state borders |

### Semantic
| Token | Hex | Soft | Usage |
|-------|-----|------|-------|
| `--success` | `#16A34A` | `#F0FDF4` | Approved, posted, connected |
| `--warning` | `#CA8A04` | `#FEFCE8` | Needs approval, pending, low confidence |
| `--error` | `#DC2626` | `#FEF2F2` | Rejected, failed, error |
| `--info` | `#2563EB` | `#EFF6FF` | Informational, validated |

### Dark Mode
- Surfaces: navy-based (`--bg: #0A1628`, `--surface: #0F172A`)
- Brand green: desaturated 10% (`#34D399`)
- Text: cool light grays (`--ink: #F1F5F9`, not pure white)
- Borders: `#1E293B`

## Spacing
- **Base unit:** 4px
- **Density:** Compact in sidebar, comfortable in full-page views
- **Scale:** 2(2xs) 4(xs) 8(sm) 12(md) 16(lg) 24(xl) 32(2xl) 48(3xl) 64(4xl)
- **Sidebar:** 4-8px gaps between elements
- **Full pages:** 12-20px gaps, 24px panel padding

## Layout
- **Approach:** Grid-disciplined — strict alignment, predictable columns
- **Grid:** Sidebar is fixed 300px. Full-page views max-width 1100px.
- **Max content width:** 1100px (full pages), 880px (form pages)
- **Border radius:** sm:6px, md:8px, lg:12px, full:9999px (pills)
- **Pipeline table is the hero layout** — sortable, filterable, data-dense

## Motion
- **Approach:** Minimal-functional — only transitions that aid comprehension
- **Easing:** enter(ease-out) exit(ease-in) move(ease-in-out)
- **Duration:** micro(50-100ms) short(150ms) medium(250ms)
- **Rules:**
  - No scroll-driven animations
  - No bounces or springs
  - State transitions: 150ms ease-out (hover, focus, active)
  - Page content fade: 150ms ease-out on route change
  - `prefers-reduced-motion`: disable all non-essential transitions

## Component Patterns

### Buttons
- **Primary:** `--brand` bg, `--navy` text (green on dark = high contrast)
- **Navy:** `--navy` bg, white text (for "Send for Approval" actions)
- **Secondary:** white bg, `--border` border, `--ink` text
- **Ghost:** transparent bg, `--brand-muted` text
- **Destructive:** `--error` bg, white text

### Status Pills
- Uppercase, 11px, 600 weight, 999px radius
- Warning (needs approval): `--warning-soft` bg, `--warning` text
- Brand (posted): `--brand-soft` bg, `--brand-muted` text
- Success (approved): `--success-soft` bg, `--success` text
- Error (rejected): `--error-soft` bg, `--error` text
- Neutral (received): `--bg` bg, `--ink-muted` text, `--border` border

### KPI Cards
- Geist Mono for values (28px, 600 weight, tabular-nums)
- DM Sans for labels (12px, 500 weight, muted color)
- 1px border, 8px radius, subtle shadow

### Pipeline Table
- Instrument Sans for column headers (11px, 600, uppercase, 0.04em tracking)
- DM Sans for body cells (13px)
- Geist Mono for amounts and invoice numbers
- Row hover: `--bg` background
- Sortable columns: cursor pointer, sort indicator arrows

### Sidebar (Gmail)
- Header: navy background, white text, green badge
- Compact spacing (4-8px)
- Invoice card: 1px border, 8px radius, 14px padding
- Monospace amounts at 24px

### Thread Banner
- Left border accent (3px)
- Color matches invoice state
- 13px body text, 11px pill

## Anti-Patterns (Never Do)
- Purple/violet gradients
- 3-column feature grid with icons in colored circles
- Uniform bubbly border-radius on everything
- Gradient buttons
- Using Inter, Roboto, or Poppins as primary font
- Pure black text (#000000) — always use `--ink` (#0F172A)
- Pure white backgrounds in Gmail context — use `--bg` (#FAFAF8) or `--surface` (#FFFFFF)

## Decisions Log
| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-03-18 | Initial design system created | Based on competitive research (Stripe, Ramp, Brex, Mercury, Streak) + Clearledgr brand identity (mint green + navy) |
| 2026-03-18 | Instrument Sans + DM Sans + Geist Mono | Distinctive typography that avoids overused Inter while maintaining professional quality |
| 2026-03-18 | Mint green `#00D67E` as primary accent | Matches existing brand logo; distinctive in finance space (most use blue/orange) |
| 2026-03-18 | Industrial/Utilitarian aesthetic | Finance = precision. Data-dense, function-first. Monospace for numbers signals accuracy. |
| 2026-03-18 | Minimal decoration | Product lives inside Gmail — must feel native, not decorative. Let data and typography speak. |
