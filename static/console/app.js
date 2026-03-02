const PAGES = [
  { id: "setup", title: "Setup", subtitle: "Connect your tools and start processing invoices." },
  { id: "activity", title: "Activity", subtitle: "See what Clearledgr processed and what needs attention." },
  { id: "ops", title: "Ops", subtitle: "Monitoring, batch actions, and operational recovery controls." },
  { id: "integrations", title: "Integrations", subtitle: "Connect and verify Gmail, Slack, and ERP." },
  { id: "organization", title: "Organization", subtitle: "Manage organization profile and runtime settings." },
  { id: "policies", title: "AP Policies", subtitle: "Configure policy behavior for AP decisioning." },
  { id: "team", title: "Team", subtitle: "Invite teammates and manage access." },
  { id: "plan", title: "Plan & Usage", subtitle: "Track plan, trial, onboarding, and usage." },
  { id: "health", title: "Health", subtitle: "See required actions before production rollout." },
];

const state = {
  orgId: "default",
  activePage: "setup",
  bootstrap: null,
  inviteToken: null,
  netsuiteFormVisible: false,
  sapFormVisible: false,
};

const qs = (selector) => document.querySelector(selector);

function clearSession() {
  document.cookie = "clearledgr_admin_access=; Max-Age=0; path=/";
  document.cookie = "clearledgr_admin_refresh=; Max-Age=0; path=/";
  document.cookie = "clearledgr_admin_csrf=; Max-Age=0; path=/";
}

function params() {
  return new URLSearchParams(window.location.search);
}

