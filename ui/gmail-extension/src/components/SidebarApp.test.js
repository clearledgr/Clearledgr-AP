import { describe, it, expect, vi, beforeEach } from 'vitest';
import { h } from 'preact';
import { render, screen } from '@testing-library/preact';
import htm from 'htm';
import store from '../utils/store.js';

const html = htm.bind(h);

// Mock chrome API
globalThis.chrome = { runtime: { getURL: (p) => `chrome-ext://abc/${p}` } };

// Import after mock
const { default: SidebarApp } = await import('./SidebarApp.js');

const mockQueueManager = {
  runtimeConfig: { backendUrl: 'http://localhost:8010', organizationId: 'test-org', authEntryMode: 'inline' },
  authorizeGmailNow: vi.fn().mockResolvedValue({ success: true }),
  refreshQueue: vi.fn().mockResolvedValue(undefined),
  fetchItemContext: vi.fn().mockResolvedValue({}),
  fetchAuditTrail: vi.fn().mockResolvedValue([]),
  requestApproval: vi.fn().mockResolvedValue({ status: 'needs_approval' }),
  nudgeApproval: vi.fn().mockResolvedValue({ status: 'nudged' }),
  retryFailedPost: vi.fn().mockResolvedValue({ status: 'ready_to_post' }),
  approveAndPost: vi.fn().mockResolvedValue({ status: 'posted' }),
};

beforeEach(() => {
  store.queueState = [];
  store.selectedItemId = null;
  store.currentThreadId = null;
  store.scanStatus = {};
  store.auditState = { itemId: null, loading: false, events: [] };
  store.contextUiState = { itemId: null, loading: false, error: '' };
  store.contextState = new Map();
  store.agentInsightsState = new Map();
  store.agentSessionsState = new Map();
  store.sourcesState = new Map();
  store.rowDecorated = new Set();
  vi.clearAllMocks();
});

describe('SidebarApp', () => {
  it('renders empty state when queue is empty', () => {
    render(html`<${SidebarApp} queueManager=${mockQueueManager} />`);
    expect(screen.getByText('No invoices in queue.')).toBeTruthy();
  });

  it('renders header with logo and title', () => {
    render(html`<${SidebarApp} queueManager=${mockQueueManager} />`);
    expect(screen.getByText('Clearledgr AP')).toBeTruthy();
  });

  it('renders scan status when monitoring active', () => {
    store.scanStatus = { state: 'idle' };
    render(html`<${SidebarApp} queueManager=${mockQueueManager} />`);
    expect(screen.getByText('Monitoring active.')).toBeTruthy();
  });

  it('renders auth required state', () => {
    store.scanStatus = { state: 'auth_required' };
    render(html`<${SidebarApp} queueManager=${mockQueueManager} />`);
    expect(screen.getByText(/Connect Gmail/)).toBeTruthy();
  });

  it('renders scanning state', () => {
    store.scanStatus = { state: 'scanning' };
    render(html`<${SidebarApp} queueManager=${mockQueueManager} />`);
    expect(screen.getByText('Scanning inbox for invoices.')).toBeTruthy();
  });

  it('renders error state', () => {
    store.scanStatus = { state: 'error', error: 'backend_down' };
    render(html`<${SidebarApp} queueManager=${mockQueueManager} />`);
    expect(screen.getByText(/Cannot sync/)).toBeTruthy();
  });

  it('renders invoice when queue has items', () => {
    store.queueState = [{
      id: 'inv-1',
      vendor_name: 'Acme Corp',
      amount: 1500,
      currency: 'USD',
      invoice_number: 'INV-001',
      due_date: '2026-04-01',
      state: 'needs_approval',
    }];
    store.selectedItemId = 'inv-1';
    render(html`<${SidebarApp} queueManager=${mockQueueManager} />`);
    expect(screen.getByText('Acme Corp')).toBeTruthy();
    expect(screen.getByText(/USD 1500\.00/)).toBeTruthy();
    expect(screen.getByText(/INV-001/)).toBeTruthy();
    expect(screen.getByText('Needs approval')).toBeTruthy();
  });

  it('renders primary action button for needs_approval state', () => {
    store.queueState = [{ id: 'inv-1', vendor_name: 'Test', state: 'needs_approval', amount: 100 }];
    store.selectedItemId = 'inv-1';
    render(html`<${SidebarApp} queueManager=${mockQueueManager} />`);
    expect(screen.getByText('Send approval request')).toBeTruthy();
  });

  it('renders Approve & Post for approved state', () => {
    store.queueState = [{ id: 'inv-1', vendor_name: 'Test', state: 'approved', amount: 100 }];
    store.selectedItemId = 'inv-1';
    render(html`<${SidebarApp} queueManager=${mockQueueManager} />`);
    expect(screen.getByText('Approve & Post')).toBeTruthy();
  });

  it('renders Retry ERP post for failed_post state', () => {
    store.queueState = [{ id: 'inv-1', vendor_name: 'Test', state: 'failed_post', amount: 100 }];
    store.selectedItemId = 'inv-1';
    render(html`<${SidebarApp} queueManager=${mockQueueManager} />`);
    expect(screen.getByText('Retry ERP post')).toBeTruthy();
  });

  it('renders navigator with prev/next', () => {
    store.queueState = [
      { id: 'a', vendor_name: 'First', state: 'received', amount: 100 },
      { id: 'b', vendor_name: 'Second', state: 'received', amount: 200 },
    ];
    store.selectedItemId = 'a';
    render(html`<${SidebarApp} queueManager=${mockQueueManager} />`);
    expect(screen.getByText('Invoice 1 of 2')).toBeTruthy();
    expect(screen.getByText('Prev')).toBeTruthy();
    expect(screen.getByText('Next')).toBeTruthy();
  });

  it('disables Prev on first item', () => {
    store.queueState = [{ id: 'a', vendor_name: 'Only', state: 'received', amount: 100 }];
    store.selectedItemId = 'a';
    render(html`<${SidebarApp} queueManager=${mockQueueManager} />`);
    const prevBtn = screen.getByText('Prev');
    expect(prevBtn.disabled).toBe(true);
  });

  it('renders context fields in details', () => {
    store.queueState = [{ id: 'inv-1', vendor_name: 'Test', state: 'received', amount: 100, subject: 'Invoice from Test', sender: 'test@example.com', exception_code: 'po_missing_reference', confidence: 0.87 }];
    store.selectedItemId = 'inv-1';
    render(html`<${SidebarApp} queueManager=${mockQueueManager} />`);
    expect(screen.getByText('Context fields')).toBeTruthy();
  });

  it('renders Decision section title', () => {
    render(html`<${SidebarApp} queueManager=${mockQueueManager} />`);
    expect(screen.getByText('Decision')).toBeTruthy();
  });
});
