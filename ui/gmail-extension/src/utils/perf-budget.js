/**
 * Frontend performance budgets — DESIGN_THESIS.md §4.07.
 *
 * The thesis defines four pass/fail performance criteria for Clearledgr's
 * Gmail surfaces:
 *
 *   Sidebar      — loads within 2 seconds of thread open
 *   Kanban       — first paint completes in under 1 second
 *   Home         — fully interactive before AP Manager reaches for mouse (~1s)
 *   Inbox labels — render before user finishes reading the subject line (~500ms)
 *
 * These are the budgets that govern whether a surface ships. Backend SLAs
 * are tracked separately in clearledgr/core/sla_tracker.py. This file owns
 * the frontend half — the budgets were drift before: the thesis called
 * them "pass/fail criteria" but nothing measured them in the bundle.
 *
 * Measurement strategy: each surface marks a start boundary when it begins
 * mounting or rendering, and a done boundary when it reaches its interactive
 * target. On done we log to console (warn on breach) and fire a
 * fire-and-forget POST to /api/ui/perf so breaches show up in backend
 * telemetry. The POST is best-effort — failing it never blocks the UI.
 *
 * Budgets are deliberately hard-coded. Knob turns are a product decision,
 * not a runtime-config decision — if we need to change what "fast enough"
 * means we should be doing it in a PR, not a settings toggle.
 */

export const PERF_BUDGETS_MS = {
  sidebar: 2000,
  kanban: 1000,
  home: 1000,
  inbox_labels: 500,
};

const _activeMarks = new Map();

/**
 * Mark the start of a measured surface. Call when the surface begins
 * mounting. If a start is already recorded under the same key it is
 * overwritten — the most recent mount wins (useful when the user
 * re-opens the same surface without leaving Gmail).
 */
export function perfMarkStart(key) {
  if (!key) return;
  try {
    _activeMarks.set(key, (performance && performance.now) ? performance.now() : Date.now());
  } catch (_) {
    _activeMarks.set(key, Date.now());
  }
}

/**
 * Mark the done boundary for a measured surface. Logs a console line and
 * fires the telemetry beacon. Returns the measured latency in ms so the
 * caller can use it for debug panels or tests.
 */
export function perfMarkDone(key, { context } = {}) {
  if (!key) return null;
  const started = _activeMarks.get(key);
  if (started == null) return null;
  _activeMarks.delete(key);

  let now;
  try {
    now = (performance && performance.now) ? performance.now() : Date.now();
  } catch (_) {
    now = Date.now();
  }
  const latencyMs = Math.max(0, Math.round(now - started));
  const budget = PERF_BUDGETS_MS[key];
  const breached = budget != null && latencyMs > budget;

  const line = `[cl.perf] ${key}=${latencyMs}ms${budget != null ? ` (budget ${budget}ms)` : ''}${breached ? ' — BREACH' : ''}`;
  try {
    if (breached) console.warn(line, context || undefined);
    else if (console.debug) console.debug(line);
  } catch (_) {}

  _reportPerf({ surface: key, latency_ms: latencyMs, budget_ms: budget || 0, breached, context: context || null });
  return latencyMs;
}

async function _reportPerf(payload) {
  // Best-effort beacon. Uses sendBeacon when available (survives unload
  // better than fetch), falls back to fetch with keepalive. Never throws.
  try {
    const backendUrl = await _resolveBackendUrl();
    if (!backendUrl) return;
    const body = JSON.stringify(payload);
    const url = `${backendUrl}/api/ui/perf`;

    if (typeof navigator !== 'undefined' && navigator.sendBeacon) {
      const blob = new Blob([body], { type: 'application/json' });
      navigator.sendBeacon(url, blob);
      return;
    }
    fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
      keepalive: true,
    }).catch(() => {});
  } catch (_) {}
}

async function _resolveBackendUrl() {
  // Sidebar already has queueManager.backendFetch with a resolved backend
  // URL, but the perf util is used from surfaces that don't all have
  // access to it. Read the cached backend URL from chrome.storage.local
  // (written at signin by background.js). If unavailable, skip silently.
  try {
    if (typeof chrome === 'undefined' || !chrome?.storage?.local) return null;
    const { cl_backend_url } = await chrome.storage.local.get(['cl_backend_url']);
    return cl_backend_url || null;
  } catch (_) {
    return null;
  }
}
