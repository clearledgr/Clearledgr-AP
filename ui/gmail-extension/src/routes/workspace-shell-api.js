/**
 * Workspace shell API adapter — wraps queueManager.backendFetch() with the same
 * api(path, options) interface as the standalone support shell.
 *
 * Page components ported from static/workspace/app.js use props.api() for
 * all backend calls. This adapter provides Bearer token auth (no cookies/CSRF).
 */
import { getFallbackCapabilities } from '../utils/capabilities.js';

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
    let bootstrapStatus = 0;
    const [bootstrap, policies, team] = await Promise.allSettled([
      api(`/api/workspace/bootstrap?organization_id=${id}`, { silent: true }).catch((err) => {
        bootstrapStatus = err?.status || 0;
        return {};
      }),
      api(`/api/workspace/policies/ap?organization_id=${id}`, { silent: true }).catch(() => ({})),
      api(`/api/workspace/team/invites?organization_id=${id}`, { silent: true }).catch(() => []),
    ]);

    const bootstrapPayload = bootstrap.value || {};
    // §15 Streak flow: a 401 bootstrap (fresh install, no session yet) means
    // the Streak-style OnboardingFlow modal should be shown — its first step
    // IS "Sign in with Google". Surface that state so inboxsdk-layer can
    // trigger the modal. Don't infer auth state from the payload shape —
    // use the explicit HTTP status.
    const needsAuth = bootstrapStatus === 401 || bootstrapStatus === 403;
    const dashboard = bootstrapPayload.dashboard || {};
    const integrations = Array.isArray(bootstrapPayload.integrations) ? bootstrapPayload.integrations : [];
    const organization = bootstrapPayload.organization || {};
    const health = bootstrapPayload.health || {};
    const subscription = bootstrapPayload.subscription || {};
    const currentUserPayload = bootstrapPayload.current_user || {};
    const fallbackRole = String(currentUserPayload.role || queueManager?.currentUserRole || '').trim().toLowerCase();
    const currentUser = {
      ...currentUserPayload,
      role: currentUserPayload.role || fallbackRole || undefined,
      email: currentUserPayload.email || queueManager?.runtimeConfig?.userEmail || '',
    };
    const explicitCapabilities = bootstrapPayload.capabilities || currentUser.capabilities || {};
    const capabilities = Object.keys(explicitCapabilities).length > 0
      ? explicitCapabilities
      : getFallbackCapabilities(fallbackRole);

    // Preserve onboarding state from bootstrap if present; otherwise,
    // if bootstrap returned 401/403 (unauthenticated), emit a synthetic
    // onboarding state so the modal fires on fresh install.
    const onboarding = bootstrapPayload.onboarding
      || (needsAuth ? { completed: false, needs_auth: true, step: 0 } : undefined);

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
      onboarding,
      needs_auth: needsAuth,
    };
  }

  return { api, toast, orgId, bootstrapWorkspaceShellData };
}
