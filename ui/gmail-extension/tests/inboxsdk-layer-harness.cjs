const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const acorn = require('acorn');

const SOURCE_PATH = path.resolve(__dirname, '../src/inboxsdk-layer.js');

function extractFunctionSource(source, name) {
  const ast = acorn.parse(source, {
    ecmaVersion: 'latest',
    sourceType: 'module',
    allowHashBang: true,
  });
  for (const node of ast.body) {
    if (node && node.type === 'FunctionDeclaration' && node.id && node.id.name === name) {
      return source.slice(node.start, node.end);
    }
  }
  throw new Error(`Function not found in inboxsdk-layer.js: ${name}`);
}

function loadInboxSdkLayerTestFns() {
  const source = fs.readFileSync(SOURCE_PATH, 'utf8');
  const orderedNames = [
    'getStateLabel',
    'formatTimestamp',
    'formatAmount',
    'escapeHtml',
    'trimText',
    'prettifyEventType',
    'normalizeAuditEventType',
    'isAuditReasonCode',
    'parseAuditReasonCodes',
    'getAuditReasonLabel',
    'formatAuditReasonText',
    'describeAgentEvent',
    'getAgentEventTimestamp',
    'parseJsonObject',
    'getAuditEventPayload',
    'getAuditEventTimestamp',
    'getIssueSummary',
    'getWorkAuditFallbackPresentation',
    'resolveOperatorAuditPresentation',
    'getWorkAuditPresentation',
    'humanizeSnakeText',
    'getBrowserFallbackStageMeta',
    'getBrowserFallbackAuditPresentation',
    'buildBrowserFallbackStatusSummary',
    'renderBrowserFallbackStatusBannerHtml',
    'classifyTimelineBucketFromState',
    'classifyAgentTimelineBucket',
    'classifyAuditTimelineBucket',
    'getAgentToolLabel',
    'describeBrowserContextEvent',
    'buildAgentTimelineEntries',
    'renderAgentTimelineGroups',
    'getItemActivityTimestampMs',
    'buildBatchAgentOpsSnapshot',
    'buildBatchOpsPreviewCard',
    'normalizeBatchOpsPolicyConfig',
    'applyBatchPolicyToGroup',
    'buildBatchRefreshIndicator',
    'buildBatchOpsRunResultCard',
    'buildFinanceSummarySharePreviewCard',
    'renderAgentSummaryCardHtml',
    'buildAgentIntentRecommendations',
    'getReasonSheetDefaults',
    'parseAgentIntentCommand',
  ];

  const functionSources = orderedNames.map((name) => extractFunctionSource(source, name));
  const scriptSource = `
    const STATE_LABELS = {
      received: 'Received',
      validated: 'Validated',
      needs_info: 'Needs info',
      needs_approval: 'Needs approval',
      approved: 'Approved',
      ready_to_post: 'Ready to post',
      posted_to_erp: 'Posted to ERP',
      closed: 'Closed',
      rejected: 'Rejected',
      failed_post: 'Failed post'
    };
    let batchOpsPolicyState = { maxItems: 5, amountThreshold: '', selectionPreset: 'queue_order' };
    ${functionSources.join('\n\n')}
    module.exports = {
      ${orderedNames.join(',\n      ')}
    };
  `;

  const context = {
    module: { exports: {} },
    exports: {},
    console,
    Date,
    JSON,
    Math,
    Intl,
    String,
    Number,
    Array,
    Object,
    Set,
    Map,
  };
  context.exports = context.module.exports;
  vm.runInNewContext(scriptSource, context, { filename: 'inboxsdk-layer-harness.vm.js' });
  return context.module.exports;
}

module.exports = {
  loadInboxSdkLayerTestFns,
  SOURCE_PATH,
};
