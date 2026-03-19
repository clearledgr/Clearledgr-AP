/** Reactive store — replaces 15+ module-level let variables with a single observable state */

const _listeners = new Set();

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
  activeContextTab: 'email',
  contextUiState: { itemId: null, loading: false, error: '' },
  agentSummaryState: { itemId: null, mode: null, loading: false, error: '', data: null },
  agentPreviewState: { key: null, loading: false, error: '', data: null },
  batchOpsState: { mode: null, loading: false, error: '', data: null },
  batchOpsPolicyState: { maxItems: 5, amountThreshold: '', selectionPreset: 'queue_order' },
  auditState: { itemId: null, loading: false, events: [] },
  rowDecorated: new Set(),
  openComposeWithPrefill: null,

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
