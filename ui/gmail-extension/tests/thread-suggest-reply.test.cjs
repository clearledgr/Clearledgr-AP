/**
 * Phase 3.3 — "Suggest reply" CTA on the exception banner.
 *
 * The button is rendered inside `injectExceptionBanner` next to
 * "View details", and on click calls `suggestReplyForItem(item)`,
 * which POSTs to /extension/draft-reply and hands the resulting
 * subject/body/to off to `openComposeWithPrefill` — the same compose
 * pre-fill plumbing the sidebar's "Draft vendor reply" action uses.
 *
 * This file pins the contract so a future refactor can't silently:
 *   1. Drop the Suggest-reply button from the exception banner.
 *   2. Re-route the button to a different endpoint.
 *   3. Bypass `openComposeWithPrefill` (and lose the compose-record
 *      status bar + audit linkage that comes with it).
 *   4. Move the button onto the approval banner — vendor replies
 *      target vendors; approvals route to internal Slack/Teams.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const SOURCE = fs.readFileSync(
  path.resolve(__dirname, '..', 'src', 'inboxsdk-layer.js'),
  'utf8',
);

test('Suggest-reply button is rendered inside injectExceptionBanner', () => {
  const start = SOURCE.indexOf('function injectExceptionBanner(threadView, item)');
  assert.ok(start > 0, 'injectExceptionBanner not found');
  const nextFn = SOURCE.indexOf('\nfunction ', start + 1);
  const body = SOURCE.slice(start, nextFn > 0 ? nextFn : SOURCE.length);
  assert.match(body, /Suggest reply/);
  assert.match(body, /suggestReplyForItem\(item\)/);
});

test('Suggest-reply button is NOT on the approval banner', () => {
  // Approval banners route to internal approvers via Slack/Teams; we
  // explicitly do not draft vendor replies for them. If a future
  // refactor adds Suggest-reply to the approval banner we want to
  // catch it here so the routing semantics stay clean.
  const start = SOURCE.indexOf('function injectApprovalBanner(threadView, item)');
  assert.ok(start > 0, 'injectApprovalBanner not found');
  const nextFn = SOURCE.indexOf('\nfunction ', start + 1);
  const body = SOURCE.slice(start, nextFn > 0 ? nextFn : SOURCE.length);
  assert.ok(
    !body.includes('Suggest reply'),
    'approval banner must not carry a Suggest-reply button',
  );
  assert.ok(
    !body.includes('suggestReplyForItem'),
    'approval banner must not call suggestReplyForItem',
  );
});

test('suggestReplyForItem POSTs to /extension/draft-reply with ap_item_id + thread_id', () => {
  const start = SOURCE.indexOf('async function suggestReplyForItem(item)');
  assert.ok(start > 0, 'suggestReplyForItem helper missing');
  const nextFn = SOURCE.indexOf('\nasync function ', start + 1);
  const altNextFn = SOURCE.indexOf('\nfunction ', start + 1);
  const end = [nextFn, altNextFn].filter((idx) => idx > 0).reduce((a, b) => Math.min(a, b), SOURCE.length);
  const body = SOURCE.slice(start, end);

  // Endpoint contract.
  assert.match(body, /\/extension\/draft-reply/);
  assert.match(body, /method: 'POST'/);

  // Both identifiers go on the wire so the server can resolve from
  // either side — ap_item_id wins when available, thread_id is the
  // fallback for repaired threads where the item id isn't yet bound.
  assert.match(body, /ap_item_id: String\(item\.id\)/);
  assert.match(body, /thread_id: String\(item\.thread_id/);
  assert.match(body, /organization_id: queueManager\?\.runtimeConfig\?\.organizationId/);
});

test('suggestReplyForItem hands off to openComposeWithPrefill (NOT raw sdk.Compose)', () => {
  // Going through openComposeWithPrefill keeps the compose-record
  // status bar + queue-linkage that the existing "Draft vendor reply"
  // sidebar action gets. Using sdk.Compose.openNewComposeView()
  // directly would drop both — pin the contract.
  const start = SOURCE.indexOf('async function suggestReplyForItem(item)');
  assert.ok(start > 0);
  const altNextFn = SOURCE.indexOf('\nfunction ', start + 1);
  const body = SOURCE.slice(start, altNextFn > 0 ? altNextFn : SOURCE.length);

  assert.match(body, /openComposeWithPrefill\(\{/);
  assert.match(body, /to: draft\.to/);
  assert.match(body, /subject: draft\.subject/);
  assert.match(body, /body: draft\.body/);
  assert.match(body, /recordContext: buildComposeRecordContext\(item\)/);
});

test('Suggest-reply click handler shows a transient "Drafting…" state', () => {
  // The compose round-trip can take ~500ms-1s under load; without a
  // visible state change a user double-clicks and we get two compose
  // tabs. Pin the in-flight UI feedback so it doesn't quietly regress.
  const start = SOURCE.indexOf('function injectExceptionBanner(threadView, item)');
  const altNextFn = SOURCE.indexOf('\nfunction ', start + 1);
  const body = SOURCE.slice(start, altNextFn > 0 ? altNextFn : SOURCE.length);

  assert.match(body, /replyBtn\.disabled = true;/);
  assert.match(body, /Drafting…/);
  assert.match(body, /\.finally\(/);
});
