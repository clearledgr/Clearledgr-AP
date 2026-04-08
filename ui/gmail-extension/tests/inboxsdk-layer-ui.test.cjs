const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const { pathToFileURL } = require('node:url');

async function importModule(relativePath) {
  const absolute = path.resolve(__dirname, '..', relativePath);
  return import(`${pathToFileURL(absolute).href}?t=${Date.now()}`);
}

test('ghost pending_approval state is normalized to needs_approval semantics', async () => {
  const formatters = await importModule('src/utils/formatters.js');
  const workActions = await importModule('src/utils/work-actions.js');

  assert.equal(formatters.getStateLabel('pending_approval'), 'Needs approval');
  assert.equal(
    formatters.getIssueSummary({ state: 'pending_approval' }),
    'Waiting on approver decision'
  );
  assert.equal(workActions.normalizeWorkState('pending_approval'), 'needs_approval');
});

test('work-surface primary action map matches the current Gmail execution doctrine', async () => {
  const {
    canEscalateApproval,
    canReassignApproval,
    canRejectWorkItem,
    getPrimaryActionConfig,
    getWorkStateNotice,
    needsEntityRouting,
  } = await importModule('src/utils/work-actions.js');

  assert.deepEqual(getPrimaryActionConfig('received'), {
    id: 'request_approval',
    label: 'Request approval',
  });
  assert.deepEqual(getPrimaryActionConfig('validated'), {
    id: 'request_approval',
    label: 'Request approval',
  });
  assert.deepEqual(getPrimaryActionConfig('needs_info'), {
    id: 'prepare_info_request',
    label: 'Prepare info request',
  });
  assert.equal(
    getPrimaryActionConfig('needs_info', 'operator', 'invoice', {
      followup_next_action: 'await_vendor_response',
    }),
    null,
  );
  assert.equal(
    getPrimaryActionConfig('needs_info', 'operator', 'invoice', {
      followup_next_action: 'manual_vendor_escalation',
    }),
    null,
  );
  assert.equal(getPrimaryActionConfig('needs_approval'), null);
  assert.equal(getPrimaryActionConfig('ready_to_post'), null);
  assert.deepEqual(getPrimaryActionConfig('ready_to_post', 'operator', 'invoice', {
    erp_connector_available: true,
    erp_status: 'ready',
  }), {
    id: 'preview_erp_post',
    label: 'Preview ERP post',
  });
  assert.equal(getPrimaryActionConfig('failed_post'), null);
  assert.deepEqual(getPrimaryActionConfig('failed_post', 'operator', 'invoice', {
    erp_connector_available: true,
    erp_status: 'failed',
  }), {
    id: 'retry_erp_post',
    label: 'Retry ERP post',
  });
  assert.deepEqual(
    getPrimaryActionConfig('validated', 'operator', 'invoice', {
      entity_routing_status: 'needs_review',
      entity_candidates: [{ entity_code: 'US-01' }, { entity_code: 'GH-01' }],
    }),
    {
      id: 'resolve_entity_route',
      label: 'Resolve entity',
    },
  );
  assert.deepEqual(
    getPrimaryActionConfig('needs_approval', 'operator', 'invoice', {
      approval_followup: { escalation_due: true },
    }),
    {
      id: 'escalate_approval',
      label: 'Escalate approval',
    },
  );
  assert.deepEqual(
    getPrimaryActionConfig('needs_approval', 'operator', 'invoice', {
      approval_followup: { sla_breached: true },
    }),
    {
      id: 'nudge_approver',
      label: 'Nudge approver',
    },
  );
  assert.equal(getPrimaryActionConfig('approved'), null);
  assert.equal(getPrimaryActionConfig('rejected'), null);
  assert.equal(getPrimaryActionConfig('needs_approval', 'viewer'), null);
  assert.equal(
    getWorkStateNotice('needs_approval', 'invoice', {
      approval_followup: { pending_assignees: ['ap@clearledgr.com'] },
    }),
    'Waiting on ap@clearledgr.com. Clearledgr is monitoring this approval and will remind or escalate if it slips.',
  );
  assert.equal(
    getWorkStateNotice('needs_info', 'invoice', {
      followup_next_action: 'await_vendor_response',
    }),
    'Waiting for the vendor response. Clearledgr already prepared the follow-up.',
  );
  assert.equal(
    getWorkStateNotice('needs_info', 'invoice', {
      followup_next_action: 'manual_vendor_escalation',
    }),
    'Vendor follow-up reached the retry limit and now needs manual escalation.',
  );
  assert.equal(
    getWorkStateNotice('approved', 'invoice', {
      erp_connector_available: false,
      erp_status: 'not_connected',
    }),
    'ERP is not connected. Connect QuickBooks, Xero, NetSuite, or SAP before Clearledgr can post this invoice.',
  );
  assert.equal(canRejectWorkItem('needs_approval', 'viewer'), false);
  assert.equal(needsEntityRouting({ entity_routing_status: 'needs_review' }, 'validated'), true);
  assert.equal(canEscalateApproval({ approval_followup: { escalation_due: true } }, 'needs_approval'), true);
  assert.equal(canEscalateApproval({ approval_followup: { sla_breached: true } }, 'needs_approval'), false);
  assert.equal(canReassignApproval({}, 'needs_approval'), true);
});

