# Gmail Sidebar Reset Evidence (B15-B19)

Date: 2026-03-01
Release: `ap-v1-2026-02-25-pilot-rc1`

## Scope
- B15 Sidebar IA split (Work vs Ops)
- B16 Inline reason-sheet migration
- B17 Work action-first compression
- B18 Ops relocation of KPI/batch/timeline/audit
- B19 Regression test realignment

## Before / After Screenshots
- Before (legacy cluttered mixed panel):
  - `artifacts/sidebar-reset-before.png`
- After (split panel baseline runtime capture):
  - `artifacts/sidebar-reset-after-work.png`

## Validation Commands
- `cd /Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension && node --test tests/inboxsdk-layer.integration.test.cjs tests/inboxsdk-layer-ui.test.cjs`
  - Result: `26 passed`
- `cd /Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension && npm run build`
  - Result: `webpack build succeeded`

## Assertions Covered
1. Work panel no longer renders Ops KPI/batch/full-audit blocks.
2. Ops panel renders KPI snapshot, batch operations, full agent timeline, and full audit timeline.
3. Reason capture paths are inline-sheet based; no source `prompt()/confirm()` calls.
4. Work panel uses collapsed evidence/details sections and compact recent activity strip.

## Notes
- Backend API contracts were unchanged for this UX reset.
- This evidence file is paired with tracker entries `B15-B19` in `/Users/mombalam/Desktop/Clearledgr.v1/docs/BETA_ALIGNMENT_FIX_TRACKER.md`.
