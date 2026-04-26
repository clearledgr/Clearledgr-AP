/** Reactive store — replaces 15+ module-level let variables with a single observable state */

import { useState, useEffect } from 'preact/hooks';

const _listeners = new Set();

/** Preact hook: subscribe a component to store updates.
 *
 * Any call to `store.update(...)` after mount will cause every
 * subscribed component to re-render. Returns the store object so
 * callers read directly (e.g. `const s = useStore(); s.llmBudgetStatus`).
 *
 * Kept here so pages outside the sidebar shell (HomePage, etc.)
 * can subscribe without having to import the private hook from
 * SidebarApp.
 */
export function useStore() {
  const [, forceUpdate] = useState(0);
  useEffect(() => store.subscribe(() => forceUpdate((n) => n + 1)), []);
  return store;
}

const store = {
  queueState: [],
  scanStatus: {},
  currentUserRole: null,
  gmailIntegration: null,
  selectedItemId: null,
  currentThreadId: null,
  agentSessionsState: new Map(),
  browserTabContext: [],
  agentInsightsState: new Map(),
  sourcesState: new Map(),
  contextState: new Map(),
  tasksState: new Map(),
  notesState: new Map(),
  commentsState: new Map(),
  filesState: new Map(),
  activeContextTab: 'email',
  contextUiState: { itemId: null, loading: false, error: '' },
  agentSummaryState: { itemId: null, mode: null, loading: false, error: '', data: null },
  agentPreviewState: { key: null, loading: false, error: '', data: null },
  batchOpsState: { mode: null, loading: false, error: '', data: null },
  batchOpsPolicyState: { maxItems: 5, amountThreshold: '', selectionPreset: 'queue_order' },
  auditState: { itemId: null, loading: false, events: [] },
  rowDecorated: new Set(),
  openComposeWithPrefill: null,
  // LLM runaway-spend guard status. null = unknown / not loaded yet.
  // Shape from GET /api/workspace/llm-budget/status:
  //   { paused, paused_at, cost_usd, cap_usd, period_start, period_end, can_override }
  // Consumed by BudgetPausedBanner in ThreadSidebar + HomePage.
  llmBudgetStatus: null,
  llmBudgetOverridePending: false,

  update(patch) {
    Object.assign(this, patch);
    _listeners.forEach(fn => fn());
  },

  subscribe(fn) {
    _listeners.add(fn);
    return () => _listeners.delete(fn);
  },

  findItemByThreadId(threadId) {
    if (!threadId) return null;
    return this.queueState.find((item) => (
      item.thread_id === threadId
      || item.threadId === threadId
      || item.message_id === threadId
      || item.messageId === threadId
    )) || null;
  },

  findItemById(itemId) {
    if (!itemId) return null;
    return this.queueState.find(item => item.id === itemId || item.invoice_key === itemId) || null;
  },

  setSelectedItem(itemId) {
    this.update({
      selectedItemId: itemId || null,
      activeContextTab: 'email',
      auditState: { itemId: null, loading: false, events: [] },
      contextUiState: { itemId: null, loading: false, error: '' },
    });
  },

  async composeWithPrefill(prefill = {}) {
    if (typeof this.openComposeWithPrefill !== 'function') {
      throw new Error('compose_launcher_unavailable');
    }
    return this.openComposeWithPrefill(prefill);
  },

  getPrimaryItem() {
    const selected = this.findItemById(this.selectedItemId);
    if (selected) return selected;
    const threadItem = this.findItemByThreadId(this.currentThreadId);
    if (threadItem) return threadItem;
    if (!this.queueState.length) return null;
    return this.queueState[0];
  },

  getPrimaryItemIndex() {
    const item = this.getPrimaryItem();
    if (!item) return -1;
    return this.queueState.findIndex(entry => (entry.id || entry.invoice_key) === (item.id || item.invoice_key));
  },

  selectItemByOffset(offset) {
    if (!this.queueState.length) return;
    const current = Math.max(0, this.getPrimaryItemIndex());
    const next = Math.max(0, Math.min(this.queueState.length - 1, current + offset));
    const nextItem = this.queueState[next];
    if (!nextItem) return;
    this.setSelectedItem(nextItem.id || nextItem.invoice_key || null);
  },
};

export default store;