test('route registry keeps left nav sparse while AppMenu exposes eligible routes', async () => {
  const {
    ROUTES,
    DEFAULT_ROUTE,
    getMenuNavRoutes,
    getNavEligibleRoutes,
    getVisibleNavRoutes,
    hideRoute,
    pinRoute,
  } = await importModule('src/routes/route-registry.js');
  const { getFallbackCapabilities } = await importModule('src/utils/capabilities.js');
  const routeIds = ROUTES.map((route) => route.id);
  const routeMap = new Map(ROUTES.map((route) => [route.id, route]));
  const operatorCapabilities = getFallbackCapabilities('operator');
  const viewerCapabilities = getFallbackCapabilities('viewer');
  const adminCapabilities = getFallbackCapabilities('admin');
  const defaultNavRouteIds = getVisibleNavRoutes({}, { capabilities: operatorCapabilities }).map((route) => route.id);
  const customizedNavRouteIds = getVisibleNavRoutes(
    pinRoute('clearledgr/vendors', hideRoute('clearledgr/connections', {}, { capabilities: operatorCapabilities }), { capabilities: operatorCapabilities }),
    { capabilities: operatorCapabilities },
  ).map((route) => route.id);
  const adminEligibleRouteIds = getNavEligibleRoutes({ capabilities: adminCapabilities }).map((route) => route.id);
  const adminDefaultNavRouteIds = getVisibleNavRoutes({}, { capabilities: adminCapabilities }).map((route) => route.id);
  const approverVisibleRouteIds = getVisibleNavRoutes({}, { capabilities: viewerCapabilities }).map((route) => route.id);
  const adminVisibleRouteIds = getVisibleNavRoutes(
    pinRoute('clearledgr/health', {}, { capabilities: adminCapabilities }),
    { capabilities: adminCapabilities },
  ).map((route) => route.id);
  const defaultMenuRouteIds = getMenuNavRoutes({}, { capabilities: operatorCapabilities }).map((route) => route.id);
  const approverMenuRouteIds = getMenuNavRoutes({}, { capabilities: viewerCapabilities }).map((route) => route.id);
  const adminMenuRouteIds = getMenuNavRoutes({}, { capabilities: adminCapabilities }).map((route) => route.id);
  const hiddenMenuRouteIds = getMenuNavRoutes(
    hideRoute('clearledgr/vendors', pinRoute('clearledgr/vendors', {}, { capabilities: operatorCapabilities }), { capabilities: operatorCapabilities }),
    { capabilities: operatorCapabilities },
  ).map((route) => route.id);

  assert.equal(DEFAULT_ROUTE, 'clearledgr/invoices');
  assert.ok(routeIds.includes('clearledgr/home'));
  assert.ok(routeIds.includes('clearledgr/invoices'));
  assert.ok(routeIds.includes('clearledgr/review'));
  assert.ok(routeIds.includes('clearledgr/activity'));
  assert.ok(routeIds.includes('clearledgr/connections'));
  assert.ok(routeIds.includes('clearledgr/settings'));
  assert.equal(routeIds.some((id) => /\bops\b/i.test(id)), false);
  assert.equal(routeIds.some((id) => /\bbatch\b/i.test(id)), false);
  assert.deepEqual(defaultNavRouteIds, [
    'clearledgr/invoices',
    'clearledgr/home',
  ]);
  assert.ok(customizedNavRouteIds.includes('clearledgr/vendors'));
  assert.equal(customizedNavRouteIds.includes('clearledgr/connections'), false);
  assert.equal(customizedNavRouteIds.includes('clearledgr/activity'), false);
  assert.equal(adminEligibleRouteIds.includes('clearledgr/health'), true);
  assert.deepEqual(adminDefaultNavRouteIds, [
    'clearledgr/invoices',
    'clearledgr/home',
  ]);
  assert.equal(defaultNavRouteIds.includes('clearledgr/health'), false);
  assert.equal(adminVisibleRouteIds.includes('clearledgr/health'), true);
  assert.deepEqual(approverVisibleRouteIds, [
    'clearledgr/invoices',
    'clearledgr/home',
  ]);
  assert.deepEqual(defaultMenuRouteIds, [
    'clearledgr/invoices',
    'clearledgr/home',
    'clearledgr/review',
    'clearledgr/upcoming',
    'clearledgr/connections',
    'clearledgr/activity',
    'clearledgr/vendors',
    'clearledgr/templates',
    'clearledgr/rules',
    'clearledgr/settings',
    'clearledgr/reconciliation',
    'clearledgr/reports',
  ]);
  assert.deepEqual(approverMenuRouteIds, defaultMenuRouteIds);
  assert.deepEqual(adminMenuRouteIds, defaultMenuRouteIds);
  assert.equal(hiddenMenuRouteIds.includes('clearledgr/vendors'), true);
  assert.equal(routeMap.get('clearledgr/connections').manageCapability, 'manage_connections');
  assert.equal(routeMap.get('clearledgr/rules').manageCapability, 'manage_rules');
  assert.equal(routeMap.get('clearledgr/home').hideTopbar, true);
  assert.equal(routeMap.get('clearledgr/settings').viewCapability, 'view_settings');
});

test('route icon mapper returns concrete icon assets for menu routes', async () => {
  const { ROUTES } = await importModule('src/routes/route-registry.js');
  const { getPipelineViewIconUrl, getRouteIconUrl } = await importModule('src/routes/route-icons.js');

  const iconUrls = ROUTES.map((route) => getRouteIconUrl(route));
  const decodeSvg = (url) => decodeURIComponent(String(url).split(',')[1] || '');
  const connectionsSvg = decodeSvg(getRouteIconUrl('connections'));
  const settingsSvg = decodeSvg(getRouteIconUrl('settings'));
  const templatesSvg = decodeSvg(getRouteIconUrl('templates'));

  assert.equal(iconUrls.every((url) => String(url).startsWith('data:image/svg+xml')), true);
  assert.equal(new Set(iconUrls).size >= 6, true);
  assert.equal(getPipelineViewIconUrl().startsWith('data:image/svg+xml'), true);
  assert.match(connectionsSvg, /scale\(1\.16\)/);
  assert.match(settingsSvg, /stroke-width="1\.84"/);
  assert.match(templatesSvg, /scale\(1\.13\)/);
});

test('pipeline blocker helpers prefer canonical backend blocker payloads', async () => {
  const { getPipelineBlockers, getPipelineBlockerKinds } = await importModule('src/routes/pipeline-views.js');

  assert.deepEqual(
    getPipelineBlockerKinds({
      state: 'received',
      exception_code: 'planner_failed',
      requires_field_review: true,
      confidence: 0.99,
    }),
    ['confidence'],
  );
  assert.deepEqual(
    getPipelineBlockerKinds({
      state: 'received',
      exception_code: 'po_missing_reference',
      requires_field_review: false,
      confidence: 0.99,
    }),
    ['exception', 'po'],
  );
  assert.deepEqual(
    getPipelineBlockerKinds({
      state: 'validated',
      entity_routing_status: 'needs_review',
    }),
    ['entity'],
  );
  assert.deepEqual(
    getPipelineBlockers({
      pipeline_blockers: [
        {
          kind: 'confidence',
          type: 'confidence_review',
          chip_label: 'Field review',
          title: 'Vendor needs review',
          detail: 'Vendor confidence is 94%, below the 95% review threshold.',
        },
        {
          kind: 'processing',
          type: 'processing_issue',
          chip_label: 'Processing issue',
          title: 'Processing issue',
          detail: 'Invoice processing needs retry or refresh before it can continue.',
        },
      ],
    }).map((blocker) => blocker.kind),
    ['confidence', 'processing'],
  );
  const erpSetupBlocker = getPipelineBlockers({
    state: 'ready_to_post',
    exception_code: 'erp_not_connected',
    erp_connector_available: false,
    erp_status: 'not_connected',
  })[0];
  assert.equal(erpSetupBlocker.chip_label, 'ERP not connected');
  assert.equal(erpSetupBlocker.detail, 'ERP is not connected for posting');
});

