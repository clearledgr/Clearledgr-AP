import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import fs from 'node:fs';

const source = fs.readFileSync(new URL('./ThreadSidebar.js', import.meta.url), 'utf8');

describe('ThreadSidebar contract', () => {
  it('renders the four fixed sections in thesis §6.6 order', () => {
    // InvoiceSection → MatchSection → VendorSection → AgentActionsSection
    const invoiceIdx = source.indexOf('InvoiceSection');
    const matchIdx = source.indexOf('MatchSection');
    const vendorIdx = source.indexOf('VendorSection');
    const agentIdx = source.indexOf('AgentActionsSection');
    assert.ok(invoiceIdx > 0 && matchIdx > 0 && vendorIdx > 0 && agentIdx > 0,
      'expected all four section components defined');
    // The usage order inside the main component must be Invoice → Match → Vendor → ... → Agent.
    const bodyStart = source.indexOf('<div class="cl-thread-sidebar">', source.indexOf('function ThreadSidebar'));
    const bodyEnd = source.indexOf('</div>', bodyStart + 10);
    // Find the main component JSX block (last occurrence which is the render return)
    const jsx = source.substring(source.indexOf('<${InvoiceSection}'));
    const iUse = jsx.indexOf('<${InvoiceSection}');
    const mUse = jsx.indexOf('<${MatchSection}');
    const vUse = jsx.indexOf('<${VendorSection}');
    const aUse = jsx.indexOf('<${AgentActionsSection}');
    assert.ok(iUse >= 0 && mUse > iUse && vUse > mUse && aUse > vUse,
      'sections must render in Invoice → Match → Vendor → Agent order');
  });

  it('renders an override-window banner with a live countdown and Undo button', () => {
    assert.match(source, /function OverrideWindowBanner/);
    assert.match(source, /formatCountdown/);
    assert.match(source, /to undo/);
    // The banner must call back to the parent to actually reverse the post.
    assert.match(source, /onUndo\(window_\)/);
  });

  it('renders a waiting-condition banner when the agent is paused', () => {
    assert.match(source, /function WaitingBanner/);
    assert.match(source, /Waiting for/);
    assert.match(source, /humanizeWaitingType/);
    // Known waiting types from spec §12 must be mapped to human labels
    assert.match(source, /grn_check/);
    assert.match(source, /external_dependency_unavailable/);
    assert.match(source, /approval_response/);
  });

  it('renders a fraud-flags banner filtering out resolved flags', () => {
    assert.match(source, /function FraudFlagsBanner/);
    // Resolved flags must be filtered out so the banner hides itself
    // when every fraud flag has been resolved.
    assert.match(source, /!f\.resolved_at/);
    assert.match(source, /fraud \$\{active\.length === 1 \? 'flag' : 'flags'\} active/);
  });

  it('renders a resubmission lineage banner', () => {
    assert.match(source, /function ResubmissionBanner/);
    assert.match(source, /is_resubmission/);
    assert.match(source, /has_resubmission/);
    assert.match(source, /Superseded by newer invoice/);
  });

  it('surfaces match-tolerance delta in the 3-way match section', () => {
    // §8.1: "Matched — passed within 0.3% tolerance"
    assert.match(source, /match_amount_delta_pct/);
    assert.match(source, /match_tolerance_pct/);
    assert.match(source, /Δ /);
    assert.match(source, /cl-ts-match-tolerance/);
  });

  it('has no inline style attributes that would violate CSP', () => {
    // All colors/sizes must be in the THREAD_SIDEBAR_CSS const, not
    // inline style="..." attributes on rendered elements. Strings inside
    // CSS itself are fine (the CSS is injected via <style>).
    const cssStart = source.indexOf('const THREAD_SIDEBAR_CSS = `');
    const cssEnd = source.indexOf('`;', cssStart);
    const beforeCss = source.substring(0, cssStart);
    const afterCss = source.substring(cssEnd);
    const renderRegion = beforeCss + afterCss;
    // Find every `style="..."` attribute in the render region
    const styleAttrs = renderRegion.match(/\bstyle="[^"]+"/g) || [];
    // One permitted exception: the empty-state hint and skeleton widths
    // (both are trivial layout sizing, not design tokens).
    const problematic = styleAttrs.filter((s) => !s.includes('width:') && !s.includes('font-size: 12px; color: #94A3B8'));
    assert.equal(problematic.length, 0,
      `expected no inline style attrs outside CSS block, found: ${problematic.join(' | ')}`);
  });

  it('shows a loading skeleton when explicitly loading', () => {
    assert.match(source, /function LoadingSkeleton/);
    assert.match(source, /if \(loading\) return html`<\${LoadingSkeleton}/);
    assert.match(source, /cl-ts-skeleton/);
  });

  it('ticks a 1-second interval only while an override window is open', () => {
    // The countdown should only poll when a window_ is actually present,
    // not every second forever. The useEffect depends on expires_at.
    assert.match(source, /if \(!item\?\.override_window\?\.expires_at\) return;/);
    assert.match(source, /setInterval\(\(\) => setNowMs\(Date\.now\(\)\), 1000\)/);
    assert.match(source, /clearInterval\(handle\)/);
  });

  it('humanizes long snake_case event_type strings before rendering', () => {
    // Regression: raw event_type / decision_reason values like
    // "ap_invoice_processing_field_review_required" forced horizontal
    // scroll because the browser would not wrap them, and the humanizer
    // was only called on event_type (not decision_reason which usually wins).
    assert.match(source, /function humanizeEventType/);
    const fnBody = source.match(/function humanizeEventType[^]*?\n\}/)[0];
    const fn = new Function(`${fnBody}; return humanizeEventType;`)();
    assert.equal(
      fn('ap_invoice_processing_field_review_required'),
      'Invoice processing — field review required',
    );
    assert.equal(fn('agent_action:apply_label'), 'Apply label');
    // Empty input defaults to '' so optional fields don't render a placeholder
    assert.equal(fn(''), '');
    assert.equal(fn(null), '');
    // With explicit fallback (used for the required "what" label)
    assert.equal(fn('', { fallback: 'Action' }), 'Action');
    assert.equal(fn(null, { fallback: 'Action' }), 'Action');
    // Already-humanized strings (has a space, no underscores) pass through
    assert.equal(fn('Approved by AP Manager'), 'Approved by AP Manager');
    // Long pathological string — capped at 80 chars
    const huge = 'a'.repeat(200);
    assert.ok(fn(huge).length <= 80);
  });

  it('humanizes decision_reason / reason when they win the fallback chain', () => {
    // The sidebar call site must humanize whatever string wins, not only
    // event_type. Backend's append_ap_audit_event stores the `reason` arg
    // as `decision_reason`, which is often a raw snake_case token.
    const callSite = source.match(/const what = humanizeEventType\([^)]*\)/s);
    assert.ok(callSite, 'what must be computed via humanizeEventType');
    assert.match(callSite[0], /e\.summary \|\| e\.decision_reason \|\| e\.event_type/);
  });

  it('skips the why line when it is identical to what', () => {
    // When decision_reason == reason (backend sometimes duplicates), don't
    // render "X — X" in the timeline.
    assert.match(source, /humanizedWhy && humanizedWhy !== what/);
  });

  it('prevents horizontal overflow — word-break CSS on every descendant', () => {
    assert.match(source, /\.cl-thread-sidebar \{ [^}]*overflow-x: hidden/);
    assert.match(source, /\.cl-thread-sidebar, \.cl-thread-sidebar \* \{[^}]*overflow-wrap: anywhere/);
  });

  it('humanizes known waiting-condition types', () => {
    const mapIdx = source.indexOf('function humanizeWaitingType');
    assert.ok(mapIdx > 0, 'humanizeWaitingType function must exist');
    const mapBlock = source.substring(mapIdx, mapIdx + 800);
    assert.match(mapBlock, /grn_check: 'GRN confirmation'/);
    assert.match(mapBlock, /approval_response: 'approval'/);
    assert.match(mapBlock, /external_dependency_unavailable: 'ERP to come back online'/);
  });

  it('does not render an approve primary action in the sidebar — DESIGN_THESIS.md §6.3', () => {
    // Thesis commitment: "Sidebar does NOT have approve/reject buttons —
    // those route to Slack/Teams." Decisions live on the decision surface
    // (Slack), not the work surface (Gmail sidebar). An approve button
    // in the sidebar bypasses the runtime intent bus and creates a
    // second audit trail — both unacceptable.
    assert.doesNotMatch(source, /cl-ts-approve-btn/,
      'approve button CSS class must not exist in the sidebar');
    assert.doesNotMatch(source, /onApprove/,
      'onApprove prop must not be wired into the sidebar');
    assert.doesNotMatch(source, /approveAndPost/,
      'sidebar must not call approveAndPost directly');
  });

  it('renders an awaiting-approval notice when the Box is in needs_approval', () => {
    // Replacement for the removed approve button. The sidebar explains
    // WHY there is no button here — otherwise users trained on the old
    // UI will think the sidebar is broken.
    assert.match(source, /cl-ts-awaiting-approval-title/);
    assert.match(source, /Awaiting approval in Slack/);
    // Gated on needs_approval / pending_approval state.
    assert.match(source, /needsApproval = state === 'needs_approval' \|\| state === 'pending_approval'/);
    assert.match(source, /\$\{needsApproval \? html`/);
  });
});