async function api(path, options = {}) {
  const method = String(options.method || "GET").trim().toUpperCase();
  const headers = {
    "Content-Type": "application/json",
    ...(options.headers || {}),
  };
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    const csrfToken = readCookie("clearledgr_admin_csrf");
    if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
  }
  const response = await fetch(path, {
    ...options,
    headers,
    credentials: "include",
  });
  if (!response.ok) {
    const payload = await response.text();
    const error = new Error(payload || `HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
  if (response.status === 204) return {};
  return response.json();
}

function readCookie(name) {
  const prefix = `${name}=`;
  const parts = String(document.cookie || "").split(";").map((part) => part.trim());
  const match = parts.find((part) => part.startsWith(prefix));
  if (!match) return "";
  return decodeURIComponent(match.slice(prefix.length));
}

// ==================== TOAST NOTIFICATIONS ====================

function toast(message, type = "success") {
  const container = qs("#toast-container");
  if (!container) return;
  const el = document.createElement("div");
  el.className = `toast toast-${type}`;
  el.textContent = message;
  container.appendChild(el);
  requestAnimationFrame(() => el.classList.add("show"));
  setTimeout(() => {
    el.classList.remove("show");
    setTimeout(() => el.remove(), 300);
  }, 4000);
}

// ==================== AUTH SHELL ====================

function showAuth(message = "") {
  qs("#auth-shell").classList.remove("hidden");
  qs("#console-shell").classList.add("hidden");
  qs("#auth-message").textContent = message;
  if (state.inviteToken) {
    qs("#invite-shell").classList.remove("hidden");
  } else {
    qs("#invite-shell").classList.add("hidden");
  }
}

function showConsole() {
  qs("#auth-shell").classList.add("hidden");
  qs("#console-shell").classList.remove("hidden");
}

function cleanUrlParams(keys) {
  const clean = new URL(window.location.href);
  keys.forEach((key) => clean.searchParams.delete(key));
  window.history.replaceState({}, "", clean.toString());
}

// ==================== NAVIGATION ====================

function renderNav() {
  const nav = qs("#nav");
  nav.innerHTML = "";
  const visiblePages = PAGES.filter((page) => !(page.id === "ops" && !hasOpsAccess()));
  if (!visiblePages.some((page) => page.id === state.activePage)) {
    state.activePage = visiblePages[0]?.id || "setup";
  }
  visiblePages.forEach((page) => {
    const button = document.createElement("button");
    button.className = `nav-btn ${state.activePage === page.id ? "active" : ""}`;
    button.textContent = page.title;
    button.onclick = () => {
      state.activePage = page.id;
      renderNav();
      renderPage();
    };
    nav.appendChild(button);
  });
}

// ==================== HELPERS ====================

function statusBadge(connected) {
  return `<span class="status-badge ${connected ? "connected" : ""}">${connected ? "Connected" : "Not connected"}</span>`;
}

function integrationByName(name) {
  return (state.bootstrap?.integrations || []).find((item) => item.name === name) || {};
}

function checkMark(ok) {
  return ok ? '<span class="check-ok">Connected</span>' : '<span class="check-no">Not connected</span>';
}

function hasOpsAccess() {
  const role = String(state.bootstrap?.current_user?.role || "").trim().toLowerCase();
  return ["admin", "owner", "operator"].includes(role);
}

function formatRate(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "0.0%";
  return `${numeric.toFixed(1)}%`;
}

function pageExists(pageId) {
  return PAGES.some((page) => page.id === pageId);
}

function resolveInvoiceReference(item) {
  return String(item?.thread_id || item?.message_id || item?.id || "").trim();
}

function currentUserEmail() {
  return String(state.bootstrap?.current_user?.email || "").trim() || "admin_console";
}

// ==================== SETUP / WIZARD PAGE ====================

function setupPage() {
  const gmail = integrationByName("gmail");
  const slack = integrationByName("slack");
  const erp = integrationByName("erp");
  const policyConfig = state.bootstrap?.policyPayload?.policy?.config_json || {};
  const slackChannel = slack.approval_channel || "";

  const gmailOk = !!gmail.connected;
  const slackOk = !!slack.connected;
  const erpOk = !!erp.connected;
  const channelOk = slackOk && !!slackChannel;
  const policyOk = policyConfig && Object.keys(policyConfig).length > 0;
  const allReady = gmailOk && slackOk && erpOk && channelOk && policyOk;

  const erpType = erp.erp_type || "";
  const erpLabel = erpType ? erpType.charAt(0).toUpperCase() + erpType.slice(1) : "";

  return `
    <div class="panel">
      <h3>Setup steps</h3>
      <div class="readiness-list">
        <div class="readiness-item">${checkMark(gmailOk && slackOk && erpOk)} 1) Integrations</div>
        <div class="readiness-item">${checkMark(channelOk)} 2) Approval channel</div>
        <div class="readiness-item">${checkMark(policyOk)} 3) AP policies</div>
        <div class="readiness-item">${checkMark(allReady)} 4) Launch</div>
      </div>
      <p class="muted">Follow this order: Integrations → Channel → Policies → Launch.</p>
    </div>

    <div class="panel">
      <h3>Step 1: Connect integrations</h3>
      <p class="muted">One-click setup for each service. Gmail is connected automatically via the Chrome extension.</p>
      <div class="connector-grid">

        <div class="connector-card ${gmailOk ? "done" : ""}">
          <div class="connector-header">
            <strong>Gmail</strong>
            ${statusBadge(gmailOk)}
          </div>
          <p class="muted">Reads invoices from your inbox.</p>
          ${gmailOk
            ? '<p class="connector-detail">Auto-connected via extension</p>'
            : '<button id="connect-gmail-btn" class="connector-btn">Connect Gmail</button>'}
        </div>

        <div class="connector-card ${slackOk ? "done" : ""}">
          <div class="connector-header">
            <strong>Slack</strong>
            ${statusBadge(slackOk)}
          </div>
          <p class="muted">Sends approval requests to your team.</p>
          ${slackOk
            ? `<p class="connector-detail">Workspace: ${slack.team_name || "connected"}</p>`
            : '<button id="connect-slack-btn" class="connector-btn">Connect Slack</button>'}
        </div>

      </div>
    </div>

    <div class="panel">
      <h3>Step 1b: Choose your ERP</h3>
      <p class="muted">Connect the accounting system where bills get posted.</p>
      <div class="connector-grid connector-grid-3">

        <div class="connector-card ${erpOk && erpType === "quickbooks" ? "done" : ""}">
          <div class="connector-header">
            <strong>QuickBooks</strong>
            ${erpOk && erpType === "quickbooks" ? statusBadge(true) : ""}
          </div>
          <p class="muted">QuickBooks Online via OAuth.</p>
          ${erpOk && erpType === "quickbooks"
            ? `<p class="connector-detail">Realm: ${erp.realm_id || "connected"}</p>`
            : '<button class="connector-btn erp-connect-btn" data-erp="quickbooks">Connect</button>'}
        </div>

        <div class="connector-card ${erpOk && erpType === "xero" ? "done" : ""}">
          <div class="connector-header">
            <strong>Xero</strong>
            ${erpOk && erpType === "xero" ? statusBadge(true) : ""}
          </div>
          <p class="muted">Xero via OAuth.</p>
          ${erpOk && erpType === "xero"
            ? `<p class="connector-detail">Tenant: ${erp.tenant_id || "connected"}</p>`
            : '<button class="connector-btn erp-connect-btn" data-erp="xero">Connect</button>'}
        </div>

        <div class="connector-card ${erpOk && erpType === "netsuite" ? "done" : ""}">
          <div class="connector-header">
            <strong>NetSuite</strong>
            ${erpOk && erpType === "netsuite" ? statusBadge(true) : ""}
          </div>
          <p class="muted">Token-Based Authentication.</p>
          ${erpOk && erpType === "netsuite"
            ? `<p class="connector-detail">Account: ${erp.account_id || "connected"}</p>`
            : '<button class="connector-btn" id="netsuite-setup-btn">Setup</button>'}
        </div>

        <div class="connector-card ${erpOk && erpType === "sap" ? "done" : ""}">
          <div class="connector-header">
            <strong>SAP</strong>
            ${erpOk && erpType === "sap" ? statusBadge(true) : ""}
          </div>
          <p class="muted">SAP via service-account credentials.</p>
          ${erpOk && erpType === "sap"
            ? `<p class="connector-detail">Base URL: ${erp.base_url || "connected"}</p>`
            : '<button class="connector-btn" id="sap-setup-btn">Setup</button>'}
        </div>

      </div>

      <div id="netsuite-form-panel" class="netsuite-form-panel ${state.netsuiteFormVisible ? "" : "hidden"}">
        <h4>NetSuite Credentials</h4>
        <p class="muted">In NetSuite: Setup &gt; Company &gt; Enable Features &gt; SuiteCloud &gt; Token-Based Authentication. Create an Integration record and generate a Token.</p>
        <div class="form-grid">
          <label>Account ID</label>
          <input id="ns-account-id" type="text" placeholder="1234567 or 1234567_SB1" />
          <label>Consumer Key</label>
          <input id="ns-consumer-key" type="text" />
          <label>Consumer Secret</label>
          <input id="ns-consumer-secret" type="password" />
          <label>Token ID</label>
          <input id="ns-token-id" type="text" />
          <label>Token Secret</label>
          <input id="ns-token-secret" type="password" />
        </div>
        <div class="row" style="margin-top:10px">
          <button id="ns-connect-btn" class="connector-btn">Test & Connect</button>
          <button id="ns-cancel-btn" class="alt">Cancel</button>
        </div>
      </div>

      <div id="sap-form-panel" class="netsuite-form-panel ${state.sapFormVisible ? "" : "hidden"}">
        <h4>SAP Credentials</h4>
        <p class="muted">Use a least-privilege integration user and the OData financials base URL.</p>
        <div class="form-grid">
          <label>Base URL</label>
          <input id="sap-base-url" type="text" placeholder="https://tenant.sapbydesign.com/sap/byd/odata/v1/financials" />
          <label>Username</label>
          <input id="sap-username" type="text" />
          <label>Password</label>
          <input id="sap-password" type="password" />
        </div>
        <div class="row" style="margin-top:10px">
          <button id="sap-connect-btn" class="connector-btn">Test & Connect</button>
          <button id="sap-cancel-btn" class="alt">Cancel</button>
        </div>
      </div>
    </div>

    <div class="panel ${slackOk ? "" : "panel-disabled"}">
      <h3>Step 2: Approval channel</h3>
      <p class="muted">Where should Clearledgr send invoice approval requests in Slack?</p>
      <div class="row">
        <input id="slack-channel-input" placeholder="#finance-approvals" value="${slackChannel}" ${slackOk ? "" : "disabled"} />
        <button id="save-slack-channel-btn" class="alt" ${slackOk ? "" : "disabled"}>Save Channel</button>
        ${slackOk ? '<button id="test-slack-btn" class="alt">Test</button>' : ""}
      </div>
    </div>

    <div class="panel">
      <h3>Step 3: AP policy readiness</h3>
      <p class="muted">Review policy thresholds before launch to ensure approval and posting guardrails are correct.</p>
      <div class="readiness-list">
        <div class="readiness-item">${checkMark(policyOk)} Policy config loaded</div>
      </div>
      <div class="row">
        <button id="open-policies-page-btn" class="alt">Open AP Policies</button>
      </div>
    </div>

    ${_autopilotPanel(gmail)}

    <div class="panel">
      <h3>Step 4: Launch readiness</h3>
      <div class="readiness-list">
        <div class="readiness-item">${checkMark(gmailOk)} Gmail</div>
        <div class="readiness-item">${checkMark(slackOk)} Slack</div>
        <div class="readiness-item">${checkMark(erpOk)} ERP (${erpLabel || "none"})</div>
        <div class="readiness-item">${checkMark(channelOk)} Approval channel</div>
        <div class="readiness-item">${checkMark(policyOk)} AP policies</div>
      </div>
      <button id="launch-btn" class="launch-btn" ${allReady ? "" : "disabled"}>
        ${allReady ? "Launch Clearledgr" : "Complete setup above to launch"}
      </button>
    </div>
  `;
}

// ==================== AUTOPILOT STATUS ====================

function _autopilotPanel(gmail) {
  if (!gmail.connected) return "";
  const ws = gmail.watch_status || "unknown";
  const wsLabel = ws === "active" ? "Push (real-time)" : ws === "polling" ? "Polling (60s)" : "Disconnected";
  const wsClass = ws === "active" ? "ap-active" : ws === "polling" ? "ap-polling" : "ap-off";
  const lastScan = gmail.last_sync_at
    ? new Date(gmail.last_sync_at).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
    : "Never";
  const processed = gmail.invoices_processed || 0;
  const dash = state.bootstrap?.dashboard || {};
  const total = dash.total_invoices || processed || 0;
  return `
    <div class="panel autopilot-panel">
      <h3>Autopilot Status</h3>
      <div class="autopilot-grid">
        <div class="autopilot-item">
          <span class="autopilot-dot ${wsClass}"></span>
          <div>
            <strong>Gmail Watch</strong>
            <span class="muted">${wsLabel}</span>
          </div>
        </div>
        <div class="autopilot-item">
          <span class="autopilot-dot ${total > 0 ? "ap-active" : "ap-polling"}"></span>
          <div>
            <strong>${total} invoices</strong>
            <span class="muted">processed</span>
          </div>
        </div>
        <div class="autopilot-item">
          <div>
            <strong>Last scan</strong>
            <span class="muted">${lastScan}</span>
          </div>
        </div>
        <div class="autopilot-item">
          <div>
            <strong>Email</strong>
            <span class="muted">${gmail.email || "—"}</span>
          </div>
        </div>
      </div>
    </div>
  `;
}

// ==================== FIRST-INVOICE CELEBRATION ====================

let _firstInvoicePollTimer = null;
let _lastKnownTotal = null;

function startFirstInvoicePoll() {
  if (_firstInvoicePollTimer) return;
  const dash = state.bootstrap?.dashboard || {};
  _lastKnownTotal = dash.total_invoices || 0;
  if (_lastKnownTotal > 0) return; // Already has invoices
  _firstInvoicePollTimer = setInterval(async () => {
    try {
      const fresh = await api(`/api/admin/bootstrap?organization_id=${encodeURIComponent(state.orgId)}`);
      const dashboard = fresh?.dashboard || {};
      const newTotal = dashboard.total_invoices || 0;
      if (newTotal > 0 && _lastKnownTotal === 0) {
        clearInterval(_firstInvoicePollTimer);
        _firstInvoicePollTimer = null;
        toast("Your first invoice was processed! Check Slack for the approval card.", "success");
        await refreshAll();
      }
      _lastKnownTotal = newTotal;
    } catch (_) { /* ignore poll errors */ }
  }, 30000);
}

function stopFirstInvoicePoll() {
  if (_firstInvoicePollTimer) {
    clearInterval(_firstInvoicePollTimer);
    _firstInvoicePollTimer = null;
  }
}

// ==================== ACTIVITY PAGE ====================

let _activityPollTimer = null;

function activityPage() {
  const dash = state.bootstrap?.dashboard || {};
  const events = state.bootstrap?.recentActivity || [];

  return `
    <div class="kpi-row">
      <div class="kpi-card">
        <strong>${dash.total_invoices || 0}</strong>
        <span>Total invoices</span>
      </div>
      <div class="kpi-card kpi-warning">
        <strong>${dash.pending_approval || 0}</strong>
        <span>Pending approval</span>
      </div>
      <div class="kpi-card kpi-success">
        <strong>${dash.posted_today || 0}</strong>
        <span>Posted today</span>
      </div>
      <div class="kpi-card kpi-danger">
        <strong>${dash.rejected_today || 0}</strong>
        <span>Rejected today</span>
      </div>
    </div>

    <div class="kpi-row" style="margin-top:0">
      <div class="kpi-card">
        <strong>${dash.auto_approved_rate ? (dash.auto_approved_rate * 100).toFixed(0) + "%" : "—"}</strong>
        <span>Auto-approved</span>
      </div>
      <div class="kpi-card">
        <strong>${dash.avg_processing_time_hours ? dash.avg_processing_time_hours.toFixed(1) + "h" : "—"}</strong>
        <span>Avg processing</span>
      </div>
      <div class="kpi-card">
        <strong>$${(dash.total_amount_pending || 0).toLocaleString(undefined, {maximumFractionDigits: 0})}</strong>
        <span>Pending amount</span>
      </div>
      <div class="kpi-card kpi-success">
        <strong>$${(dash.total_amount_posted_today || 0).toLocaleString(undefined, {maximumFractionDigits: 0})}</strong>
        <span>Posted today</span>
      </div>
    </div>

    <div class="panel">
      <h3>Recent Activity</h3>
      ${events.length === 0
        ? '<p class="muted">No activity yet. Invoices will appear here once they start processing.</p>'
        : `<div class="activity-timeline">
            ${events.map((ev) => {
              const ts = ev.ts || ev.timestamp || "";
              const time = ts ? new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";
              const date = ts ? new Date(ts).toLocaleDateString([], { month: "short", day: "numeric" }) : "";
              const evType = ev.event_type || ev.new_state || "event";
              const badge = _eventBadge(evType);
              const vendor = ev.vendor_name || (ev.payload_json ? (typeof ev.payload_json === "string" ? JSON.parse(ev.payload_json) : ev.payload_json).vendor_name : "") || "";
              const amount = ev.amount || (ev.payload_json ? (typeof ev.payload_json === "string" ? JSON.parse(ev.payload_json) : ev.payload_json).amount : null);
              const amountStr = amount ? "$" + Number(amount).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "";
              return `<div class="activity-row">
                <div class="activity-time"><span class="activity-date">${date}</span> ${time}</div>
                <div class="activity-dot ${badge.cls}"></div>
                <div class="activity-body">
                  <span class="activity-vendor">${vendor}</span>
                  ${amountStr ? `<span class="activity-amount">${amountStr}</span>` : ""}
                  <span class="activity-badge ${badge.cls}">${badge.label}</span>
                </div>
              </div>`;
            }).join("")}
          </div>`
      }
      <div class="row" style="margin-top:10px">
        <button id="refresh-activity-btn" class="alt">Refresh</button>
      </div>
    </div>
  `;
}

function _eventBadge(eventType) {
  const t = (eventType || "").toLowerCase();
  if (t.includes("posted") || t.includes("closed")) return { label: "Posted", cls: "ev-posted" };
  if (t.includes("approved") || t.includes("auto_approved")) return { label: "Approved", cls: "ev-approved" };
  if (t.includes("rejected")) return { label: "Rejected", cls: "ev-rejected" };
  if (t.includes("needs_approval") || t.includes("pending")) return { label: "Pending review", cls: "ev-pending" };
  if (t.includes("received") || t.includes("classified")) return { label: "Received", cls: "ev-received" };
  if (t.includes("validated")) return { label: "Validated", cls: "ev-validated" };
  if (t.includes("failed") || t.includes("error")) return { label: "Error", cls: "ev-error" };
  return { label: eventType, cls: "" };
}

function _stateOf(item) {
  return String(item?.state || "").trim().toLowerCase();
}

function _buildOpsCandidates(worklist) {
  const items = Array.isArray(worklist) ? worklist : [];
  const retryFailed = items.filter((item) => _stateOf(item) === "failed_post");
  const nudgeApprovals = items.filter((item) => ["needs_approval", "pending_approval"].includes(_stateOf(item)));
  const routeLowRisk = items.filter((item) => {
    const state = _stateOf(item);
    const confidence = Number(item?.confidence || 0);
    const hasException = Boolean(item?.exception_code || item?.requires_field_review);
    return state === "validated" && confidence >= 0.95 && !hasException;
  });
  const retryRecoverable = retryFailed.filter((item) => {
    const lastError = String(item?.last_error || "").toLowerCase();
    return !lastError.includes("non_recoverable");
  });
  return {
    retryFailed,
    nudgeApprovals,
    routeLowRisk,
    retryRecoverable,
  };
}

function opsPage() {
  if (!hasOpsAccess()) {
    return `
      <div class="panel">
        <h3>Access required</h3>
        <p class="muted">Ops Console is limited to admin/operator roles.</p>
      </div>
    `;
  }

  const ops = state.bootstrap?.ops || {};
  const health = ops.health || {};
  const kpis = ops.kpis || {};
  const retryQueue = Array.isArray(ops.retryQueue) ? ops.retryQueue : [];
  const worklist = Array.isArray(ops.worklist) ? ops.worklist : [];
  const connectorReadiness = ops.connectorReadiness || {};
  const connectorRows = Array.isArray(connectorReadiness?.connectors) ? connectorReadiness.connectors : [];
  const connectorSummary = connectorReadiness?.summary || {};
  const learningSnapshot = ops.learningCalibration || {};
  const recentAudit = Array.isArray(state.bootstrap?.recentActivity) ? state.bootstrap.recentActivity : [];
  const candidates = _buildOpsCandidates(worklist);
  const queueLagMinutes = Number(health?.queue_lag?.avg_minutes || 0);
  const workflowStuck = Number(health?.workflow_stuck_count?.count || 0);
  const approvalLatencyMinutes = Number(health?.approval_latency?.avg_minutes || 0);
  const postingFailureRate = Number(health?.post_failure_rate?.rate_24h || 0) * 100;
  const topBlockers = Array.isArray(kpis?.agentic_telemetry?.top_blocker_reasons?.top_reasons)
    ? kpis.agentic_telemetry.top_blocker_reasons.top_reasons.slice(0, 3)
    : [];

  return `
    <div class="kpi-row">
      <div class="kpi-card">
        <strong>${queueLagMinutes.toFixed(1)}m</strong>
        <span>Queue lag (avg)</span>
      </div>
      <div class="kpi-card">
        <strong>${approvalLatencyMinutes.toFixed(1)}m</strong>
        <span>Approval latency (avg)</span>
      </div>
      <div class="kpi-card">
        <strong>${formatRate(postingFailureRate)}</strong>
        <span>Posting failure (24h)</span>
      </div>
      <div class="kpi-card ${workflowStuck > 0 ? "kpi-warning" : "kpi-success"}">
        <strong>${workflowStuck}</strong>
        <span>Stuck workflows</span>
      </div>
    </div>

    <div class="panel">
      <h3>Top blockers</h3>
      ${
        topBlockers.length
          ? `<ul>${topBlockers.map((entry) => `<li>${String(entry?.reason || "").replace(/_/g, " ")} (${Number(entry?.count || 0)})</li>`).join("")}</ul>`
          : '<p class="muted">No blocker telemetry available.</p>'
      }
    </div>

    <div class="panel">
      <h3>Batch operations</h3>
      <div class="row">
        <button data-ops-action="retry_failed_posts">Retry failed posts (${candidates.retryFailed.length})</button>
        <button data-ops-action="nudge_approvals" class="alt">Nudge approvals (${candidates.nudgeApprovals.length})</button>
        <button data-ops-action="route_low_risk" class="alt">Route low-risk (${candidates.routeLowRisk.length})</button>
        <button data-ops-action="retry_recoverable" class="alt">Recoverable retries (${candidates.retryRecoverable.length})</button>
      </div>
      <p class="muted">Batch actions are executed from Admin Ops only. Gmail remains decision-focused.</p>
    </div>

    <div class="panel">
      <h3>ERP connector readiness</h3>
      <p class="muted">
        Status: <strong>${connectorSummary.status || "not_verifiable"}</strong> ·
        Enabled ready: ${connectorSummary.enabled_connectors_ready || 0}/${connectorSummary.enabled_connectors_total || 0}
      </p>
      <table class="table">
        <thead><tr><th>Connector</th><th>Readiness</th><th>Checklist</th><th>Connected</th><th>Rollback</th></tr></thead>
        <tbody>
          ${
            connectorRows.length
              ? connectorRows.map((row) => `
                <tr>
                  <td>${row.erp_type}</td>
                  <td>${row.readiness_status}</td>
                  <td>${row.checklist_status}</td>
                  <td>${row.connection_present ? "yes" : "no"}</td>
                  <td>${row.rollback_blocked ? "blocked" : "ok"}</td>
                </tr>
              `).join("")
              : "<tr><td colspan='5'>No connector readiness data yet.</td></tr>"
          }
        </tbody>
      </table>
    </div>

    <div class="panel">
      <h3>Learning calibration</h3>
      <p class="muted">
        Status: <strong>${learningSnapshot.status || "not_calibrated"}</strong> ·
        Feedback: ${Number(learningSnapshot?.summary?.total_feedback || 0)}
      </p>
      <div class="row">
        <button id="recompute-learning-calibration-btn" class="alt">Recompute calibration</button>
      </div>
      ${
        Array.isArray(learningSnapshot?.recommendations) && learningSnapshot.recommendations.length
          ? `<ul>${learningSnapshot.recommendations.slice(0, 3).map((item) => `<li>${item}</li>`).join("")}</ul>`
          : '<p class="muted">No calibration recommendations yet.</p>'
      }
      <details style="margin-top:10px">
        <summary>Top vendor calibration gaps</summary>
        <pre>${JSON.stringify((learningSnapshot?.top_vendor_calibration_gaps || []).slice(0, 10), null, 2)}</pre>
      </details>
    </div>

    <div class="panel">
      <h3>Retry queue controls</h3>
      <table class="table">
        <thead><tr><th>Job</th><th>Status</th><th>Retry count</th><th>Next retry</th><th>Action</th></tr></thead>
        <tbody>
          ${
            retryQueue.length
              ? retryQueue.slice(0, 20).map((job) => `
                <tr>
                  <td>${job.id}</td>
                  <td>${job.status}</td>
                  <td>${job.retry_count || 0}/${job.max_retries || 0}</td>
                  <td>${job.next_retry_at || "-"}</td>
                  <td>
                    <button class="alt" data-retry-job="${job.id}">Retry</button>
                    <button class="alt" data-skip-job="${job.id}">Skip</button>
                  </td>
                </tr>
              `).join("")
              : "<tr><td colspan='5'>No retry jobs found.</td></tr>"
          }
        </tbody>
      </table>
    </div>

    <div class="panel">
      <h3>Debug tools</h3>
      <div class="row">
        <button id="ops-refresh-btn" class="alt">Refresh Ops data</button>
      </div>
      <details style="margin-top:10px">
        <summary>View raw agent/audit events</summary>
        <pre>${JSON.stringify(recentAudit.slice(0, 40), null, 2)}</pre>
      </details>
      <details style="margin-top:10px">
        <summary>Evidence viewer (worklist snapshot)</summary>
        <pre>${JSON.stringify(worklist.slice(0, 20), null, 2)}</pre>
      </details>
    </div>
  `;
}

// ==================== OTHER PAGES ====================

function integrationsPage() {
  const integrations = state.bootstrap?.integrations || [];
  const slack = integrationByName("slack");
  return `
    <div class="panel">
      <table class="table">
        <thead><tr><th>Integration</th><th>Status</th><th>Mode</th><th>Last sync</th></tr></thead>
        <tbody>
          ${integrations
            .map(
              (item) => `<tr>
                <td>${item.name}</td>
                <td>${item.status || "unknown"}</td>
                <td>${item.mode || "-"}</td>
                <td>${item.last_sync_at || "-"}</td>
              </tr>`
            )
            .join("")}
        </tbody>
      </table>
    </div>
    <div class="panel">
      <h3>Slack Setup</h3>
      <div class="row">
        <button id="slack-install-btn">Install to Slack</button>
        <input id="slack-channel-input" placeholder="#finance-approvals" value="${slack.approval_channel || ""}" />
        <button id="save-slack-channel-btn" class="alt">Save Channel</button>
        <button id="test-slack-btn" class="alt">Send Test Card</button>
      </div>
    </div>
  `;
}

function organizationPage() {
  const org = state.bootstrap?.organization || {};
  const settings = org.settings || {};
  return `
    <div class="panel">
      <h3>Organization</h3>
      <label>Name</label><input id="org-name-input" value="${org.name || ""}" />
      <label>Domain</label><input id="org-domain-input" value="${org.domain || ""}" />
      <label>Integration mode</label>
      <select id="org-mode-input">
        <option value="shared" ${org.integration_mode === "shared" ? "selected" : ""}>shared</option>
        <option value="per_org" ${org.integration_mode === "per_org" ? "selected" : ""}>per_org</option>
      </select>
      <div class="row">
        <button id="save-org-settings-btn">Save Organization</button>
      </div>
    </div>
    <div class="panel">
      <h3>Raw Settings JSON</h3>
      <textarea id="org-settings-json">${JSON.stringify(settings, null, 2)}</textarea>
      <div class="row">
        <button id="save-org-json-btn" class="alt">Save Settings JSON</button>
      </div>
    </div>
  `;
}

function policiesPage() {
  const policy = state.bootstrap?.policyPayload || {};
  return `
    <div class="panel">
      <h3>AP Policy (${policy.policy_name || "ap_business_v1"})</h3>
      <textarea id="policy-json">${JSON.stringify((policy.policy || {}).config_json || {}, null, 2)}</textarea>
      <div class="row">
        <button id="save-policy-btn">Save Policy</button>
      </div>
    </div>
  `;
}

function teamPage() {
  const invites = state.bootstrap?.teamInvites || [];
  return `
    <div class="panel">
      <h3>Invite Teammate</h3>
      <div class="row">
        <input id="invite-email" placeholder="teammate@company.com" />
        <select id="invite-role">
          <option value="member">member</option>
          <option value="admin">admin</option>
          <option value="viewer">viewer</option>
        </select>
        <button id="create-invite-btn">Create Invite</button>
      </div>
    </div>
    <div class="panel">
      <h3>Active Invites</h3>
      <table class="table">
        <thead><tr><th>Email</th><th>Role</th><th>Status</th><th>Link</th><th></th></tr></thead>
        <tbody>
          ${
            invites
              .map(
                (invite) => `<tr>
                  <td>${invite.email}</td>
                  <td>${invite.role}</td>
                  <td>${invite.status}</td>
                  <td><a href="${invite.invite_link}" target="_blank">Open</a></td>
                  <td>${invite.status === "pending" ? `<button data-revoke="${invite.id}" class="alt">Revoke</button>` : ""}</td>
                </tr>`
              )
              .join("") || "<tr><td colspan='5'>No invites yet.</td></tr>"
          }
        </tbody>
      </table>
    </div>
  `;
}

function planPage() {
  const sub = state.bootstrap?.subscription || {};
  return `
    <div class="panel">
      <h3>Plan</h3>
      <p><strong>${(sub.plan || "free").toUpperCase()}</strong> (${sub.status || "active"})</p>
      <div class="row">
        <button data-plan="free" class="alt">Free</button>
        <button data-plan="trial" class="alt">Trial</button>
        <button data-plan="pro">Pro</button>
        <button data-plan="enterprise">Enterprise</button>
      </div>
      <p class="muted">Stripe billing portal is deferred; this release includes plan controls and usage visibility.</p>
    </div>
    <div class="panel">
      <h3>Usage</h3>
      <pre>${JSON.stringify(sub.usage || {}, null, 2)}</pre>
    </div>
  `;
}

function healthPage() {
  const health = state.bootstrap?.health || {};
  return `
    <div class="panel">
      <h3>Diagnostics</h3>
      <pre>${JSON.stringify(health.integrations || {}, null, 2)}</pre>
    </div>
    <div class="panel">
      <h3>Required actions</h3>
      <ul>
        ${(health.required_actions || []).map((item) => `<li>${item.message}</li>`).join("") || "<li>None</li>"}
      </ul>
    </div>
  `;
}

// ==================== RENDER ====================

function renderPage() {
  const page = PAGES.find((item) => item.id === state.activePage) || PAGES[0];
  qs("#page-title").textContent = page.title;
  qs("#page-subtitle").textContent = page.subtitle;
  let html = "";
  if (page.id === "setup") html = setupPage();
  if (page.id === "activity") html = activityPage();
  if (page.id === "ops") html = opsPage();
  if (page.id === "integrations") html = integrationsPage();
  if (page.id === "organization") html = organizationPage();
  if (page.id === "policies") html = policiesPage();
  if (page.id === "team") html = teamPage();
  if (page.id === "plan") html = planPage();
  if (page.id === "health") html = healthPage();
  qs("#page-content").innerHTML = html;
  // Start/stop first-invoice celebration polling based on active page
  if (page.id === "setup") {
    startFirstInvoicePoll();
  } else {
    stopFirstInvoicePoll();
  }
  bindPageEvents();
}

// ==================== DATA LOADING ====================

async function loadBootstrap() {
  const data = await api(`/api/admin/bootstrap?organization_id=${encodeURIComponent(state.orgId)}`);
  state.bootstrap = data;
  qs("#org-chip").textContent = `${data.organization.name} (${data.organization.id})`;
}

async function refreshAll() {
  await loadBootstrap();

  // Load activity data in parallel
  const [policyResult, invitesResult, auditResult] = await Promise.allSettled([
    api(`/api/admin/policies/ap?organization_id=${encodeURIComponent(state.orgId)}`),
    api(`/api/admin/team/invites?organization_id=${encodeURIComponent(state.orgId)}`),
    api(`/api/ap/audit/recent?organization_id=${encodeURIComponent(state.orgId)}&limit=30`),
  ]);

  state.bootstrap.policyPayload = policyResult.status === "fulfilled" ? policyResult.value : {};
  state.bootstrap.teamInvites = invitesResult.status === "fulfilled" ? (invitesResult.value.invites || []) : [];
  state.bootstrap.recentActivity = auditResult.status === "fulfilled" ? (auditResult.value.events || auditResult.value || []) : [];

  const opsState = {
    health: {},
    kpis: {},
    retryQueue: [],
    worklist: [],
    connectorReadiness: {},
    learningCalibration: {},
    errors: {},
  };
  if (hasOpsAccess()) {
    const org = encodeURIComponent(state.orgId);
    const [healthResult, kpisResult, retryQueueResult, worklistResult, connectorReadinessResult, learningCalibrationResult] = await Promise.allSettled([
      api(`/api/ops/tenant-health?organization_id=${org}`),
      api(`/api/ops/ap-kpis?organization_id=${org}`),
      api(`/api/ops/retry-queue?organization_id=${org}&status=all&limit=200`),
      api(`/extension/worklist?organization_id=${org}`),
      api(`/api/admin/ops/connector-readiness?organization_id=${org}`),
      api(`/api/admin/ops/learning-calibration?organization_id=${org}`),
    ]);
    if (healthResult.status === "fulfilled") {
      opsState.health = healthResult.value?.health || {};
    } else {
      opsState.errors.health = String(healthResult.reason || "");
    }
    if (kpisResult.status === "fulfilled") {
      opsState.kpis = kpisResult.value?.kpis || {};
    } else {
      opsState.errors.kpis = String(kpisResult.reason || "");
    }
    if (retryQueueResult.status === "fulfilled") {
      opsState.retryQueue = Array.isArray(retryQueueResult.value?.jobs) ? retryQueueResult.value.jobs : [];
    } else {
      opsState.errors.retryQueue = String(retryQueueResult.reason || "");
    }
    if (worklistResult.status === "fulfilled") {
      opsState.worklist = Array.isArray(worklistResult.value?.items) ? worklistResult.value.items : [];
    } else {
      opsState.errors.worklist = String(worklistResult.reason || "");
    }
    if (connectorReadinessResult.status === "fulfilled") {
      opsState.connectorReadiness = connectorReadinessResult.value?.connector_readiness || {};
    } else {
      opsState.errors.connectorReadiness = String(connectorReadinessResult.reason || "");
    }
    if (learningCalibrationResult.status === "fulfilled") {
      opsState.learningCalibration = learningCalibrationResult.value?.snapshot || {};
    } else {
      opsState.errors.learningCalibration = String(learningCalibrationResult.reason || "");
    }
  }
  state.bootstrap.ops = opsState;

  renderNav();
  renderPage();
}

async function runOpsBatchAction(action) {
  const op = String(action || "").trim();
  const ops = state.bootstrap?.ops || {};
  const worklist = Array.isArray(ops.worklist) ? ops.worklist : [];
  const candidates = _buildOpsCandidates(worklist);
  let selected = [];
  if (op === "retry_failed_posts") selected = candidates.retryFailed;
  if (op === "nudge_approvals") selected = candidates.nudgeApprovals;
  if (op === "route_low_risk") selected = candidates.routeLowRisk;
  if (op === "retry_recoverable") selected = candidates.retryRecoverable;
  if (!selected.length) {
    toast("No matching invoices for this batch action.", "error");
    return;
  }

  const org = encodeURIComponent(state.orgId);
  const limit = 25;
  const bounded = selected.slice(0, limit);
  let success = 0;
  let failed = 0;
  const failureReasons = [];
  for (const item of bounded) {
    try {
      if (op === "retry_failed_posts") {
        const apItemId = encodeURIComponent(String(item?.id || "").trim());
        if (!apItemId) {
          failed += 1;
          failureReasons.push("missing_item_id");
          continue;
        }
        await api(`/api/ap/items/${apItemId}/retry-post?organization_id=${org}`, {
          method: "POST",
        });
      } else if (op === "nudge_approvals") {
        const emailId = resolveInvoiceReference(item);
        if (!emailId) {
          failed += 1;
          failureReasons.push("missing_email_reference");
          continue;
        }
        await api("/extension/approval-nudge", {
          method: "POST",
          body: JSON.stringify({
            email_id: emailId,
            organization_id: state.orgId,
            user_email: currentUserEmail(),
            message: "Admin Ops nudge for pending approval",
          }),
        });
      } else if (op === "route_low_risk") {
        const emailId = resolveInvoiceReference(item);
        if (!emailId) {
          failed += 1;
          failureReasons.push("missing_email_reference");
          continue;
        }
        await api("/extension/route-low-risk-approval", {
          method: "POST",
          body: JSON.stringify({
            email_id: emailId,
            organization_id: state.orgId,
            user_email: currentUserEmail(),
            reason: "admin_ops_batch_route_low_risk",
          }),
        });
      } else if (op === "retry_recoverable") {
        const emailId = resolveInvoiceReference(item);
        if (!emailId) {
          failed += 1;
          failureReasons.push("missing_email_reference");
          continue;
        }
        await api("/extension/retry-recoverable-failure", {
          method: "POST",
          body: JSON.stringify({
            email_id: emailId,
            organization_id: state.orgId,
            user_email: currentUserEmail(),
            reason: "admin_ops_batch_retry_recoverable",
          }),
        });
      }
      success += 1;
    } catch (error) {
      failed += 1;
      failureReasons.push(String(error?.message || "request_failed"));
    }
  }
  const clippedReason = failureReasons.find(Boolean) || "";
  const suffix = failed > 0 ? ` (${failed} failed${clippedReason ? `: ${clippedReason}` : ""})` : "";
  toast(`Batch action complete: ${success} succeeded${suffix}`, failed > 0 ? "error" : "success");
}

// ==================== EVENT BINDING ====================

async function bindPageEvents() {
  const opsRefreshBtn = qs("#ops-refresh-btn");
  if (opsRefreshBtn) {
    opsRefreshBtn.onclick = async () => {
      opsRefreshBtn.disabled = true;
      opsRefreshBtn.textContent = "Refreshing...";
      await refreshAll();
    };
  }

  document.querySelectorAll("[data-ops-action]").forEach((button) => {
    button.onclick = async () => {
      if (!hasOpsAccess()) {
        toast("Ops access required.", "error");
        return;
      }
      const op = button.getAttribute("data-ops-action");
      const original = button.textContent;
      button.disabled = true;
      button.textContent = "Running...";
      await runOpsBatchAction(op);
      await refreshAll();
      button.disabled = false;
      button.textContent = original;
    };
  });

  const recomputeLearningBtn = qs("#recompute-learning-calibration-btn");
  if (recomputeLearningBtn) {
    recomputeLearningBtn.onclick = async () => {
      if (!hasOpsAccess()) {
        toast("Ops access required.", "error");
        return;
      }
      recomputeLearningBtn.disabled = true;
      recomputeLearningBtn.textContent = "Recomputing...";
      try {
        await api("/api/admin/ops/learning-calibration/recompute", {
          method: "POST",
          body: JSON.stringify({
            organization_id: state.orgId,
            window_days: 180,
            min_feedback: 20,
            limit: 5000,
          }),
        });
        toast("Learning calibration recomputed.", "success");
      } catch (error) {
        toast(`Calibration recompute failed: ${error.message}`, "error");
      }
      await refreshAll();
    };
  }

  document.querySelectorAll("[data-retry-job]").forEach((button) => {
    button.onclick = async () => {
      if (!hasOpsAccess()) {
        toast("Ops access required.", "error");
        return;
      }
      const jobId = String(button.getAttribute("data-retry-job") || "").trim();
      if (!jobId) return;
      button.disabled = true;
      await api(`/api/ops/retry-queue/${encodeURIComponent(jobId)}/retry`, { method: "POST" });
      toast(`Retry requested for job ${jobId}.`, "success");
      await refreshAll();
    };
  });

  document.querySelectorAll("[data-skip-job]").forEach((button) => {
    button.onclick = async () => {
      if (!hasOpsAccess()) {
        toast("Ops access required.", "error");
        return;
      }
      const jobId = String(button.getAttribute("data-skip-job") || "").trim();
      if (!jobId) return;
      button.disabled = true;
      await api(`/api/ops/retry-queue/${encodeURIComponent(jobId)}/skip`, { method: "POST" });
      toast(`Retry job ${jobId} marked as skipped.`, "success");
      await refreshAll();
    };
  });

  // Refresh activity
  const refreshActivityBtn = qs("#refresh-activity-btn");
  if (refreshActivityBtn) {
    refreshActivityBtn.onclick = async () => {
      refreshActivityBtn.textContent = "Loading...";
      refreshActivityBtn.disabled = true;
      await refreshAll();
    };
  }

  // Gmail connect
  const connectGmailBtn = qs("#connect-gmail-btn");
  if (connectGmailBtn) {
    connectGmailBtn.onclick = async () => {
      connectGmailBtn.disabled = true;
      connectGmailBtn.textContent = "Connecting...";
      try {
        const payload = await api("/api/admin/integrations/gmail/connect/start", {
          method: "POST",
          body: JSON.stringify({
            organization_id: state.orgId,
            redirect_path: `/console?org=${encodeURIComponent(state.orgId)}&page=integrations`,
          }),
        });
        if (!payload?.auth_url) throw new Error("gmail_auth_url_missing");
        window.location.href = payload.auth_url;
      } catch (err) {
        toast(`Failed to start Gmail connection: ${err.message}`, "error");
        connectGmailBtn.disabled = false;
        connectGmailBtn.textContent = "Connect Gmail";
      }
    };
  }

  // Slack connect (setup page + integrations page)
  const connectSlackBtn = qs("#connect-slack-btn");
  const slackConnectHandler = async () => {
    const payload = await api("/api/admin/integrations/slack/install/start", {
      method: "POST",
      body: JSON.stringify({ organization_id: state.orgId, mode: "per_org", redirect_path: "/console" }),
    });
    window.location.href = payload.auth_url;
  };
  if (connectSlackBtn) connectSlackBtn.onclick = slackConnectHandler;

  const slackInstallBtn = qs("#slack-install-btn");
  if (slackInstallBtn) slackInstallBtn.onclick = slackConnectHandler;

  // ERP connect buttons (QuickBooks, Xero)
  document.querySelectorAll(".erp-connect-btn").forEach((btn) => {
    btn.onclick = async () => {
      const erpType = btn.getAttribute("data-erp");
      btn.textContent = "Connecting...";
      btn.disabled = true;
      try {
        const payload = await api("/api/admin/integrations/erp/connect/start", {
          method: "POST",
          body: JSON.stringify({ organization_id: state.orgId, erp_type: erpType }),
        });
        if (payload.method === "oauth") {
          window.location.href = payload.auth_url;
        }
      } catch (err) {
        toast(`Failed to start ${erpType} connection: ${err.message}`, "error");
        btn.textContent = "Connect";
        btn.disabled = false;
      }
    };
  });

  // NetSuite setup toggle
  const nsSetupBtn = qs("#netsuite-setup-btn");
  if (nsSetupBtn) {
    nsSetupBtn.onclick = () => {
      state.netsuiteFormVisible = true;
      const panel = qs("#netsuite-form-panel");
      if (panel) panel.classList.remove("hidden");
    };
  }

  // NetSuite cancel
  const nsCancelBtn = qs("#ns-cancel-btn");
  if (nsCancelBtn) {
    nsCancelBtn.onclick = () => {
      state.netsuiteFormVisible = false;
      const panel = qs("#netsuite-form-panel");
      if (panel) panel.classList.add("hidden");
    };
  }

  // NetSuite connect
  const nsConnectBtn = qs("#ns-connect-btn");
  if (nsConnectBtn) {
    nsConnectBtn.onclick = async () => {
      const payload = {
        organization_id: state.orgId,
        account_id: qs("#ns-account-id").value.trim(),
        consumer_key: qs("#ns-consumer-key").value.trim(),
        consumer_secret: qs("#ns-consumer-secret").value.trim(),
        token_id: qs("#ns-token-id").value.trim(),
        token_secret: qs("#ns-token-secret").value.trim(),
      };
      if (!payload.account_id || !payload.consumer_key || !payload.token_id) {
        toast("Please fill in all required fields.", "error");
        return;
      }
      nsConnectBtn.textContent = "Testing connection...";
      nsConnectBtn.disabled = true;
      try {
        await api("/erp/netsuite/connect", { method: "POST", body: JSON.stringify(payload) });
        toast("NetSuite connected!", "success");
        state.netsuiteFormVisible = false;
        await refreshAll();
      } catch (err) {
        toast(`NetSuite connection failed: ${err.message}`, "error");
        nsConnectBtn.textContent = "Test & Connect";
        nsConnectBtn.disabled = false;
      }
    };
  }

  // SAP setup toggle
  const sapSetupBtn = qs("#sap-setup-btn");
  if (sapSetupBtn) {
    sapSetupBtn.onclick = () => {
      state.sapFormVisible = true;
      const panel = qs("#sap-form-panel");
      if (panel) panel.classList.remove("hidden");
    };
  }

  // SAP cancel
  const sapCancelBtn = qs("#sap-cancel-btn");
  if (sapCancelBtn) {
    sapCancelBtn.onclick = () => {
      state.sapFormVisible = false;
      const panel = qs("#sap-form-panel");
      if (panel) panel.classList.add("hidden");
    };
  }

  // SAP connect
  const sapConnectBtn = qs("#sap-connect-btn");
  if (sapConnectBtn) {
    sapConnectBtn.onclick = async () => {
      const payload = {
        organization_id: state.orgId,
        base_url: qs("#sap-base-url").value.trim(),
        username: qs("#sap-username").value.trim(),
        password: qs("#sap-password").value.trim(),
      };
      if (!payload.base_url || !payload.username || !payload.password) {
        toast("Please fill in all SAP fields.", "error");
        return;
      }
      sapConnectBtn.textContent = "Testing connection...";
      sapConnectBtn.disabled = true;
      try {
        await api("/api/admin/integrations/erp/connect/sap", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        toast("SAP connected!", "success");
        state.sapFormVisible = false;
        await refreshAll();
      } catch (err) {
        toast(`SAP connection failed: ${err.message}`, "error");
      } finally {
        sapConnectBtn.textContent = "Test & Connect";
        sapConnectBtn.disabled = false;
      }
    };
  }

  // Save Slack channel
  const saveSlackChannel = qs("#save-slack-channel-btn");
  if (saveSlackChannel && !saveSlackChannel.disabled) {
    saveSlackChannel.onclick = async () => {
      const channel = qs("#slack-channel-input").value.trim();
      await api("/api/admin/integrations/slack/channel", {
        method: "POST",
        body: JSON.stringify({ organization_id: state.orgId, channel_id: channel }),
      });
      toast("Approval channel saved.");
      await refreshAll();
    };
  }

  // Test Slack
  const testSlackBtn = qs("#test-slack-btn");
  if (testSlackBtn) {
    testSlackBtn.onclick = async () => {
      const channel = qs("#slack-channel-input").value.trim();
      await api("/api/admin/integrations/slack/test", {
        method: "POST",
        body: JSON.stringify({ organization_id: state.orgId, channel_id: channel }),
      });
      toast("Test message sent to Slack.");
    };
  }

  const openPoliciesBtn = qs("#open-policies-page-btn");
  if (openPoliciesBtn) {
    openPoliciesBtn.onclick = () => {
      state.activePage = "policies";
      renderNav();
      renderPage();
    };
  }

  // Launch button
  const launchBtn = qs("#launch-btn");
  if (launchBtn && !launchBtn.disabled) {
    launchBtn.onclick = async () => {
      await api("/api/admin/onboarding/step", {
        method: "POST",
        body: JSON.stringify({ organization_id: state.orgId, step: 5 }),
      });
      toast("Clearledgr is live! Invoices will now be processed automatically.", "success");
      await refreshAll();
    };
  }

  // Mark step complete (legacy)
  const markStepBtn = qs("#mark-step5-btn");
  if (markStepBtn) {
    markStepBtn.onclick = async () => {
      await api("/api/admin/onboarding/step", {
        method: "POST",
        body: JSON.stringify({ organization_id: state.orgId, step: 5 }),
      });
      await refreshAll();
    };
  }

  // Save org settings
  const saveOrgBtn = qs("#save-org-settings-btn");
  if (saveOrgBtn) {
    saveOrgBtn.onclick = async () => {
      await api("/api/admin/org/settings", {
        method: "PATCH",
        body: JSON.stringify({
          organization_id: state.orgId,
          patch: {
            organization_name: qs("#org-name-input").value.trim(),
            domain: qs("#org-domain-input").value.trim(),
            integration_mode: qs("#org-mode-input").value,
          },
        }),
      });
      toast("Organization settings saved.");
      await refreshAll();
    };
  }

  // Save org JSON
  const saveOrgJsonBtn = qs("#save-org-json-btn");
  if (saveOrgJsonBtn) {
    saveOrgJsonBtn.onclick = async () => {
      const patch = JSON.parse(qs("#org-settings-json").value);
      await api("/api/admin/org/settings", {
        method: "PATCH",
        body: JSON.stringify({ organization_id: state.orgId, patch }),
      });
      toast("Settings JSON saved.");
      await refreshAll();
    };
  }

  // Save policy
  const savePolicyBtn = qs("#save-policy-btn");
  if (savePolicyBtn) {
    savePolicyBtn.onclick = async () => {
      const config = JSON.parse(qs("#policy-json").value);
      await api("/api/admin/policies/ap", {
        method: "PUT",
        body: JSON.stringify({
          organization_id: state.orgId,
          config,
          enabled: true,
        }),
      });
      toast("Policy updated.");
      await refreshAll();
    };
  }

  // Team invites
  const createInviteBtn = qs("#create-invite-btn");
  if (createInviteBtn) {
    createInviteBtn.onclick = async () => {
      const email = qs("#invite-email").value.trim();
      const role = qs("#invite-role").value;
      const payload = await api("/api/admin/team/invites", {
        method: "POST",
        body: JSON.stringify({ organization_id: state.orgId, email, role }),
      });
      toast(`Invite sent to ${email}.`);
      await refreshAll();
    };
  }

  document.querySelectorAll("[data-revoke]").forEach((button) => {
    button.onclick = async () => {
      await api(`/api/admin/team/invites/${button.getAttribute("data-revoke")}/revoke?organization_id=${encodeURIComponent(state.orgId)}`, {
        method: "POST",
      });
      toast("Invite revoked.");
      await refreshAll();
    };
  });

  // Plan buttons
  document.querySelectorAll("[data-plan]").forEach((button) => {
    button.onclick = async () => {
      await api("/api/admin/subscription/plan", {
        method: "PATCH",
        body: JSON.stringify({ organization_id: state.orgId, plan: button.getAttribute("data-plan") }),
      });
      toast(`Plan updated to ${button.getAttribute("data-plan")}.`);
      await refreshAll();
    };
  });
}

// ==================== AUTH ====================

async function submitLogin(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const email = String(form.get("email") || "").trim();
  const password = String(form.get("password") || "").trim();
  const login = await api("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
    headers: {},
  });
  if (!login?.access_token) {
    throw new Error("Login did not return an access token.");
  }
  state.orgId = params().get("org") || state.orgId;
  localStorage.setItem("cl_admin_org", state.orgId);
  showConsole();
  await refreshAll();
}

async function submitInviteAccept(event) {
  event.preventDefault();
  if (!state.inviteToken) {
    toast("Invite token missing.", "error");
    return;
  }
  const form = new FormData(event.currentTarget);
  const name = String(form.get("name") || "").trim();
  const password = String(form.get("password") || "").trim();
  const payload = await api("/auth/invites/accept", {
    method: "POST",
    body: JSON.stringify({
      token: state.inviteToken,
      name: name || null,
      password: password || null,
    }),
    headers: {},
  });
  if (!payload?.access_token) {
    throw new Error("Invite accept did not return an access token.");
  }
  state.orgId = payload?.user?.organization_id || state.orgId;
  localStorage.setItem("cl_admin_org", state.orgId);
  state.inviteToken = null;
  cleanUrlParams(["invite_token"]);
  showConsole();
  await refreshAll();
}

// ==================== BOOT ====================

async function boot() {
  const url = params();
  state.inviteToken = url.get("invite_token");
  const requestedPage = String(url.get("page") || "").trim().toLowerCase();
  if (requestedPage && pageExists(requestedPage)) {
    state.activePage = requestedPage;
  }
  const authCode = url.get("auth_code");
  if (authCode) {
    try {
      const exchange = await api("/auth/google/exchange", {
        method: "POST",
        body: JSON.stringify({ auth_code: authCode }),
        headers: {},
      });
      if (!exchange?.access_token) {
        throw new Error("Google exchange did not return access token");
      }
      cleanUrlParams(["auth_code"]);
    } catch (error) {
      cleanUrlParams(["auth_code"]);
      console.error(error);
      clearSession();
      showAuth("Google sign-in session expired. Please sign in again.");
      return;
    }
  } else if (url.get("token") || url.get("refresh_token")) {
    cleanUrlParams(["token", "refresh_token"]);
  }

  state.orgId = url.get("org") || localStorage.getItem("cl_admin_org") || "default";
  localStorage.setItem("cl_admin_org", state.orgId);

  // Handle post-OAuth redirects
  const connected = url.get("connected");
  const erpError = url.get("erp_error");
  if (connected) {
    cleanUrlParams(["connected", "org"]);
    setTimeout(() => toast(`${connected.charAt(0).toUpperCase() + connected.slice(1)} connected!`, "success"), 500);
  }
  if (erpError) {
    cleanUrlParams(["erp_error"]);
    setTimeout(() => toast(`Connection error: ${erpError}`, "error"), 500);
  }

  qs("#login-form").addEventListener("submit", submitLogin);
  qs("#invite-form").addEventListener("submit", submitInviteAccept);
  qs("#logout-btn").addEventListener("click", () => {
    api("/auth/logout", { method: "POST" }).catch(() => {});
    clearSession();
    showAuth("Signed out.");
  });
  qs("#google-login-btn").addEventListener("click", async () => {
    const invitePart = state.inviteToken
      ? `&invite_token=${encodeURIComponent(state.inviteToken)}`
      : "";
    const payload = await api(
      `/auth/google/start?organization_id=${encodeURIComponent(state.orgId)}&redirect_path=${encodeURIComponent("/console")}${invitePart}`,
      { headers: {} }
    );
    window.location.href = payload.auth_url;
  });

  try {
    showConsole();
    await refreshAll();
  } catch (error) {
    console.error(error);
    clearSession();
    showAuth("Session expired. Sign in again.");
  }
}

boot();