test('agent memory formatter normalizes the canonical cross-surface memory payload', async () => {
  const { getAgentMemoryView } = await importModule('src/utils/formatters.js');

  const view = getAgentMemoryView({
    state: 'needs_approval',
    agent_memory: {
      profile: {
        name: 'Clearledgr AP Agent',
        mission: 'Own the AP lane from intake through approval routing and ERP completion.',
        doctrine_version: 'ap_v1',
        risk_posture: 'bounded_autonomy',
        autonomy_level: 'assisted',
      },
      current_state: 'validated',
      status: 'pending_approval',
      uncertainties: {
        reason_codes: ['vendor_unscored', 'blocking_source_conflicts'],
        confidence_blockers: [{ field: 'amount' }],
      },
      next_action: {
        type: 'await_approval',
        label: 'Wait for approval decision',
        owner: 'approver',
      },
      summary: {
        reason: 'Awaiting approval response.',
      },
    },
  });

  assert.equal(view.name, 'Clearledgr AP Agent');
  assert.equal(view.autonomyLabel, 'Assisted');
  assert.equal(view.currentStateLabel, 'Validated');
  assert.equal(view.statusLabel, 'Needs approval');
  assert.equal(view.nextActionLabel, 'Waiting for approval');
  assert.equal(view.nextActionOwnerLabel, 'Approver');
  assert.equal(view.beliefReason, 'Awaiting approval response.');
  assert.deepEqual(view.reasonCodes, [
    'Vendor details need review',
    'Email and attachment do not match',
  ]);
  assert.equal(view.highlights.includes('1 field check still needs confirmation'), true);
});

test('confidence field-review blockers expose current value, source, and confidence context', async () => {
  const { getFieldReviewBlockers } = await importModule('src/utils/formatters.js');

  const blockers = getFieldReviewBlockers({
    currency: 'USD',
    amount: 0,
    field_provenance: {
      amount: {
        source: 'attachment',
        value: 0,
        candidates: {
          email: 38.46,
          attachment: 0,
        },
      },
    },
    field_evidence: {
      amount: {
        source: 'attachment',
        selected_value: 0,
        email_value: 38.46,
        attachment_value: 0,
      },
    },
    confidence_blockers: [
      {
        field: 'amount',
        confidence: 0.61,
        confidence_pct: 61,
        threshold_pct: 95,
      },
    ],
  });

  assert.equal(blockers.length, 1);
  assert.equal(blockers[0].kind, 'confidence');
  assert.equal(blockers[0].current_value_display, 'USD 0.00');
  assert.equal(blockers[0].current_source_label, 'Invoice attachment');
  assert.equal(blockers[0].email_value_display, 'USD 38.46');
  assert.equal(blockers[0].attachment_value_display, 'USD 0.00');
  assert.equal(blockers[0].confidence_pct, 61);
  assert.equal(blockers[0].threshold_pct, 95);
  assert.equal(
    blockers[0].paused_reason,
    'Review amount before this invoice moves forward.',
  );
  assert.equal(
    blockers[0].winner_reason,
    'Clearledgr read USD 0.00 from the invoice attachment. Because amount is a critical field, a person needs to confirm it before approval continues.',
  );
});

test('admin bootstrap adapter preserves backend current user role instead of hardcoding admin', async () => {
  const { createWorkspaceShellApi } = await importModule('src/routes/workspace-shell-api.js');
  const calls = [];
  const queueManager = {
    runtimeConfig: {
      organizationId: 'org-eu-1',
      backendUrl: 'https://api.clearledgr.test',
    },
    async backendFetch(url) {
      calls.push(url);
      if (url.endsWith('/api/workspace/bootstrap?organization_id=org-eu-1')) {
        return {
          ok: true,
          status: 200,
          async json() {
            return {
              dashboard: { recent_activity: [{ title: 'Approval sent' }] },
              integrations: [{ name: 'gmail', connected: true }],
              organization: { id: 'org-eu-1', name: 'Clearledgr Europe' },
              health: { status: 'ok' },
              subscription: { plan: 'beta' },
              required_actions: ['connect_erp'],
              current_user: {
                role: 'operator',
                email: 'ops@clearledgr.com',
                preferences: {
                  gmail_extension: {
                    pipeline_views: {
                      activeSliceId: 'waiting_on_approval',
                    },
                  },
                },
              },
              capabilities: {
                view_connections: true,
                manage_connections: false,
              },
            };
          },
        };
      }
      if (url.endsWith('/api/workspace/policies/ap?organization_id=org-eu-1')) {
        return {
          ok: true,
          status: 200,
          async json() {
            return { policy: { config_json: { approval_threshold: 500 } } };
          },
        };
      }
      if (url.endsWith('/api/workspace/team/invites?organization_id=org-eu-1')) {
        return {
          ok: true,
          status: 200,
          async json() {
            return [];
          },
        };
      }
      throw new Error(`unexpected url: ${url}`);
    },
  };

  const api = createWorkspaceShellApi(queueManager);
  const bootstrap = await api.bootstrapWorkspaceShellData();

  assert.deepEqual(calls, [
    'https://api.clearledgr.test/api/workspace/bootstrap?organization_id=org-eu-1',
    'https://api.clearledgr.test/api/workspace/policies/ap?organization_id=org-eu-1',
    'https://api.clearledgr.test/api/workspace/team/invites?organization_id=org-eu-1',
  ]);
  assert.equal(bootstrap.current_user.role, 'operator');
  assert.equal(bootstrap.current_user.email, 'ops@clearledgr.com');
  assert.equal(bootstrap.capabilities.view_connections, true);
  assert.equal(bootstrap.capabilities.manage_connections, false);
  assert.equal(
    bootstrap.current_user.preferences.gmail_extension.pipeline_views.activeSliceId,
    'waiting_on_approval',
  );
  assert.deepEqual(bootstrap.recentActivity, [{ title: 'Approval sent' }]);
  assert.deepEqual(bootstrap.required_actions, ['connect_erp']);
});

test('bootstrap adapter preserves last known admin role when workspace bootstrap is temporarily unavailable', async () => {
  const { createWorkspaceShellApi } = await importModule('src/routes/workspace-shell-api.js');
  const queueManager = {
    currentUserRole: 'owner',
    runtimeConfig: {
      organizationId: 'default',
      backendUrl: 'https://api.clearledgr.test',
      userEmail: 'mo@clearledgr.com',
    },
    async backendFetch(url) {
      if (url.endsWith('/api/workspace/bootstrap?organization_id=default')) {
        return {
          ok: false,
          status: 503,
          async text() {
            return 'service unavailable';
          },
        };
      }
      return {
        ok: true,
        status: 200,
        async json() {
          return {};
        },
      };
    },
  };

  const api = createWorkspaceShellApi(queueManager);
  const bootstrap = await api.bootstrapWorkspaceShellData();

  assert.equal(bootstrap.current_user.role, 'owner');
  assert.equal(bootstrap.current_user.email, 'mo@clearledgr.com');
  assert.equal(bootstrap.capabilities.view_connections, true);
  assert.equal(bootstrap.capabilities.manage_connections, true);
  assert.equal(bootstrap.capabilities.manage_admin_pages, true);
});

