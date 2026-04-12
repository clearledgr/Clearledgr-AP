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

  it('humanizes known waiting-condition types', () => {
    const mapIdx = source.indexOf('function humanizeWaitingType');
    assert.ok(mapIdx > 0, 'humanizeWaitingType function must exist');
    const mapBlock = source.substring(mapIdx, mapIdx + 800);
    assert.match(mapBlock, /grn_check: 'GRN confirmation'/);
    assert.match(mapBlock, /approval_response: 'approval'/);
    assert.match(mapBlock, /external_dependency_unavailable: 'ERP to come back online'/);
  });
});
