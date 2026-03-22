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
    'Pending human approval'
  );
  assert.equal(workActions.normalizeWorkState('pending_approval'), 'needs_approval');
});

test('work-surface primary action map matches the current Gmail execution doctrine', async () => {
  const {
    canRejectWorkItem,
    getPrimaryActionConfig,
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
  assert.deepEqual(getPrimaryActionConfig('needs_approval'), {
    id: 'nudge_approver',
    label: 'Nudge approver',
  });
  assert.deepEqual(getPrimaryActionConfig('ready_to_post'), {
    id: 'preview_erp_post',
    label: 'Preview ERP post',
  });
  assert.deepEqual(getPrimaryActionConfig('failed_post'), {
    id: 'retry_erp_post',
    label: 'Retry ERP post',
  });
  assert.equal(getPrimaryActionConfig('approved'), null);
  assert.equal(getPrimaryActionConfig('rejected'), null);
  assert.equal(getPrimaryActionConfig('needs_approval', 'viewer'), null);
  assert.equal(canRejectWorkItem('needs_approval', 'viewer'), false);
});

test('route registry stays Gmail-native and AppMenu exposes every eligible route', async () => {
  const {
    ROUTES,
    DEFAULT_ROUTE,
    getMenuNavRoutes,
    getNavEligibleRoutes,
    getVisibleNavRoutes,
    hideRoute,
    pinRoute,
  } = await importModule('src/routes/route-registry.js');
  const routeIds = ROUTES.map((route) => route.id);
  const routeMap = new Map(ROUTES.map((route) => [route.id, route]));
  const defaultNavRouteIds = getVisibleNavRoutes().map((route) => route.id);
  const customizedNavRouteIds = getVisibleNavRoutes(
    pinRoute('clearledgr/vendors', hideRoute('clearledgr/connections'))
  ).map((route) => route.id);
  const adminEligibleRouteIds = getNavEligibleRoutes({ includeAdmin: true }).map((route) => route.id);
  const adminDefaultNavRouteIds = getVisibleNavRoutes({}, { includeAdmin: true }).map((route) => route.id);
  const approverVisibleRouteIds = getVisibleNavRoutes({}, { includeOps: false }).map((route) => route.id);
  const adminVisibleRouteIds = getVisibleNavRoutes(
    pinRoute('clearledgr/health', {}, { includeAdmin: true }),
    { includeAdmin: true },
  ).map((route) => route.id);
  const defaultMenuRouteIds = getMenuNavRoutes().map((route) => route.id);
  const approverMenuRouteIds = getMenuNavRoutes({}, { includeOps: false }).map((route) => route.id);
  const adminMenuRouteIds = getMenuNavRoutes({}, { includeAdmin: true }).map((route) => route.id);
  const hiddenMenuRouteIds = getMenuNavRoutes(
    hideRoute('clearledgr/vendors', pinRoute('clearledgr/vendors')),
  ).map((route) => route.id);

  assert.equal(DEFAULT_ROUTE, 'clearledgr/home');
  assert.ok(routeIds.includes('clearledgr/home'));
  assert.ok(routeIds.includes('clearledgr/pipeline'));
  assert.ok(routeIds.includes('clearledgr/review'));
  assert.ok(routeIds.includes('clearledgr/activity'));
  assert.ok(routeIds.includes('clearledgr/connections'));
  assert.equal(routeIds.some((id) => /\bops\b/i.test(id)), false);
  assert.equal(routeIds.some((id) => /\bbatch\b/i.test(id)), false);
  assert.deepEqual(defaultNavRouteIds, [
    'clearledgr/home',
    'clearledgr/pipeline',
  ]);
  assert.ok(customizedNavRouteIds.includes('clearledgr/vendors'));
  assert.equal(customizedNavRouteIds.includes('clearledgr/connections'), false);
  assert.equal(customizedNavRouteIds.includes('clearledgr/activity'), false);
  assert.equal(adminEligibleRouteIds.includes('clearledgr/health'), true);
  assert.deepEqual(adminDefaultNavRouteIds, [
    'clearledgr/home',
    'clearledgr/pipeline',
    'clearledgr/connections',
  ]);
  assert.equal(defaultNavRouteIds.includes('clearledgr/health'), false);
  assert.equal(adminVisibleRouteIds.includes('clearledgr/health'), true);
  assert.deepEqual(approverVisibleRouteIds, [
    'clearledgr/home',
    'clearledgr/pipeline',
  ]);
  assert.equal(defaultMenuRouteIds.includes('clearledgr/home'), true);
  assert.equal(defaultMenuRouteIds.includes('clearledgr/pipeline'), true);
  assert.equal(defaultMenuRouteIds.includes('clearledgr/review'), true);
  assert.equal(defaultMenuRouteIds.includes('clearledgr/vendors'), true);
  assert.equal(defaultMenuRouteIds.includes('clearledgr/connections'), false);
  assert.deepEqual(approverMenuRouteIds, [
    'clearledgr/home',
    'clearledgr/pipeline',
  ]);
  assert.equal(adminMenuRouteIds.includes('clearledgr/connections'), true);
  assert.equal(adminMenuRouteIds.includes('clearledgr/health'), true);
  assert.equal(hiddenMenuRouteIds.includes('clearledgr/vendors'), true);
  assert.equal(routeMap.get('clearledgr/connections').adminOnly, true);
  assert.equal(routeMap.get('clearledgr/rules').adminOnly, true);
  assert.equal(routeMap.get('clearledgr/team').adminOnly, true);
  assert.equal(routeMap.get('clearledgr/company').adminOnly, true);
  assert.equal(routeMap.get('clearledgr/plan').adminOnly, true);
});

test('route icon mapper returns concrete icon assets for menu routes', async () => {
  const { ROUTES } = await importModule('src/routes/route-registry.js');
  const { getPipelineViewIconUrl, getRouteIconUrl } = await importModule('src/routes/route-icons.js');

  const iconUrls = ROUTES.map((route) => getRouteIconUrl(route));

  assert.equal(iconUrls.every((url) => String(url).startsWith('data:image/svg+xml')), true);
  assert.equal(new Set(iconUrls).size >= 6, true);
  assert.equal(getPipelineViewIconUrl().startsWith('data:image/svg+xml'), true);
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
  assert.equal(
    bootstrap.current_user.preferences.gmail_extension.pipeline_views.activeSliceId,
    'waiting_on_approval',
  );
  assert.deepEqual(bootstrap.recentActivity, [{ title: 'Approval sent' }]);
  assert.deepEqual(bootstrap.required_actions, ['connect_erp']);
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
  assert.equal(homeSource.includes('/api/workspace/integrations/slack/install/start'), true);
  assert.equal(connectionsSource.includes("bootstrap?.gmail_auth_url"), false);
  assert.equal(connectionsSource.includes('/api/workspace/integrations/gmail/connect/start'), true);
});

test('oauth completion rehydrates bootstrap so app menu access can refresh', () => {
  const source = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/inboxsdk-layer.js'),
    'utf8',
  );

  assert.equal(source.includes('void getBootstrap();'), true);
  assert.equal(source.includes('routeAccessResolved = true;'), true);
  assert.equal(source.includes('currentRouteAccess = { includeAdmin: false, includeOps: false };'), true);
});