test('routed setup pages request fresh OAuth URLs instead of bootstrap auth fields', () => {
  const homeSource = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/routes/pages/HomePage.js'),
    'utf8',
  );
  const connectionsSource = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/routes/pages/ConnectionsPage.js'),
    'utf8',
  );

  assert.equal(homeSource.includes("bootstrap?.gmail_auth_url"), false);
  assert.equal(homeSource.includes("bootstrap?.slack_auth_url"), false);
  assert.equal(homeSource.includes('/api/workspace/integrations/gmail/connect/start'), true);
  assert.equal(homeSource.includes('/api/workspace/integrations/slack/install/start'), false);
  assert.equal(connectionsSource.includes("bootstrap?.gmail_auth_url"), false);
  assert.equal(connectionsSource.includes('/api/workspace/integrations/gmail/connect/start'), true);
  assert.equal(connectionsSource.includes('/api/workspace/integrations/slack/install/start'), true);
  assert.equal(connectionsSource.includes('/api/workspace/integrations/erp/connect/start'), true);
});

test('pipeline blocker summary reads canonical backend blocker payload', () => {
  const pipelineSource = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/routes/pages/PipelinePage.js'),
    'utf8',
  );

  assert.equal(pipelineSource.includes('function PipelineBlockerSummary({ item, compact = false }) {'), true);
  assert.equal(pipelineSource.includes('const blockers = getPipelineBlockers(item);'), true);
  assert.equal(pipelineSource.includes('const secondaryDetail = extraCount > 0'), true);
});

test('oauth completion rehydrates bootstrap so app menu access can refresh', () => {
  const source = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/inboxsdk-layer.js'),
    'utf8',
  );

  assert.equal(source.includes('void getBootstrap();'), true);
  assert.equal(source.includes('routeAccessResolved = true;'), true);
  assert.equal(source.includes('currentRouteAccess = { capabilities: getCapabilities({}) };'), true);
  assert.equal(source.includes("const routeOptions = { capabilities: getCapabilities(bootstrap) };"), true);
});

test('invoice detail page stays on the canonical AP action contract', () => {
  const source = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/routes/pages/InvoiceDetailPage.js'),
    'utf8',
  );
  const routeSource = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/routes/route-styles.js'),
    'utf8',
  );
  const routerSource = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/inboxsdk-layer.js'),
    'utf8',
  );

  assert.equal(source.includes('/extension/approve-and-post'), false);
  assert.equal(source.includes("getPrimaryActionConfig(state, actorRole, documentType, item)"), true);
  assert.equal(source.includes("auditData?.events"), true);
  assert.equal(source.includes("executeIntent(api, orgId, 'post_to_erp'"), true);
  assert.equal(source.includes("executeIntent(api, orgId, 'request_approval'"), true);
  assert.equal(source.includes('const agentView = useMemo(() => getAgentMemoryView(item), [item]);'), true);
  assert.equal(source.includes('What Clearledgr sees'), true);
  assert.equal(source.includes('Agent memory'), false);
  assert.equal(source.includes('Doctrine'), false);
  assert.equal(source.includes('Risk posture'), false);
  assert.equal(source.includes('Ready-to-send replies'), true);
  assert.equal(source.includes('Evidence attached'), true);
  assert.equal(source.includes('prettifyEventType(eventType)'), false);
  assert.equal(source.includes('partitionAuditEvents(auditEvents)'), true);
  assert.equal(source.includes('Record history'), true);
  assert.equal(source.includes('Background activity'), true);
  assert.equal(source.includes('Credits and payments'), true);
  assert.equal(source.includes('Check these fields'), true);
  assert.equal(source.includes('getOperatorOverrideCopy(state, item, documentType)'), true);
  assert.equal(source.includes('route-operator-overrides'), true);
  assert.equal(source.includes('Email says'), true);
  assert.equal(source.includes('Attachment says'), true);
  assert.equal(source.includes("api(`/api/ap/items/${encodeURIComponent(itemId)}?organization_id=${encodeURIComponent(orgId)}`, { silent: true })"), true);
  assert.equal(source.includes("api(`/api/ap/items/${encodeURIComponent(itemId)}/audit?organization_id=${encodeURIComponent(orgId)}`, { silent: true })"), true);
  assert.equal(source.includes("api(`/api/ap/items/${encodeURIComponent(itemId)}/context?organization_id=${encodeURIComponent(orgId)}`, { silent: true })"), true);
  assert.equal(routerSource.includes("container.className = 'cl-route cl-route-record-detail';"), true);
  assert.equal(routeSource.includes('.cl-route.cl-route-record-detail .record-detail-shell'), true);
  assert.equal(routeSource.includes('.cl-route.cl-route-record-detail .record-detail-side'), true);
});

test('review workbench route is mounted in Gmail and exposes field resolution actions', () => {
  const routerSource = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/inboxsdk-layer.js'),
    'utf8',
  );
  const reviewSource = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/routes/pages/ReviewPage.js'),
    'utf8',
  );

  assert.equal(routerSource.includes("import ReviewPage from './routes/pages/ReviewPage.js';"), true);
  assert.equal(routerSource.includes("'clearledgr/review': ReviewPage"), true);
  assert.equal(reviewSource.includes('Review queue'), true);
  assert.equal(reviewSource.includes('/field-review/resolve'), true);
  assert.equal(reviewSource.includes('/field-review/bulk-resolve'), true);
  assert.equal(reviewSource.includes('/non-invoice/resolve'), true);
  assert.equal(reviewSource.includes('Keyboard: J/K move'), false);
  assert.equal(reviewSource.includes('Field checks'), true);
  assert.equal(reviewSource.includes('Bulk actions'), true);
  assert.equal(reviewSource.includes('Find a record in this queue by vendor, reference, sender, or exception.'), true);
});

test('pipeline page stays list-first and supports bulk routing without keyboard mode', () => {
  const source = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/routes/pages/PipelinePage.js'),
    'utf8',
  );

  assert.equal(source.includes("/extension/route-low-risk-approval"), true);
  assert.equal(source.includes("if (state !== 'validated') return false;"), true);
  assert.equal(source.includes('Select visible'), true);
  assert.equal(source.includes('Route selected'), true);
  assert.equal(source.includes('First issue:'), true);
  assert.equal(source.includes('Only validated invoices can be routed for approval.'), true);
  assert.equal(source.includes('Filter, route, and reopen records without leaving Gmail.'), true);
  assert.equal(source.includes('Keyboard: J/K move'), false);
  assert.equal(source.includes('kpi-row'), false);
  assert.equal(source.includes("viewMode === 'cards'"), false);
  assert.equal(source.includes("const [selectedIds, setSelectedIds] = useState([]);"), true);
});

