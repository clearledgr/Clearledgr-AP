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
    'Waiting on vendor reply. Clearledgr will send reminders automatically.',
  );
  assert.equal(
    getWorkStateNotice('needs_info', 'invoice', {
      followup_next_action: 'manual_vendor_escalation',
    }),
    'Vendor did not reply. Manual escalation needed.',
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
  assert.equal(view.nextActionLabel, 'Approval request sent, waiting for decision');
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