test('invoice detail page stays on the canonical AP action contract', () => {
  const source = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/routes/pages/InvoiceDetailPage.js'),
    'utf8',
  );

  assert.equal(source.includes('/extension/approve-and-post'), false);
  assert.equal(source.includes("getPrimaryActionConfig(state, actorRole, documentType)"), true);
  assert.equal(source.includes("auditData?.events"), true);
  assert.equal(source.includes("executeIntent(api, orgId, 'post_to_erp'"), true);
  assert.equal(source.includes("executeIntent(api, orgId, 'request_approval'"), true);
  assert.equal(source.includes('prettifyEventType(eventType)'), false);
  assert.equal(source.includes('partitionAuditEvents(auditEvents)'), true);
  assert.equal(source.includes('Record history'), true);
  assert.equal(source.includes('Background activity'), true);
  assert.equal(source.includes('Accounting linkage'), true);
  assert.equal(source.includes('Paused field review'), true);
  assert.equal(source.includes('Email said'), true);
  assert.equal(source.includes('Attachment said'), true);
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
  assert.equal(reviewSource.includes('Review workbench'), true);
  assert.equal(reviewSource.includes('/field-review/resolve'), true);
  assert.equal(reviewSource.includes('/field-review/bulk-resolve'), true);
  assert.equal(reviewSource.includes('/non-invoice/resolve'), true);
  assert.equal(reviewSource.includes('Keyboard: J/K move'), true);
  assert.equal(reviewSource.includes('Paused field review'), true);
});