test('invoice detail page exposes explicit non-invoice review actions', () => {
  const source = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/routes/pages/InvoiceDetailPage.js'),
    'utf8',
  );

  assert.equal(source.includes('/non-invoice/resolve'), true);
  assert.equal(source.includes('Apply to invoice'), true);
  assert.equal(source.includes('Link to payment'), true);
  assert.equal(source.includes('Record vendor credit'), true);
});

test('home page queue shortcuts and saved views stay user and org scoped', () => {
  const source = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/routes/pages/HomePage.js'),
    'utf8',
  );
  const inboxLayerSource = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/inboxsdk-layer.js'),
    'utf8',
  );
  const routeStyles = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/routes/route-styles.js'),
    'utf8',
  );

  assert.equal(source.includes('const adminAccess = hasAdminAccess(bootstrap);'), true);
  assert.equal(source.includes('const pipelineScope = { orgId, userEmail };'), true);
  assert.equal(source.includes('getBootstrappedPipelinePreferences(bootstrap)'), true);
  assert.equal(source.includes('readPipelinePreferences(pipelineScope)'), true);
  assert.equal(source.includes('getPinnedPipelineViews(pipelinePrefs)'), true);
  assert.equal(source.includes('getStarterPipelineViews(pipelinePrefs)'), true);
  assert.equal(source.includes('writePipelinePreferences(pipelineScope, view.snapshot)'), true);
  assert.equal(source.includes("activatePipelineSlice(pipelineScope, sliceId)"), true);
  assert.equal(source.includes("/api/ap/audit/recent?organization_id="), true);
  assert.equal(source.includes('Welcome to Clearledgr'), true);
  assert.equal(source.includes("import { getRouteIconUrl } from '../route-icons.js';"), true);
  assert.equal(source.includes('Connections'), true);
  assert.equal(source.includes('Team'), true);
  assert.equal(source.includes('Billing'), true);
  assert.equal(source.includes('home-utility-icon-button'), true);
  assert.equal(source.includes('home-utility-primary'), true);
  assert.equal(source.includes('Quick access'), true);
  assert.equal(source.includes('Recent work'), true);
  assert.equal(source.includes('Recently posted'), true);
  assert.equal(source.includes('Upcoming tasks'), true);
  assert.equal(source.includes('class="home-quick-row"'), true);
  assert.equal(source.includes('class="home-main-grid"'), true);
  assert.equal(source.includes('class="home-panel-grid"'), true);
  assert.equal(source.includes('Saved views and slices'), true);
  assert.equal(source.includes('Highlights'), true);
  assert.equal(source.includes('Choose what stays on Home'), false);
  assert.equal(routeStyles.includes('box-sizing: border-box;'), true);
  assert.equal(routeStyles.includes('max-width: none;'), true);
  assert.equal(routeStyles.includes('.cl-route .home-header-shell {'), true);
  assert.equal(routeStyles.includes('.cl-route .home-utility-rail {'), true);
  assert.equal(routeStyles.includes('.cl-route .home-utility-strip {'), true);
  assert.equal(routeStyles.includes('.cl-route .home-utility-icon {'), true);
  assert.equal(routeStyles.includes('.cl-route .home-utility-icon-button {'), true);
  assert.equal(routeStyles.includes('.cl-route .home-utility-primary {'), true);
  assert.equal(routeStyles.includes('.cl-route input[type="checkbox"],'), true);
  assert.equal(routeStyles.includes('.cl-route .home-quick-row {'), true);
  assert.equal(routeStyles.includes('.cl-route .home-main-grid {'), true);
  assert.equal(routeStyles.includes('.cl-route .home-panel-grid {'), true);
  assert.equal(routeStyles.includes('.cl-route .home-status-pill {'), true);
  assert.equal(routeStyles.includes('grid-template-columns: minmax(0, 1.42fr) minmax(336px, 0.78fr);'), true);
  assert.equal(inboxLayerSource.includes("routeEl.style.maxWidth = 'none';"), true);
  assert.equal(inboxLayerSource.includes("routeEl.style.width = '100%';"), true);
});

test('pipeline page syncs saved views through the authenticated user preferences contract', () => {
  const source = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/routes/pages/PipelinePage.js'),
    'utf8',
  );

  assert.equal(source.includes("api('/api/workspace/user/preferences'"), true);
  assert.equal(source.includes('buildPipelinePreferencePatch(normalized)'), true);
  assert.equal(source.includes('getBootstrappedPipelinePreferences(bootstrap)'), true);
  assert.equal(source.includes('getStarterPipelineViews(viewPrefs)'), true);
  assert.equal(source.includes('Update active view'), true);
  assert.equal(source.includes('PipelineBlockerSummary'), true);
  assert.equal(source.includes("const pipelineBlockers = getPipelineBlockers(item);"), true);
  assert.equal(source.includes('processing'), true);
});

test('thread card stays compact, capped, and free of dashboard/debug clutter', () => {
  const source = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/components/SidebarApp.js'),
    'utf8',
  );

  assert.equal(source.includes('primaryLimit: 4'), true);
  assert.equal(source.includes('secondaryLimit: 2'), true);
  assert.equal(source.includes('Evidence checklist'), true);
  assert.equal(source.includes('#: ${invoiceNumber}'), true);
  assert.equal(source.includes('Due: ${dueDate}'), true);
  assert.equal(source.includes('Check these fields'), true);
  assert.equal(source.includes('getAgentMemoryView(item)'), true);
  assert.equal(source.includes('Before Clearledgr continues'), true);
  assert.equal(source.includes('What happens next'), true);
  assert.equal(source.includes('Next step'), true);
  assert.equal(source.includes('Needs attention'), true);
  assert.equal(source.includes('Previous record'), true);
  assert.equal(source.includes('Next record'), true);
  assert.equal(source.includes('cl-navigator'), false);
  assert.equal(source.includes('Email says'), true);
  assert.equal(source.includes('View audit'), true);
  assert.equal(source.includes('Key history'), true);
  assert.equal(source.includes('Background activity'), true);
  assert.equal(source.includes('MiniBarChart'), false);
  assert.equal(source.includes('HorizontalBar'), false);
  assert.equal(source.includes('SnapshotCard'), false);
  assert.equal(source.includes('prompt('), false);
  assert.equal(source.includes('confirm('), false);
  assert.equal(source.includes('window.open('), false);
  assert.equal(source.includes('Keyboard: J/K move'), false);
});

