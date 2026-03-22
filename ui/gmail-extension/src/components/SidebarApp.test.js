import { describe, it, expect, vi, beforeEach } from 'vitest';
import { h } from 'preact';
import { render, screen, cleanup, fireEvent } from '@testing-library/preact';
import htm from 'htm';
import store from '../utils/store.js';

const html = htm.bind(h);

// Mock chrome API
globalThis.chrome = { runtime: { getURL: (p) => `chrome-ext://abc/${p}` } };

// Import after mock
const { default: SidebarApp } = await import('./SidebarApp.js');

const mockQueueManager = {
  runtimeConfig: { backendUrl: 'http://localhost:8010', organizationId: 'test-org', authEntryMode: 'inline' },
  backendFetch: vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({
      erp_connected: true,
      slack_connected: true,
      approval_thresholds: [{ amount: 500 }],
    }),
  }),
  authorizeGmailNow: vi.fn().mockResolvedValue({ success: true }),
  refreshQueue: vi.fn().mockResolvedValue(undefined),
  fetchItemContext: vi.fn().mockResolvedValue({}),
  fetchAuditTrail: vi.fn().mockResolvedValue([]),
  requestApproval: vi.fn().mockResolvedValue({ status: 'needs_approval' }),
  nudgeApproval: vi.fn().mockResolvedValue({ status: 'nudged' }),
  rejectInvoice: vi.fn().mockResolvedValue({ status: 'rejected' }),
  prepareVendorFollowup: vi.fn().mockResolvedValue({ status: 'prepared' }),
  retryFailedPost: vi.fn().mockResolvedValue({ status: 'ready_to_post' }),
  approveAndPost: vi.fn().mockResolvedValue({ status: 'posted' }),
  resolveFieldReview: vi.fn().mockResolvedValue({ status: 'resolved', ap_item: { id: 'inv-conflict-1' } }),
};

beforeEach(() => {
  store.queueState = [];
  store.selectedItemId = null;
  store.currentThreadId = null;
  store.currentUserRole = 'operator';
  store.scanStatus = {};
  store.auditState = { itemId: null, loading: false, events: [] };
  store.contextUiState = { itemId: null, loading: false, error: '' };
  store.contextState = new Map();
  store.agentInsightsState = new Map();
  store.agentSessionsState = new Map();
  store.sourcesState = new Map();
  store.rowDecorated = new Set();
  vi.clearAllMocks();
  cleanup();
});

