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
  const { getPrimaryActionConfig } = await importModule('src/utils/work-actions.js');

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
});

test('route registry stays Gmail-native and does not define an in-Gmail Ops route', async () => {
  const {
    ROUTES,
    DEFAULT_ROUTE,
    getNavEligibleRoutes,
    getVisibleNavRoutes,
    hideRoute,
    pinRoute,
  } = await importModule('src/routes/route-registry.js');
  const routeIds = ROUTES.map((route) => route.id);
  const defaultNavRouteIds = getVisibleNavRoutes().map((route) => route.id);
  const customizedNavRouteIds = getVisibleNavRoutes(
    pinRoute('clearledgr/vendors', hideRoute('clearledgr/connections'))
  ).map((route) => route.id);
  const adminEligibleRouteIds = getNavEligibleRoutes({ includeAdmin: true }).map((route) => route.id);
  const adminVisibleRouteIds = getVisibleNavRoutes(
    pinRoute('clearledgr/health', {}, { includeAdmin: true }),
    { includeAdmin: true },
  ).map((route) => route.id);

  assert.equal(DEFAULT_ROUTE, 'clearledgr/home');
  assert.ok(routeIds.includes('clearledgr/home'));
  assert.ok(routeIds.includes('clearledgr/pipeline'));
  assert.ok(routeIds.includes('clearledgr/activity'));
  assert.ok(routeIds.includes('clearledgr/connections'));
  assert.equal(routeIds.some((id) => /\bops\b/i.test(id)), false);
  assert.equal(routeIds.some((id) => /\bbatch\b/i.test(id)), false);
  assert.deepEqual(defaultNavRouteIds, [
    'clearledgr/home',
    'clearledgr/pipeline',
    'clearledgr/connections',
  ]);
  assert.ok(customizedNavRouteIds.includes('clearledgr/vendors'));
  assert.equal(customizedNavRouteIds.includes('clearledgr/connections'), false);
  assert.equal(customizedNavRouteIds.includes('clearledgr/activity'), false);
  assert.equal(adminEligibleRouteIds.includes('clearledgr/health'), true);
  assert.equal(defaultNavRouteIds.includes('clearledgr/health'), false);
  assert.equal(adminVisibleRouteIds.includes('clearledgr/health'), true);
});

test('admin bootstrap adapter preserves backend current user role instead of hardcoding admin', async () => {
  const { createAdminApi } = await importModule('src/routes/admin-api.js');
  const calls = [];
  const queueManager = {
    runtimeConfig: {
      organizationId: 'org-eu-1',
      backendUrl: 'https://api.clearledgr.test',
    },
    async backendFetch(url) {
      calls.push(url);
      if (url.endsWith('/api/admin/bootstrap?organization_id=org-eu-1')) {
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
              current_user: { role: 'operator', email: 'ops@clearledgr.com' },
            };
          },
        };
      }
      if (url.endsWith('/api/admin/policies/ap?organization_id=org-eu-1')) {
        return {
          ok: true,
          status: 200,
          async json() {
            return { policy: { config_json: { approval_threshold: 500 } } };
          },
        };
      }
      if (url.endsWith('/api/admin/team/invites?organization_id=org-eu-1')) {
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

  const api = createAdminApi(queueManager);
  const bootstrap = await api.bootstrapAdminData();

  assert.deepEqual(calls, [
    'https://api.clearledgr.test/api/admin/bootstrap?organization_id=org-eu-1',
    'https://api.clearledgr.test/api/admin/policies/ap?organization_id=org-eu-1',
    'https://api.clearledgr.test/api/admin/team/invites?organization_id=org-eu-1',
  ]);
  assert.equal(bootstrap.current_user.role, 'operator');
  assert.equal(bootstrap.current_user.email, 'ops@clearledgr.com');
  assert.deepEqual(bootstrap.recentActivity, [{ title: 'Approval sent' }]);
  assert.deepEqual(bootstrap.required_actions, ['connect_erp']);
});

test('invoice detail page stays on the canonical AP action contract', () => {
  const source = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/routes/pages/InvoiceDetailPage.js'),
    'utf8',
  );

  assert.equal(source.includes('/extension/approve-and-post'), false);
  assert.equal(source.includes("getPrimaryActionConfig(state)"), true);
  assert.equal(source.includes("auditData?.events"), true);
  assert.equal(source.includes("executeIntent(api, orgId, 'post_to_erp'"), true);
  assert.equal(source.includes("executeIntent(api, orgId, 'request_approval'"), true);
  assert.equal(source.includes('prettifyEventType(eventType)'), false);
  assert.equal(source.includes('event?.operator_title || safeTitle'), true);
});

test('home page queue shortcuts and saved views stay org-scoped', () => {
  const source = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/routes/pages/HomePage.js'),
    'utf8',
  );

  assert.equal(source.includes('orgId,'), true);
  assert.equal(source.includes('readPipelinePreferences(orgId)'), true);
  assert.equal(source.includes('writePipelinePreferences(orgId, view.snapshot)'), true);
  assert.equal(source.includes("activatePipelineSlice(orgId, sliceId)"), true);
});

test('sidebar audit rendering falls back to safe generic copy instead of raw event names', () => {
  const source = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/components/SidebarApp.js'),
    'utf8',
  );

  assert.equal(source.includes('prettifyEventType(eventType)'), false);
  assert.equal(source.includes("event?.operator_title || safeTitle"), true);
  assert.equal(source.includes("event?.operator_message || safeDetail"), true);
});

test('secondary Gmail pages stay lightweight and avoid raw admin/dashboard surfaces', () => {
  const activitySource = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/routes/pages/ActivityPage.js'),
    'utf8',
  );
  const companySource = fs.readFileSync(
    path.resolve(__dirname, '..', 'src/routes/pages/CompanyPage.js'),
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

  assert.equal(activitySource.includes('MiniBarChart'), false);
  assert.equal(activitySource.includes('HorizontalBar'), false);
  assert.equal(companySource.includes('cl-org-json'), false);
  assert.equal(rulesSource.includes('cl-policy-json'), false);
  assert.equal(rulesSource.includes('cl-policy-confidence'), true);
  assert.equal(reconSource.includes('AP remains the primary production workflow today.'), true);
});