test('gmail sidebar turns empty threads into create-or-link finance record flows', () => {
  const source = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/components/SidebarApp.js'),
    'utf8',
  );

  assert.equal(source.includes('Create record from email'), true);
  assert.equal(source.includes('Find record'), true);
  assert.equal(source.includes('searchRecordCandidates'), true);
  assert.equal(source.includes('linkCurrentThreadToItem'), true);
  assert.equal(source.includes('Related records'), true);
  assert.equal(source.includes('Files and evidence'), true);
  assert.equal(source.includes('Comments'), true);
  assert.equal(source.includes('Edit record'), true);
  assert.equal(source.includes('Tasks'), true);
  assert.equal(source.includes('Notes'), true);
});

test('thread handler refreshes the canonical thread item so new evidence fields replace stale queue rows', () => {
  const source = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/inboxsdk-layer.js'),
    'utf8',
  );

  assert.equal(source.includes('Always refresh the canonical item for the open thread'), true);
  assert.equal(source.includes('Lookup stays read-only; thread'), true);
  assert.equal(source.includes('queueManager.upsertQueueItem(item);'), true);
  assert.equal(source.includes('queueManager.emitQueueUpdated();'), true);
  assert.equal(source.includes('if (threadId && queueManager) {'), true);
  assert.equal(source.includes("/extension/by-thread/${encodeURIComponent(threadId)}/recover"), true);
  assert.equal(source.includes("{ method: 'POST' }"), true);
});

test('compose drafts keep finance record context attached inside Gmail', () => {
  const source = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/inboxsdk-layer.js'),
    'utf8',
  );
  const queueSource = fs.readFileSync(
    path.resolve(__dirname, '..', 'queue-manager.js'),
    'utf8',
  );
  const detailSource = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/routes/pages/InvoiceDetailPage.js'),
    'utf8',
  );

  assert.equal(source.includes('recordContext: prefill?.recordContext || null'), true);
  assert.equal(source.includes('Clearledgr: linked finance record'), true);
  assert.equal(source.includes('Create finance record'), true);
  assert.equal(source.includes('queueManager?.lookupComposeRecord'), true);
  assert.equal(queueSource.includes('/api/ap/items/compose/lookup'), true);
  assert.equal(queueSource.includes('/api/ap/items/compose/create'), true);
  assert.equal(queueSource.includes('/compose-link'), true);
  assert.equal(source.includes("navigateInboxRoute('clearledgr/invoice/:id', sdk, { id: recordContext.apItemId })"), true);
  assert.equal(detailSource.includes('prefill.recordContext = {'), true);
  assert.equal(detailSource.includes('apItemId: item.id,'), true);
});

test('gmail auth stays explicit and never opens OAuth during startup bootstrap', () => {
  const inboxSource = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/inboxsdk-layer.js'),
    'utf8',
  );
  const sidebarSource = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/components/SidebarApp.js'),
    'utf8',
  );
  const homeSource = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/routes/pages/HomePage.js'),
    'utf8',
  );
  const queueSource = fs.readFileSync(
    path.resolve(__dirname, '..', 'queue-manager.js'),
    'utf8',
  );

  assert.equal(inboxSource.includes('void getBootstrap();'), true);
  assert.equal(inboxSource.includes('oauthBridge.startOAuth('), false);
  assert.equal(inboxSource.includes('authorizeGmailNow('), false);
  assert.equal(queueSource.includes('Only explicit user actions should open interactive OAuth windows.'), true);
  assert.equal(queueSource.includes('Automatic retries (e.g. 401 recovery) must stay non-interactive.'), true);
  assert.equal(sidebarSource.includes('authorizeGmailNow?.()'), true);
  assert.equal(sidebarSource.includes('Connect Gmail'), true);
  assert.equal(homeSource.includes('/api/workspace/integrations/gmail/connect/start'), true);
  assert.equal(homeSource.includes("oauthBridge.startOAuth(payload.auth_url, 'gmail');"), true);
});

test('sidebar audit rendering falls back to safe generic copy instead of raw event names', () => {
  const source = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/components/SidebarApp.js'),
    'utf8',
  );

  assert.equal(source.includes('prettifyEventType(eventType)'), false);
  assert.equal(source.includes('partitionAuditEvents(events'), true);
  assert.equal(source.includes('Key history'), true);
  assert.equal(source.includes('Background activity'), true);
});

test('thread and detail surfaces can reopen the current AP item in pipeline context', () => {
  const sidebarSource = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/components/SidebarApp.js'),
    'utf8',
  );
  const detailSource = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/routes/pages/InvoiceDetailPage.js'),
    'utf8',
  );

  assert.equal(sidebarSource.includes('focusPipelineItem(pipelineScope, item, \'thread\')'), true);
  assert.equal(sidebarSource.includes('Open in invoices'), true);
  assert.equal(detailSource.includes('focusPipelineItem(pipelineScope, item, \'detail\')'), true);
  assert.equal(detailSource.includes('const openInPipeline = useCallback(() => {'), true);
});

test('gmail route gating distinguishes ops access from admin access and removes stale thread action paths', () => {
  const inboxSource = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/inboxsdk-layer.js'),
    'utf8',
  );

  assert.equal(inboxSource.includes('getCapabilities(bootstrap)'), true);
  assert.equal(inboxSource.includes('canViewRoute(route, routeOptions)'), true);
  assert.equal(inboxSource.includes('const menuRoutes = getMenuNavRoutes(routePreferences, routeOptions);'), true);
  assert.equal(inboxSource.includes('let routeAccessResolved = true;'), true);
  assert.equal(inboxSource.includes('if (!routeAccessResolved) return;'), true);
  assert.equal(inboxSource.includes('iconUrl: getRouteIconUrl(route)'), true);
  assert.equal(inboxSource.includes('iconUrl: route.iconUrl'), true);
  assert.equal(inboxSource.includes('queueManager.submitForApproval'), false);
  assert.equal(inboxSource.includes('title: \'Open in invoices\''), true);
});

test('app menu collapses the panel top slot instead of rendering a second Clearledgr logo', () => {
  const inboxSource = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/inboxsdk-layer.js'),
    'utf8',
  );

  assert.equal(inboxSource.includes('function injectAppMenuPanelStyles()'), true);
  assert.equal(inboxSource.includes("className: 'cl-appmenu-panel'"), true);
  assert.equal(inboxSource.includes('.cl-appmenu-panel .aic {'), true);
  assert.equal(inboxSource.includes('display: none;'), true);
  assert.equal(inboxSource.includes('primaryButton:'), false);
});

