/**
 * Phase 3.1 — thread-top exception banner contract.
 *
 * The banner is raw DOM injected by `injectExceptionBanner` in
 * `src/inboxsdk-layer.js` (companion to the existing
 * `injectInvoiceBanner` state banner). This file pins three things so
 * a future refactor can't silently regress the contract:
 *
 *   1. `injectExceptionBanner` exists and only renders when the AP
 *      item carries an active exception signal.
 *   2. The thread handler invokes the exception banner BEFORE the
 *      state banner so the most actionable signal sits closest to
 *      the message body.
 *   3. The "View details" CTA routes through `openItemInPipeline` with
 *      the `thread_exception_banner` source tag — the same plumbing
 *      the state banner already uses, so the click ends up at the
 *      same context the Exceptions tab would have surfaced.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const SOURCE = fs.readFileSync(
  path.resolve(__dirname, '..', 'src', 'inboxsdk-layer.js'),
  'utf8',
);

test('injectExceptionBanner is defined alongside injectInvoiceBanner', () => {
  assert.match(SOURCE, /function injectExceptionBanner\(threadView, item\)/);
  assert.match(SOURCE, /function injectInvoiceBanner\(threadView, item\)/);
});

test('exception banner only injects when item carries an active exception signal', () => {
  // Predicate must short-circuit on no-exception items so threads
  // without blockers don't render an empty warning bar.
  const fnMatch = SOURCE.match(/function _itemHasActiveException\(item\)[\s\S]+?return false;\s*\}/);
  assert.ok(fnMatch, '_itemHasActiveException helper missing');
  const body = fnMatch[0];
  assert.match(body, /item\.exception_code/);
  assert.match(body, /item\.requires_field_review/);
  assert.match(body, /field_review_blockers/);
  assert.match(body, /pipeline_blockers/);

  // The banner function must guard on this predicate, not on truthy item.
  const guardMatch = SOURCE.match(
    /function injectExceptionBanner\(threadView, item\) \{\s*if \(!_itemHasActiveException\(item\)\) return;/,
  );
  assert.ok(guardMatch, 'injectExceptionBanner must early-return when no exception is present');
});

test('exception banner stacks ABOVE state banner inside the thread handler', () => {
  // The thread-open hook calls injectExceptionBanner(...) FIRST, then
  // injectInvoiceBanner(...) — InboxSDK addNoticeBar prepends, so the
  // resulting visual order puts the most actionable signal nearest
  // the message body.
  const handlerBlock = SOURCE.match(
    /Inject thread-top banner[\s\S]+?injectInvoiceBanner\(threadView, item\);[\s\S]+?\}/,
  );
  assert.ok(handlerBlock, 'thread handler banner-injection block not found');
  const body = handlerBlock[0];
  const exceptionIdx = body.indexOf('injectExceptionBanner');
  const invoiceIdx = body.indexOf('injectInvoiceBanner');
  assert.ok(exceptionIdx > 0 && invoiceIdx > exceptionIdx,
    'injectExceptionBanner must be called before injectInvoiceBanner so the exception banner stacks above the state banner');
});

test('"View details" CTA routes through openItemInPipeline with thread_exception_banner source', () => {
  // The state banner uses thread_banner; the exception banner uses
  // thread_exception_banner. Both flow through openItemInPipeline so
  // sidebar focus and pipeline navigation stay consistent.
  assert.match(SOURCE, /openItemInPipeline\(item, 'thread_exception_banner'\)/);
});

test('severity config covers critical/high/medium/low + an unlabelled fallback', () => {
  const fnMatch = SOURCE.match(/function _exceptionSeverityConfig\(severity\)[\s\S]+?return \{[\s\S]+?\};\s*\}/);
  assert.ok(fnMatch, '_exceptionSeverityConfig helper missing');
  const body = fnMatch[0];
  assert.match(body, /'critical'/);
  assert.match(body, /'high'/);
  assert.match(body, /'medium'/);
  assert.match(body, /'low'/);
  // Fallback path — when severity is unrecognised but we know there's
  // an exception, we still render in a warning palette.
  assert.match(body, /label: 'Exception'/);
});
