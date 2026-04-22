// Thesis-compliance guards for the Gmail extension.
//
// Static-source checks that enforce the architectural commitments in
// DESIGN_THESIS.md across every Gmail render surface. These tests are
// intentionally coarse — they do not load the component tree. They
// grep the source files for patterns that would indicate a drift from
// thesis. Cheap, fast, surface-agnostic.
//
// If a test here fails, do NOT loosen the assertion. Either fix the
// offending file or take the question to a product-level discussion
// and amend the thesis in writing.
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import fs from 'node:fs';

const readFile = (relPath) =>
  fs.readFileSync(new URL(relPath, import.meta.url), 'utf8');

describe('DESIGN_THESIS §6.3: Gmail = work surface, Slack = decision surface', () => {
  // Every file below represents a Gmail render surface (one of the
  // seven InboxSDK injection points). None of them may register an
  // "Approve" action that directly writes to the ERP from a
  // needs_approval state. Approvals route to Slack.

  it('ThreadSidebar (Conversations injection point) has no approve action', () => {
    const src = readFile('./components/ThreadSidebar.js');
    assert.doesNotMatch(src, /cl-ts-approve-btn/,
      'approve button CSS class must not exist in the sidebar');
    assert.doesNotMatch(src, /onApprove/,
      'onApprove prop must not be wired into the sidebar');
    assert.doesNotMatch(src, /postToErp|approveAndPost/,
      'sidebar must not call the post-to-ERP method directly (old or new name)');
  });

  it('Thread toolbar (Toolbars injection point) has no Approve button', () => {
    const src = readFile('./inboxsdk-layer.js');
    // The thesis-violating pattern was a registerThreadButton with
    // title:'Approve' that called queueManager.postToErp / approveAndPost.
    // Either form in the toolbar registration surface is a regression.
    const toolbarRegistration = src.match(
      /sdk\.Toolbars\.registerThreadButton\(\{[^}]*?title:\s*['"]Approve['"][\s\S]*?\}\)/
    );
    assert.equal(toolbarRegistration, null,
      'no Gmail thread-toolbar button may be titled "Approve" — approvals route to Slack');

    // Defence-in-depth: the post-to-ERP method must not be called from the
    // inboxsdk-layer (which is the toolbar registration surface). Check both
    // the canonical name and the legacy name so drift in either direction fails.
    assert.doesNotMatch(src, /queueManager\.(postToErp|approveAndPost)/,
      'inboxsdk-layer must not invoke post-to-ERP from a toolbar button — that surface is work, not decision');
  });
});