test('secondary Gmail pages stay lightweight and avoid raw admin/dashboard surfaces', () => {
  const activitySource = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/routes/pages/ActivityPage.js'),
    'utf8',
  );
  const connectionsSource = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/routes/pages/ConnectionsPage.js'),
    'utf8',
  );
  const companySource = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/routes/pages/CompanyPage.js'),
    'utf8',
  );
  const healthSource = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/routes/pages/HealthPage.js'),
    'utf8',
  );
  const rulesSource = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/routes/pages/RulesPage.js'),
    'utf8',
  );
  const planSource = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/routes/pages/PlanPage.js'),
    'utf8',
  );
  const settingsSource = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/routes/pages/SettingsPage.js'),
    'utf8',
  );
  const reconSource = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/routes/pages/ReconciliationPage.js'),
    'utf8',
  );
  const teamSource = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/routes/pages/TeamPage.js'),
    'utf8',
  );
  const upcomingSource = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/routes/pages/UpcomingPage.js'),
    'utf8',
  );
  const templatesSource = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/routes/pages/TemplatesPage.js'),
    'utf8',
  );
  const reportsSource = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/routes/pages/ReportsPage.js'),
    'utf8',
  );
  const inboxLayerSource = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/inboxsdk-layer.js'),
    'utf8',
  );
  const vendorsSource = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/routes/pages/VendorsPage.js'),
    'utf8',
  );
  const vendorDetailSource = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/routes/pages/VendorDetailPage.js'),
    'utf8',
  );

  assert.equal(activitySource.includes('MiniBarChart'), false);
  assert.equal(activitySource.includes('HorizontalBar'), false);
  assert.equal(activitySource.includes('SnapshotCard'), false);
  assert.equal(activitySource.includes('kpi-row'), false);
  assert.equal(connectionsSource.includes('<table class="table">'), false);
  assert.equal(companySource.includes('cl-org-json'), false);
  assert.equal(companySource.includes('cl-entity-code-0'), true);
  assert.equal(companySource.includes('cl-entity-rule-entity-code-0'), true);
  assert.equal(healthSource.includes('<table class="table">'), false);
  assert.equal(rulesSource.includes('cl-policy-json'), false);
  assert.equal(rulesSource.includes("import { useEffect, useMemo, useState } from 'preact/hooks';"), true);
  assert.equal(rulesSource.includes('cl-policy-confidence'), true);
  assert.equal(rulesSource.includes("if (value === 'ap_business_v1') return 'Default AP approval policy';"), true);
  assert.equal(rulesSource.includes('Approval routing'), true);
  assert.equal(rulesSource.includes('Add routing rule'), true);
  assert.equal(rulesSource.includes('Slack channel'), true);
  assert.equal(rulesSource.includes('/api/workspace/team/approvers'), true);
  assert.equal(rulesSource.includes('Select workspace approver'), true);
  assert.equal(rulesSource.includes('approver_targets'), true);
  assert.equal(rulesSource.includes('What this policy includes'), true);
  assert.equal(rulesSource.includes('effective_policies'), true);
  assert.equal(rulesSource.includes('Approval delegation'), true);
  assert.equal(rulesSource.includes('/api/workspace/delegation-rules'), true);
  assert.equal(rulesSource.includes('/settings/${encodeURIComponent(orgId)}/approval-thresholds'), true);
  assert.equal(rulesSource.includes('class="rules-workspace-grid"'), true);
  assert.equal(planSource.includes('Subscription and billing'), true);
  assert.equal(planSource.includes(".replace(/\\bApi\\b/g, 'API')"), true);
  assert.equal(planSource.includes('Usage against plan limits'), true);
  assert.equal(planSource.includes('Choose a plan'), true);
  assert.equal(planSource.includes("navigate('clearledgr/settings')"), true);
  assert.equal(planSource.includes("changePlan('trial')"), true);
  assert.equal(settingsSource.includes('routeId, navigate }'), true);
  assert.equal(settingsSource.includes("navigate('clearledgr/plan')"), true);
  assert.equal(inboxLayerSource.includes("'clearledgr/plan': PlanPage"), true);
  assert.equal(inboxLayerSource.includes("<h2>Billing</h2><p>Plan, usage, and workspace limits.</p>"), true);
  assert.equal(reconSource.includes('Use this page when you want to test or run reconciliation work from a spreadsheet.'), true);
  assert.equal(teamSource.includes('<table class="table">'), false);
  assert.equal(upcomingSource.includes('See what needs attention next'), true);
  assert.equal(templatesSource.includes('Syncfusion'), false);
  assert.equal(reportsSource.includes('Get a quick view of queue health, spend, coverage, and duplicate risk, then jump back into the work.'), true);
  assert.equal(reportsSource.includes('Proof scorecard'), true);
  assert.equal(reportsSource.includes('posting_success_rate_pct'), true);
  assert.equal(reportsSource.includes('recovery_success_rate_pct'), true);
  assert.equal(vendorsSource.includes('kpi-row'), false);
  assert.equal(vendorsSource.includes('Review issues'), true);
  assert.equal(vendorsSource.includes('top_exception_codes'), true);
  assert.equal(vendorDetailSource.includes('Recurring exception codes'), false);
  assert.equal(vendorDetailSource.includes('Common workflow states'), true);
  assert.equal(vendorDetailSource.includes('Open issues and follow-up'), true);
  assert.equal(vendorDetailSource.includes('Recurring issues'), true);
  assert.equal(vendorDetailSource.includes('getExceptionLabel('), true);
  assert.equal(vendorDetailSource.includes('Exception ${String(item.exception_code)'), false);
  assert.equal(vendorDetailSource.includes("return String(item?.ap_item_id || item?.id || '').trim();"), true);
  assert.equal(vendorDetailSource.includes('navigateToRecordDetail(navigate, recordId);'), true);
});

test('full-page Gmail routes collapse the thread sidebar rail while active', () => {
  const source = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/inboxsdk-layer.js'),
    'utf8',
  );

  assert.equal(source.includes('async function setSidebarPanelOpen(shouldOpen)'), true);
  assert.equal(source.includes('void setSidebarPanelOpen(false);'), true);
  assert.equal(source.includes("if (!hash.includes('clearledgr/')) {"), true);
  assert.equal(source.includes('void setSidebarPanelOpen(true);'), true);
});

test('full-page Gmail routes claim a Clearledgr-specific browser title instead of leaving Inbox chrome behind', () => {
  const source = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/inboxsdk-layer.js'),
    'utf8',
  );

  assert.equal(source.includes('function buildRouteDocumentTitle(pageTitle = \'\') {'), true);
  assert.equal(source.includes('function claimRouteDocumentTitle(pageTitle = \'\') {'), true);
  assert.equal(source.includes("document.title = nextTitle;"), true);
  assert.equal(source.includes("const releaseDocumentTitle = claimRouteDocumentTitle(route.title);"), true);
  assert.equal(source.includes("const releaseDocumentTitle = claimRouteDocumentTitle('Record Detail');"), true);
  assert.equal(source.includes("const releaseDocumentTitle = claimRouteDocumentTitle('Vendor Detail');"), true);
});