describe('SidebarApp', () => {
  it('renders empty state when queue is empty', () => {
    render(html`<${SidebarApp} queueManager=${mockQueueManager} />`);
    expect(screen.getByText('No finance documents in queue.')).toBeTruthy();
  });

  it('renders header with logo and title', () => {
    render(html`<${SidebarApp} queueManager=${mockQueueManager} />`);
    expect(screen.getByText('Clearledgr')).toBeTruthy();
  });

  it('renders scan status when monitoring active', () => {
    store.scanStatus = { state: 'idle' };
    render(html`<${SidebarApp} queueManager=${mockQueueManager} />`);
    expect(screen.getByText(/Monitoring active/)).toBeTruthy();
  });

  it('renders auth required state', () => {
    store.scanStatus = { state: 'auth_required' };
    render(html`<${SidebarApp} queueManager=${mockQueueManager} />`);
    expect(screen.getByText(/Connect Gmail/)).toBeTruthy();
    expect(screen.queryByText('Connections')).toBeNull();
  });

  it('shows Connections shortcut for admins in auth required state', () => {
    store.currentUserRole = 'admin';
    store.scanStatus = { state: 'auth_required' };
    render(html`<${SidebarApp} queueManager=${mockQueueManager} />`);
    expect(screen.getByText('Connections')).toBeTruthy();
  });

  it('renders scanning state', () => {
    store.scanStatus = { state: 'scanning' };
    render(html`<${SidebarApp} queueManager=${mockQueueManager} />`);
    expect(screen.getByText('Scanning inbox for invoices.')).toBeTruthy();
  });

  it('renders error state', () => {
    store.scanStatus = { state: 'error', error: 'backend_down' };
    render(html`<${SidebarApp} queueManager=${mockQueueManager} />`);
    expect(screen.getByText(/Backend unreachable/)).toBeTruthy();
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
    expect(screen.getByText('1,500.00')).toBeTruthy();
    expect(screen.getByText('USD')).toBeTruthy();
    expect(screen.getByText(/INV-001/)).toBeTruthy();
    expect(screen.getByText('Needs approval')).toBeTruthy();
  });

  it('renders primary action button for needs_approval state', () => {
    store.queueState = [{ id: 'inv-1', vendor_name: 'Test', state: 'needs_approval', amount: 100 }];
    store.selectedItemId = 'inv-1';
    render(html`<${SidebarApp} queueManager=${mockQueueManager} />`);
    expect(screen.getByText('Nudge approver')).toBeTruthy();
  });

  it('hides mutation actions for read-only roles', () => {
    store.currentUserRole = 'viewer';
    store.queueState = [{ id: 'inv-1', vendor_name: 'Test', state: 'needs_approval', amount: 100 }];
    store.selectedItemId = 'inv-1';
    render(html`<${SidebarApp} queueManager=${mockQueueManager} />`);
    expect(screen.queryByText('Nudge approver')).toBeNull();
    expect(screen.queryByText('Reject')).toBeNull();
    expect(screen.getByText(/Read-only view/)).toBeTruthy();
  });

  it('does not render Approve & Post for approved state', () => {
    store.queueState = [{ id: 'inv-1', vendor_name: 'Test', state: 'approved', amount: 100 }];
    store.selectedItemId = 'inv-1';
    render(html`<${SidebarApp} queueManager=${mockQueueManager} />`);
    expect(screen.queryByText('Approve & Post')).toBeNull();
    expect(screen.getByText(/Approval received\. Clearledgr is preparing the posting step\./)).toBeTruthy();
  });

  it('renders Preview ERP post for ready_to_post state', () => {
    store.queueState = [{ id: 'inv-1', vendor_name: 'Test', state: 'ready_to_post', amount: 100 }];
    store.selectedItemId = 'inv-1';
    render(html`<${SidebarApp} queueManager=${mockQueueManager} />`);
    expect(screen.getByText('Preview ERP post')).toBeTruthy();
  });

  it('renders Retry ERP post for failed_post state', () => {
    store.queueState = [{ id: 'inv-1', vendor_name: 'Test', state: 'failed_post', amount: 100 }];
    store.selectedItemId = 'inv-1';
    render(html`<${SidebarApp} queueManager=${mockQueueManager} />`);
    expect(screen.getByText('Retry ERP post')).toBeTruthy();
  });

  it('renders navigator with prev/next for multi-item queue', () => {
    store.queueState = [
      { id: 'a', vendor_name: 'First', state: 'received', amount: 100 },
      { id: 'b', vendor_name: 'Second', state: 'received', amount: 200 },
    ];
    store.selectedItemId = 'a';
    render(html`<${SidebarApp} queueManager=${mockQueueManager} />`);
    expect(screen.getByText('1 of 2')).toBeTruthy();
    expect(screen.getByLabelText('Previous')).toBeTruthy();
    expect(screen.getByLabelText('Next')).toBeTruthy();
  });

  it('disables Prev on first item', () => {
    store.queueState = [
      { id: 'a', vendor_name: 'First', state: 'received', amount: 100 },
      { id: 'b', vendor_name: 'Second', state: 'received', amount: 200 },
    ];
    store.selectedItemId = 'a';
    render(html`<${SidebarApp} queueManager=${mockQueueManager} />`);
    const prevBtn = screen.getByLabelText('Previous');
    expect(prevBtn.disabled).toBe(true);
  });

  it('renders evidence checklist instead of legacy context panels', () => {
    store.queueState = [{
      id: 'inv-1',
      vendor_name: 'Test',
      state: 'received',
      amount: 100,
      subject: 'Invoice from Test',
      sender: 'test@example.com',
      exception_code: 'po_missing_reference',
      confidence: 0.87,
      has_attachment: true,
    }];
    store.selectedItemId = 'inv-1';
    render(html`<${SidebarApp} queueManager=${mockQueueManager} />`);
    expect(screen.getByText('Evidence checklist')).toBeTruthy();
    expect(screen.getByText('Attachment')).toBeTruthy();
    expect(screen.queryByText('Context fields')).toBeNull();
    expect(screen.queryByText(/Agent timeline/i)).toBeNull();
  });

  it('renders paused field review detail when extraction sources conflict', () => {
    store.queueState = [{
      id: 'inv-conflict-1',
      vendor_name: 'Acme Corp',
      amount: 440,
      currency: 'USD',
      invoice_number: 'INV-77',
      due_date: '2026-04-01',
      state: 'received',
      requires_field_review: true,
      workflow_paused_reason: 'Workflow paused until amount is confirmed because the email and attachment disagree.',
      field_review_blockers: [
        {
          kind: 'source_conflict',
          field: 'amount',
          field_label: 'Amount',
          email_value_display: 'USD 400.00',
          attachment_value_display: 'USD 440.00',
          winning_source_label: 'Attachment',
          winning_value_display: 'USD 440.00',
          winner_reason: 'Attachment currently wins because Clearledgr selected the value from invoice.pdf as canonical.',
        },
      ],
    }];
    store.selectedItemId = 'inv-conflict-1';

    render(html`<${SidebarApp} queueManager=${mockQueueManager} />`);

    expect(screen.getByText('Paused field review')).toBeTruthy();
    expect(screen.getByText(/Workflow paused until amount is confirmed/)).toBeTruthy();
    expect(screen.getByText('Email said')).toBeTruthy();
    expect(screen.getByText('USD 400.00')).toBeTruthy();
    expect(screen.getByText('Attachment said')).toBeTruthy();
    expect(screen.getByText('USD 440.00')).toBeTruthy();
    expect(screen.getByText(/Attachment currently wins/)).toBeTruthy();
    expect(screen.queryByText('Request approval')).toBeNull();
    expect(screen.getByText('Use email')).toBeTruthy();
    expect(screen.getByText('Use attachment')).toBeTruthy();
    expect(screen.getByText('Enter manually')).toBeTruthy();
  });

  it('resolves a field-review blocker from the sidebar', async () => {
    store.queueState = [{
      id: 'inv-conflict-1',
      vendor_name: 'Acme Corp',
      amount: 440,
      currency: 'USD',
      invoice_number: 'INV-77',
      due_date: '2026-04-01',
      state: 'received',
      requires_field_review: true,
      workflow_paused_reason: 'Workflow paused until amount is confirmed because the email and attachment disagree.',
      field_review_blockers: [
        {
          kind: 'source_conflict',
          field: 'amount',
          field_label: 'Amount',
          email_value: 400,
          email_value_display: 'USD 400.00',
          attachment_value: 440,
          attachment_value_display: 'USD 440.00',
          winning_source_label: 'Attachment',
          winning_value_display: 'USD 440.00',
          winner_reason: 'Attachment currently wins because Clearledgr selected the value from invoice.pdf as canonical.',
        },
      ],
    }];
    store.selectedItemId = 'inv-conflict-1';

    render(html`<${SidebarApp} queueManager=${mockQueueManager} />`);
    fireEvent.click(screen.getByText('Use attachment'));

    await Promise.resolve();
    expect(mockQueueManager.resolveFieldReview).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'inv-conflict-1' }),
      expect.objectContaining({ field: 'amount', source: 'attachment', autoResume: true }),
    );
  });

  it('renders credit notes as non-invoice finance documents', () => {
    store.queueState = [{
      id: 'doc-credit-1',
      vendor_name: 'Attio',
      amount: 36,
      currency: 'USD',
      invoice_number: 'AW63GKYA-0003',
      state: 'received',
      document_type: 'credit_note',
    }];
    store.selectedItemId = 'doc-credit-1';

    render(html`<${SidebarApp} queueManager=${mockQueueManager} />`);

    expect(screen.getByText(/Credit note/)).toBeTruthy();
    expect(screen.getByText(/non-invoice finance document/i)).toBeTruthy();
    expect(screen.queryByText('Request approval')).toBeNull();
    expect(screen.queryByText('Reject')).toBeNull();
  });

  it('groups audit history into key history and background activity', () => {
    store.queueState = [{
      id: 'inv-1',
      vendor_name: 'Test',
      state: 'failed_post',
      amount: 100,
    }];
    store.selectedItemId = 'inv-1';
    store.auditState = {
      itemId: 'inv-1',
      loading: false,
      events: [
        {
          id: 'evt-high',
          event_type: 'erp_post_failed',
          operator_title: 'Posting failed',
          operator_message: 'Posting did not complete.',
          operator_severity: 'error',
          operator_importance: 'high',
          operator_category: 'posting',
          operator_evidence_label: 'ERP result',
          operator_evidence_detail: 'Recorded from the ERP connector response (DOC-77).',
          operator_action_hint: 'Retry ERP post or escalate for review.',
          ts: '2026-03-01T10:00:00Z',
        },
        {
          id: 'evt-low',
          event_type: 'browser_session_created',
          operator_title: 'ERP fallback prepared',
          operator_message: 'Prepared secure ERP browser fallback session.',
          operator_severity: 'info',
          operator_importance: 'low',
          operator_category: 'system',
          operator_evidence_label: 'ERP result',
          operator_evidence_detail: 'Recorded from the ERP connector response.',
          ts: '2026-03-01T09:00:00Z',
        },
      ],
    };

    render(html`<${SidebarApp} queueManager=${mockQueueManager} />`);
    fireEvent.click(screen.getByText(/View audit \(2\)/));

    expect(screen.getByText('Key history')).toBeTruthy();
    expect(screen.getByText('Background activity (1)')).toBeTruthy();
    expect(screen.getByText('Posting failed')).toBeTruthy();
    expect(screen.getByText(/Recorded from the ERP connector response \(DOC-77\)/)).toBeTruthy();
    expect(screen.getByText(/Next: Retry ERP post or escalate for review\./)).toBeTruthy();
  });

  it('hides navigator for single-item queue', () => {
    store.queueState = [{ id: 'a', vendor_name: 'Only', state: 'received', amount: 100 }];
    store.selectedItemId = 'a';
    render(html`<${SidebarApp} queueManager=${mockQueueManager} />`);
    expect(screen.queryByLabelText('Previous')).toBeNull();
  });

  it('renders invoice count badge in header', () => {
    store.queueState = [
      { id: 'a', vendor_name: 'A', state: 'received', amount: 100 },
      { id: 'b', vendor_name: 'B', state: 'received', amount: 200 },
    ];
    store.selectedItemId = 'a';
    render(html`<${SidebarApp} queueManager=${mockQueueManager} />`);
    expect(screen.getByText('2 invoices')).toBeTruthy();
  });

  it('uses the backend reject call instead of the legacy window event path', async () => {
    const dispatchSpy = vi.spyOn(window, 'dispatchEvent');
    store.queueState = [{ id: 'inv-1', vendor_name: 'Test', state: 'needs_approval', amount: 100 }];
    store.selectedItemId = 'inv-1';
    render(html`<${SidebarApp} queueManager=${mockQueueManager} />`);

    fireEvent.click(screen.getByText('Reject'));
    fireEvent.input(screen.getByLabelText('Rejection reason'), { target: { value: 'Duplicate invoice' } });
    const rejectButtons = screen.getAllByText('Reject', { selector: 'button' });
    fireEvent.click(rejectButtons[rejectButtons.length - 1]);

    await Promise.resolve();
    expect(mockQueueManager.rejectInvoice).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'inv-1' }),
      { reason: 'Duplicate invoice' }
    );
    expect(dispatchSpy).not.toHaveBeenCalledWith(expect.objectContaining({ type: 'clearledgr:reject-invoice' }));
    dispatchSpy.mockRestore();
  });
});