test('pipeline page supports bulk routing and keyboard-first queue movement', () => {
  const source = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/routes/pages/PipelinePage.js'),
    'utf8',
  );

  assert.equal(source.includes("/extension/route-low-risk-approval"), true);
  assert.equal(source.includes('Select visible'), true);
  assert.equal(source.includes('Route selected'), true);
  assert.equal(source.includes('Keyboard: J/K move'), true);
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

  assert.equal(source.includes('const adminAccess = hasAdminAccess(bootstrap);'), true);
  assert.equal(source.includes('const pipelineScope = { orgId, userEmail };'), true);
  assert.equal(source.includes('getBootstrappedPipelinePreferences(bootstrap)'), true);
  assert.equal(source.includes('readPipelinePreferences(pipelineScope)'), true);
  assert.equal(source.includes('getPinnedPipelineViews(pipelinePrefs)'), true);
  assert.equal(source.includes('getStarterPipelineViews(pipelinePrefs)'), true);
  assert.equal(source.includes('writePipelinePreferences(pipelineScope, view.snapshot)'), true);
  assert.equal(source.includes("activatePipelineSlice(pipelineScope, sliceId)"), true);
  assert.equal(source.includes('Setup pages are reserved for admins.'), true);
  assert.equal(source.includes('Support surfaces'), true);
  assert.equal(source.includes('Finance-native starter views are ready'), true);
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
  assert.equal(source.includes('FieldReviewSummary'), true);
  assert.equal(source.includes('Email ${first.email_value_display ||'), true);
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
  assert.equal(source.includes('Paused field review'), true);
  assert.equal(source.includes('Email said'), true);
  assert.equal(source.includes('View audit'), true);
  assert.equal(source.includes('Key history'), true);
  assert.equal(source.includes('Background activity'), true);
  assert.equal(source.includes('MiniBarChart'), false);
  assert.equal(source.includes('HorizontalBar'), false);
  assert.equal(source.includes('SnapshotCard'), false);
  assert.equal(source.includes('prompt('), false);
  assert.equal(source.includes('confirm('), false);
  assert.equal(source.includes('window.open('), false);
});

test('thread handler refreshes the canonical thread item so new evidence fields replace stale queue rows', () => {
  const source = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/inboxsdk-layer.js'),
    'utf8',
  );

  assert.equal(source.includes('Always refresh the canonical item for the open thread'), true);
  assert.equal(source.includes('queueManager.upsertQueueItem(item);'), true);
  assert.equal(source.includes('queueManager.emitQueueUpdated();'), true);
  assert.equal(source.includes('if (threadId && queueManager) {'), true);
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
  assert.equal(sidebarSource.includes('Open in pipeline'), true);
  assert.equal(detailSource.includes('focusPipelineItem(pipelineScope, item, \'detail\')'), true);
  assert.equal(detailSource.includes('const openInPipeline = useCallback(() => {'), true);
});

test('gmail route gating distinguishes ops access from admin access and removes stale thread action paths', () => {
  const inboxSource = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/inboxsdk-layer.js'),
    'utf8',
  );

  assert.equal(inboxSource.includes('hasAdminAccess(bootstrap)'), true);
  assert.equal(inboxSource.includes('hasOpsAccess(bootstrap)'), true);
  assert.equal(inboxSource.includes('let routeAccessResolved = false;'), true);
  assert.equal(inboxSource.includes('if (!routeAccessResolved) return;'), true);
  assert.equal(inboxSource.includes('iconUrl: getRouteIconUrl(route)'), true);
  assert.equal(inboxSource.includes('iconUrl: route.iconUrl'), true);
  assert.equal(inboxSource.includes('queueManager.submitForApproval'), false);
  assert.equal(inboxSource.includes('title: \'Open in pipeline\''), true);
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
  const vendorsSource = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/routes/pages/VendorsPage.js'),
    'utf8',
  );

  assert.equal(activitySource.includes('MiniBarChart'), false);
  assert.equal(activitySource.includes('HorizontalBar'), false);
  assert.equal(activitySource.includes('SnapshotCard'), false);
  assert.equal(activitySource.includes('kpi-row'), false);
  assert.equal(connectionsSource.includes('<table class="table">'), false);
  assert.equal(companySource.includes('cl-org-json'), false);
  assert.equal(healthSource.includes('<table class="table">'), false);
  assert.equal(rulesSource.includes('cl-policy-json'), false);
  assert.equal(rulesSource.includes('cl-policy-confidence'), true);
  assert.equal(reconSource.includes('This surface is intentionally secondary.'), true);
  assert.equal(teamSource.includes('<table class="table">'), false);
  assert.equal(upcomingSource.includes('Upcoming follow-ups'), true);
  assert.equal(templatesSource.includes('Syncfusion'), false);
  assert.equal(reportsSource.includes('Pipeline remains the operational surface.'), true);
  assert.equal(vendorsSource.includes('kpi-row'), false);
});