test('app menu panel exposes a Clearledgr start-work CTA and a saved views section', () => {
  const source = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/inboxsdk-layer.js'),
    'utf8',
  );

  assert.equal(source.includes("createSavedPipelineView"), true);
  assert.equal(source.includes('<span class="cl-appmenu-panel-cta-copy">New record</span>'), true);
  assert.equal(source.includes("function renderAppMenuPanelChrome({ workspaceRoutes = [], pinnedViews = [], configurationRoutes = [], libraryRoutes = [] } = {}) {"), true);
  assert.equal(source.includes("renderAppMenuPanelChrome({"), true);
  assert.equal(source.includes("renderSection('Workspace', workspaceRoutes.map((route) => ({"), true);
  assert.equal(source.includes("configurationRoutes: menuRoutes.filter((route) => APPMENU_CONFIGURATION_ROUTE_IDS.has(route.id)),"), true);
  assert.equal(source.includes("libraryRoutes: menuRoutes.filter((route) => APPMENU_LIBRARY_ROUTE_IDS.has(route.id)),"), true);
  assert.equal(source.includes("renderSection('Configurations', configurationRoutes.map((route) => ({"), true);
  assert.equal(source.includes("renderSection('Templates', libraryRoutes.map((route) => ({"), true);
  assert.equal(source.includes("iconImage.src = row.iconUrl;"), true);
  assert.equal(source.includes("const currentHash = normalizeClearledgrHash(window.location.hash) || lastActiveClearledgrRoute;"), true);
  assert.equal(source.includes("trailingActionAriaLabel: 'Save current view'"), true);
  assert.equal(source.includes("Save current view"), true);
  assert.equal(source.includes('showToast(`Saved "${name}" to Views.`, \'success\');'), true);
  assert.equal(source.includes('Use the right-hand Clearledgr panel to create a record from this email or link it to an existing record.'), true);
});

test('app menu active state refreshes when Clearledgr routes change', () => {
  const source = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/inboxsdk-layer.js'),
    'utf8',
  );

  assert.equal(source.includes("rememberActiveClearledgrRoute(route.id);\n      rebuildMenuNavigation();"), true);
  assert.equal(source.includes("rememberActiveClearledgrRoute('clearledgr/invoice/:id', { id: rawId });\n    rebuildMenuNavigation();"), true);
  assert.equal(source.includes("rememberActiveClearledgrRoute('clearledgr/vendor/:name', { name: rawName });\n    rebuildMenuNavigation();"), true);
  assert.equal(source.includes("lastActiveClearledgrRoute = currentClearledgrHash;"), true);
  assert.equal(source.includes("rebuildMenuNavigation();\n    window.setTimeout(async () => {"), true);
});

test('direct Gmail hash loads are replayed through InboxSDK route handlers', () => {
  const source = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/inboxsdk-layer.js'),
    'utf8',
  );
  const routeCaptureSource = fs.readFileSync(
    path.resolve(__dirname, '..', 'route-capture.js'),
    'utf8',
  );
  const manifest = JSON.parse(fs.readFileSync(
    path.resolve(__dirname, '..', 'manifest.json'),
    'utf8',
  ));

  assert.equal(source.includes('function parseDirectHashRoute(hash = \'\')'), true);
  assert.equal(source.includes('async function syncDirectHashRoute({ force = false } = {})'), true);
  assert.equal(source.includes("const STORAGE_PENDING_DIRECT_ROUTE = '__clearledgr_pending_direct_route_v1';"), true);
  assert.equal(source.includes("const ATTR_PENDING_DIRECT_ROUTE = 'data-clearledgr-pending-direct-route';"), true);
  assert.equal(source.includes('async function readPendingDirectHashRoute() {'), true);
  assert.equal(source.includes("if (globalThis.chrome?.runtime?.sendMessage) {"), true);
  assert.equal(source.includes("const response = await globalThis.chrome.runtime.sendMessage({ action: 'getPendingDirectRouteForTab' });"), true);
  assert.equal(source.includes("await globalThis.chrome.runtime.sendMessage({ action: 'clearPendingDirectRouteForTab' });"), true);
  assert.equal(source.includes("const pendingHash = !hash.startsWith('#clearledgr/')"), true);
  assert.equal(source.includes('const confirmRouteActivation = async () => {'), true);
  assert.equal(source.includes('rememberActiveClearledgrRoute(activeHash);'), true);
  assert.equal(source.includes('if (activeHash === expectedHash) {'), true);
  assert.equal(source.includes('await clearPendingDirectHashRoute();'), true);
  assert.equal(source.includes('window.setTimeout(() => {'), true);
  assert.equal(source.includes('globalThis.chrome?.storage?.session?.get'), true);
  assert.equal(source.includes("window.addEventListener('hashchange', () => {"), true);
  assert.equal(source.includes("const restored = await maybeRestoreReloadedClearledgrRoute({ force: true });"), true);
  assert.equal(source.includes("await syncDirectHashRoute({ force: true });"), true);
  assert.equal(source.includes("routeId: 'clearledgr/invoice/:id'"), true);
  assert.equal(source.includes("routeId: 'clearledgr/invoices-view/:ref'"), true);
  assert.equal(routeCaptureSource.includes("window.addEventListener('hashchange', writePendingRoute, true);"), true);
  assert.equal(routeCaptureSource.includes('window.sessionStorage.setItem(STORAGE_KEY'), true);
  assert.equal(routeCaptureSource.includes("document.documentElement.setAttribute(ATTRIBUTE_NAME, normalizedHash);"), true);
  assert.equal(routeCaptureSource.includes('globalThis.chrome?.storage?.session?.set'), true);
  assert.deepEqual(manifest.content_scripts?.[0]?.js || [], ['route-capture.js']);
  assert.equal(manifest.content_scripts?.[0]?.run_at, 'document_start');
});

test('reloading an active Clearledgr page restores that route instead of dropping back to inbox', () => {
  const source = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/inboxsdk-layer.js'),
    'utf8',
  );

  assert.equal(source.includes("const STORAGE_RELOAD_ROUTE = '__clearledgr_reload_route_v1';"), true);
  assert.equal(source.includes('function navigationWasReload() {'), true);
  assert.equal(source.includes('function persistReloadedClearledgrRoute() {'), true);
  assert.equal(source.includes('async function maybeRestoreReloadedClearledgrRoute({ force = false } = {}) {'), true);
  assert.equal(source.includes("globalThis.performance?.getEntriesByType?.('navigation')"), true);
  assert.equal(source.includes("window.addEventListener('pagehide', persistReloadedClearledgrRoute, true);"), true);
  assert.equal(source.includes("window.addEventListener('beforeunload', persistReloadedClearledgrRoute, true);"), true);
  assert.equal(source.includes("window.setTimeout(async () => {\n    const restored = await maybeRestoreReloadedClearledgrRoute({ force: true });"), true);
  assert.equal(source.includes('window.setTimeout(() => clearReloadedClearledgrRoute(), ROUTE_RESTORE_WINDOW_MS);'), true);
});
