/**
 * Phase 3.2 — thread-top approval banner contract.
 *
 * The approval banner is the second contextual surface (after 3.1's
 * exception banner). It sits between exception and state banners so
 * the visual stack reads exception → approval → state, with the most
 * actionable signal closest to the message body. Same Streak/Fyxer
 * pattern: invisible AI, native Gmail primitives.
 *
 * This file pins the contract so a future refactor can't silently:
 *   1. Drop the approver/wait-time context.
 *   2. Re-order the banner stack.
 *   3. Lose the SLA / escalation severity branching.
 *   4. Re-route the CTA away from openItemInPipeline.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const SOURCE = fs.readFileSync(
  path.resolve(__dirname, '..', 'src', 'inboxsdk-layer.js'),
  'utf8',
);

test('injectApprovalBanner is defined alongside the exception + state banners', () => {
  assert.match(SOURCE, /function injectApprovalBanner\(threadView, item\)/);
  assert.match(SOURCE, /function injectExceptionBanner\(threadView, item\)/);
  assert.match(SOURCE, /function injectInvoiceBanner\(threadView, item\)/);
});

test('approval banner only injects when state is needs_approval / pending_approval', () => {
  const fnMatch = SOURCE.match(/function _itemAwaitsApproval\(item\)[\s\S]+?\}\s*\n/);
  assert.ok(fnMatch, '_itemAwaitsApproval helper missing');
  const body = fnMatch[0];
  assert.match(body, /'needs_approval'/);
  assert.match(body, /'pending_approval'/);

  // The banner function must guard on this predicate.
  assert.match(
    SOURCE,
    /function injectApprovalBanner\(threadView, item\) \{\s*if \(!_itemAwaitsApproval\(item\)\) return;/,
  );
});

test('approval banner suppresses itself when neither wait time nor approver is known', () => {
  // Without wait_minutes or pending_assignees, the banner adds no info
  // beyond the state banner's "NEEDS APPROVAL" pill — render nothing.
  const guardMatch = SOURCE.match(
    /if \(waitMinutes <= 0 && !approvers\) return;/,
  );
  assert.ok(guardMatch, 'approval banner must early-return when there is no wait/approver context');
});

test('banner stack order: exception → approval → state', () => {
  // InboxSDK addNoticeBar prepends to the notice list, so the call
  // order in the handler is the REVERSE of the rendered stack. The
  // first call ends up at the top of the visual stack.
  const handlerBlock = SOURCE.match(
    /Inject thread-top banners[\s\S]+?injectInvoiceBanner\(threadView, item\);[\s\S]+?\}/,
  );
  assert.ok(handlerBlock, 'thread handler banner-injection block not found');
  const body = handlerBlock[0];
  const eIdx = body.indexOf('injectExceptionBanner');
  const aIdx = body.indexOf('injectApprovalBanner');
  const sIdx = body.indexOf('injectInvoiceBanner');
  assert.ok(
    eIdx > 0 && aIdx > eIdx && sIdx > aIdx,
    'thread handler must call injectExceptionBanner → injectApprovalBanner → injectInvoiceBanner',
  );
});

test('"View details" CTA routes through openItemInPipeline with thread_approval_banner source', () => {
  // Each banner has its own source tag so telemetry can attribute
  // sidebar opens to the contextual surface that triggered them.
  assert.match(SOURCE, /openItemInPipeline\(item, 'thread_approval_banner'\)/);
});

test('urgency config branches on escalation_due → sla_breached → within-SLA', () => {
  // Escalation is the most urgent — it must out-rank sla_breached
  // even when both are true on the same followup payload.
  const start = SOURCE.indexOf('function _approvalUrgencyConfig(followup)');
  assert.ok(start > 0, '_approvalUrgencyConfig helper missing');
  // Find the next top-level function declaration after this one — that
  // marks the boundary of the body we want to inspect.
  const nextFn = SOURCE.indexOf('\nfunction ', start + 1);
  const body = SOURCE.slice(start, nextFn > 0 ? nextFn : SOURCE.length);
  const escIdx = body.indexOf('escalation_due');
  const slaIdx = body.indexOf('sla_breached');
  assert.ok(escIdx > 0 && slaIdx > escIdx,
    'escalation_due must be checked before sla_breached so the more urgent label wins');
  assert.match(body, /label: 'Escalate'/);
  assert.match(body, /label: 'SLA breached'/);
  assert.match(body, /label: 'Waiting'/);
});

test('humanize formatters cover sub-hour, sub-day, multi-day, and email-stripped approvers', () => {
  // Run the formatters in-process via a Function-eval shim so we
  // don't have to export them from the layer module (keeps the
  // module's module-level side effects out of the test environment).
  const waitFn = SOURCE.match(/function _humanizeWaitMinutes\(minutes\)[\s\S]+?\n\}\n/);
  const apprFn = SOURCE.match(/function _formatApprovers\(assignees\)[\s\S]+?\n\}\n/);
  assert.ok(waitFn && apprFn, 'helper functions missing');
  // eslint-disable-next-line no-new-func
  const ctx = new Function(
    `${waitFn[0]}\n${apprFn[0]}\nreturn { _humanizeWaitMinutes, _formatApprovers };`,
  )();
  // Wait formatter
  assert.equal(ctx._humanizeWaitMinutes(0), '0m');
  assert.equal(ctx._humanizeWaitMinutes(45), '45m');
  assert.equal(ctx._humanizeWaitMinutes(60), '1h');
  assert.equal(ctx._humanizeWaitMinutes(135), '2h 15m');
  assert.equal(ctx._humanizeWaitMinutes(60 * 26), '1d 2h');
  assert.equal(ctx._humanizeWaitMinutes(60 * 24 * 3), '3d');
  // Approver formatter — strips domain, caps at 2 names, "+N more" for the rest
  assert.equal(ctx._formatApprovers([]), '');
  assert.equal(ctx._formatApprovers(['mo@x.com']), 'mo');
  assert.equal(ctx._formatApprovers(['mo@x.com', 'sarah@y.com']), 'mo, sarah');
  assert.equal(
    ctx._formatApprovers(['mo@x.com', 'sarah@y.com', 'jane@z.com', 'bob@q.com']),
    'mo, sarah, +2 more',
  );
});
