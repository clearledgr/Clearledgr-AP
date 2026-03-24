/**
 * Workspace shell API adapter — wraps queueManager.backendFetch() with the same
 * api(path, options) interface as the standalone support shell.
 *
 * Page components ported from static/workspace/app.js use props.api() for
 * all backend calls. This adapter provides Bearer token auth (no cookies/CSRF).
 */

let _toastFn = null;

export function setToastFn(fn) { _toastFn = fn; }

export function createWorkspaceShellApi(queueManager) {
  const orgId = () => String(queueManager?.runtimeConfig?.organizationId || 'default').trim();
  const backendUrl = () => String(queueManager?.runtimeConfig?.backendUrl || 'http://127.0.0.1:8010').replace(/\/+$/, '');

  async function api(path, options = {}) {
    const fullUrl = `${backendUrl()}${path}`;
    const result = await queueManager.backendFetch(fullUrl, {
      method: options.method || 'GET',
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
      body: options.body || undefined,
    });

    if (!result || !result.ok) {
      const text = await result?.text?.().catch(() => '') || '';
      const err = new Error(text || `HTTP ${result?.status || 'unknown'}`);
      err.status = result?.status;
      if (!options.silent) _toastFn?.(`Request failed: ${err.message}`, 'error');
      throw err;
    }

    if (result.status === 204) return {};
    return result.json();
  }

  function toast(msg, type = 'info') {
    _toastFn?.(msg, type);
  }

  async function bootstrapWorkspaceShellData() {
    const id = orgId();
    const [bootstrap, policies, team] = await Promise.allSettled([
      api(`/api/workspace/bootstrap?organization_id=${id}`, { silent: true }).catch(() => ({})),
      api(`/api/workspace/policies/ap?organization_id=${id}`, { silent: true }).catch(() => ({})),
      api(`/api/workspace/team/invites?organization_id=${id}`, { silent: true }).catch(() => []),
    ]);

    const bootstrapPayload = bootstrap.value || {};
    const dashboard = bootstrapPayload.dashboard || {};
    const integrations = Array.isArray(bootstrapPayload.integrations) ? bootstrapPayload.integrations : [];
    const organization = bootstrapPayload.organization || {};
    const health = bootstrapPayload.health || {};
    const subscription = bootstrapPayload.subscription || {};
    const currentUser = bootstrapPayload.current_user || {};
    const capabilities = bootstrapPayload.capabilities || currentUser.capabilities || {};

    return {
      dashboard,
      integrations,
      policyPayload: policies.value || {},
      teamInvites: Array.isArray(team.value) ? team.value : [],
      organization,
      health,
      subscription,
      recentActivity: dashboard.recent_activity || [],
      required_actions: Array.isArray(bootstrapPayload.required_actions) ? bootstrapPayload.required_actions : [],
      capabilities,
      current_user: currentUser,
    };
  }

  return { api, toast, orgId, bootstrapWorkspaceShellData };
}
