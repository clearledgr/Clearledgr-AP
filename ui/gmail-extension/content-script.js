/**
 * Clearledgr Gmail Extension - Data Bridge (NO UI)
 *
 * This file intentionally mounts ZERO UI into Gmail.
 * All Gmail UI is mounted via InboxSDK in `src/inboxsdk-layer.js` (built to `dist/`).
 *
 * Responsibilities:
 * - Initialize and run `ClearledgrQueueManager` (scanning + queue state)
 * - Handle background/runtime messages (open Clearledgr, etc.)
 * - Respond to InboxSDK layer events with DATA ONLY via CustomEvents
 */
import { ClearledgrQueueManager } from './queue-manager.js';

(function () {
  'use strict';

  const GUARD_KEY = '__clearledgr_content_bridge_initialized';
  if (window[GUARD_KEY]) {
    console.log('[Clearledgr] Content bridge already initialized (guard active)');
    return;
  }
  window[GUARD_KEY] = true;

  let queueManager = null;

  // Vendor + GL config are stored locally so UI can manage them without backend.
  let vendorSearch = '';
  let vendorConfig = {};
  let glConfig = { accounts: [], rules: [] };
  let historyFilter = 'all';

  function emit(name, detail) {
    window.dispatchEvent(new CustomEvent(name, { detail }));
  }

  function toast(message, type = 'info') {
    emit('clearledgr:toast', { message, type });
  }

  async function getRuntimeSettings() {
    // Prefer queue manager's sync config (single source of truth).
    if (queueManager?.getSyncConfig) return await queueManager.getSyncConfig();

    const data = await new Promise((resolve) => {
      chrome.storage.sync.get(['settings', 'backendUrl', 'organizationId', 'userEmail', 'slackChannel'], resolve);
    });
    const nested = data.settings || {};
    let backendUrl = data.backendUrl || nested.backendUrl || nested.apiEndpoint || 'http://127.0.0.1:8010';
    backendUrl = String(backendUrl).trim();
    if (!/^https?:\/\//i.test(backendUrl)) backendUrl = `http://${backendUrl}`;
    if (backendUrl.endsWith('/v1')) backendUrl = backendUrl.slice(0, -3);
    try {
      const parsed = new URL(backendUrl);
      if (parsed.hostname === '0.0.0.0' || parsed.hostname === 'localhost') {
        parsed.hostname = '127.0.0.1';
      }
      backendUrl = parsed.toString();
    } catch (_) {
      // keep original value
    }
    backendUrl = backendUrl.replace(/\/+$/, '');

    return {
      backendUrl,
      organizationId: data.organizationId || nested.organizationId || 'default',
      userEmail: data.userEmail || nested.userEmail || null,
      slackChannel: data.slackChannel || nested.slackChannel || '#finance-approvals'
    };
  }

  function safeParseAmount(value) {
    if (value === null || value === undefined) return null;
    const n = parseFloat(String(value).replace(/[^0-9.-]/g, ''));
    return Number.isFinite(n) ? n : null;
  }

  function getQueue() {
    return queueManager?.getQueue?.() || [];
  }

  function findQueueItemById(emailId) {
    if (!emailId) return null;
    const queue = getQueue();
    return queue.find((e) => (e.id || e.gmail_id) === emailId) || null;
  }

  function dispatchPipelineData(meta = {}) {
    emit('clearledgr:pipeline-data', {
      queue: getQueue(),
      ...meta
    });
  }

  // ---------------------------------------------------------------------------
  // DATA ENDPOINTS (for InboxSDK layer)
  // ---------------------------------------------------------------------------

  function sendVendorData() {
    const queue = getQueue();

    const vendorMap = {};
    queue.forEach((email) => {
      const vendorName =
        email.vendor ||
        email.detected?.vendor ||
        email.sender ||
        (email.from ? String(email.from).split('@')[0] : null) ||
        'Unknown';

      const key = String(vendorName).trim() || 'Unknown';
      if (!vendorMap[key]) {
        const cfg = vendorConfig[key] || {};
        vendorMap[key] = {
          id: key,
          name: cfg.alias || key,
          alias: cfg.alias || null,
          email: cfg.email || (email.sender || email.from || null),
          invoiceCount: 0,
          totalSpend: 0,
          lastActivity: null
        };
      }

      vendorMap[key].invoiceCount += 1;

      const amountRaw = email.detected?.amount ?? email.amount ?? null;
      const amount = safeParseAmount(amountRaw);
      if (amount !== null) vendorMap[key].totalSpend += amount;

      const activityDate = email.date || email.detectedAt || email.receivedAt || null;
      if (activityDate && (!vendorMap[key].lastActivity || new Date(activityDate) > new Date(vendorMap[key].lastActivity))) {
        vendorMap[key].lastActivity = activityDate;
      }
    });

    let vendors = Object.values(vendorMap).map((v) => ({
      ...v,
      totalSpend: v.totalSpend,
      canMerge: v.invoiceCount <= 3
    }));

    // Sort by spend (desc)
    vendors.sort((a, b) => (b.totalSpend || 0) - (a.totalSpend || 0));

    // Apply search
    if (vendorSearch) {
      const q = vendorSearch.toLowerCase();
      vendors = vendors.filter((v) => {
        return (
          String(v.name || '').toLowerCase().includes(q) ||
          String(v.email || '').toLowerCase().includes(q) ||
          String(v.alias || '').toLowerCase().includes(q)
        );
      });
    }

    emit('clearledgr:vendors-data', { vendors });
  }

  function sendAnalyticsData() {
    const queue = getQueue();
    const activity = queueManager?.getActivityFeed?.() || [];

    let totalAmount = 0;
    const vendorSpend = {};
    const statusCounts = { new: 0, pending: 0, approved: 0, synced: 0, exception: 0 };
    const confidenceCounts = { high: 0, medium: 0, low: 0 };
    let autoApproved = 0;

    queue.forEach((email) => {
      const amountRaw = email.detected?.amount ?? email.amount ?? null;
      const amount = safeParseAmount(amountRaw);
      if (amount !== null) {
        totalAmount += amount;
        const vendor = email.detected?.vendor || email.vendor || email.sender || (email.from ? String(email.from).split('@')[0] : null) || 'Unknown';
        vendorSpend[vendor] = (vendorSpend[vendor] || 0) + amount;
      }

      const status = email.status || 'pending';
      if (status === 'pending' || status === 'new') statusCounts.new += 1;
      else if (status === 'needs_review' || status === 'pending_approval') statusCounts.pending += 1;
      else if (status === 'approved') statusCounts.approved += 1;
      else if (status === 'posted') statusCounts.synced += 1;
      else if (status === 'error' || status === 'rejected') statusCounts.exception += 1;

      const conf = typeof email.confidence === 'number' ? email.confidence : 0.85;
      if (conf >= 0.95) {
        confidenceCounts.high += 1;
        if (email.autoDetected) autoApproved += 1;
      } else if (conf >= 0.8) {
        confidenceCounts.medium += 1;
      } else {
        confidenceCounts.low += 1;
      }
    });

    const topVendors = Object.entries(vendorSpend)
      .map(([name, amount]) => ({ name, amount }))
      .sort((a, b) => b.amount - a.amount)
      .slice(0, 5);

    const recentActivity = activity.slice(-10).reverse().map((a) => ({
      type: a.type,
      message: a.message,
      time: formatTimeAgo(a.timestamp)
    }));

    const autoRate = queue.length > 0 ? Math.round((autoApproved / queue.length) * 100) : 0;

    emit('clearledgr:analytics-data', {
      totalAmount,
      totalCount: queue.length,
      autoApproved,
      autoRate,
      pendingCount: statusCounts.pending + statusCounts.new,
      syncedCount: statusCounts.synced,
      topVendors,
      statusBreakdown: statusCounts,
      confidenceDistribution: confidenceCounts,
      recentActivity
    });
  }

  function sendHistoryData() {
    const activity = queueManager?.getActivityFeed?.() || [];
    const queue = getQueue();

    let activities = [];

    queue.forEach((email) => {
      const vendor =
        email.vendor ||
        email.detected?.vendor ||
        email.sender ||
        (email.from ? String(email.from).split('@')[0] : null) ||
        'Unknown';
      const amountRaw = email.detected?.amount ?? email.amount ?? null;
      const amount = safeParseAmount(amountRaw);

      if (email.detectedAt) {
        activities.push({
          type: 'detected',
          action: 'Invoice Detected',
          description: `${vendor} - ${email.subject || 'No subject'}`,
          time: formatRelativeTime(email.detectedAt),
          timestamp: new Date(email.detectedAt).getTime(),
          amount
        });
      }

      if (email.status === 'approved' && email.approvedAt) {
        activities.push({
          type: 'approved',
          action: 'Invoice Approved',
          badge: 'Approved',
          description: `${vendor} - ${email.subject || 'No subject'}`,
          time: formatRelativeTime(email.approvedAt),
          timestamp: new Date(email.approvedAt).getTime(),
          amount
        });
      }

      if (email.status === 'rejected' && email.rejectedAt) {
        activities.push({
          type: 'rejected',
          action: 'Invoice Rejected',
          badge: email.rejectionReason || 'Rejected',
          description: `${vendor} - ${email.subject || 'No subject'}`,
          time: formatRelativeTime(email.rejectedAt),
          timestamp: new Date(email.rejectedAt).getTime(),
          amount
        });
      }

      if (email.status === 'posted' && email.postedAt) {
        activities.push({
          type: 'posted',
          action: 'Posted to ERP',
          badge: 'Synced',
          description: `${vendor} - ${email.subject || 'No subject'}`,
          time: formatRelativeTime(email.postedAt),
          timestamp: new Date(email.postedAt).getTime(),
          amount
        });
      }

      if (email.status === 'error') {
        const ts = email.errorAt || email.updatedAt || Date.now();
        activities.push({
          type: 'error',
          action: 'Processing Error',
          badge: 'Error',
          description: `${vendor} - ${email.errorMessage || 'Unknown error'}`,
          time: formatRelativeTime(ts),
          timestamp: new Date(ts).getTime(),
          amount
        });
      }
    });

    activity.forEach((a) => {
      activities.push({
        type: a.type || 'detected',
        action: a.action || a.message,
        description: a.description || a.message,
        time: formatRelativeTime(a.timestamp),
        timestamp: new Date(a.timestamp || Date.now()).getTime()
      });
    });

    activities.sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0));
    if (historyFilter !== 'all') activities = activities.filter((a) => a.type === historyFilter);
    activities = activities.slice(0, 100);

    emit('clearledgr:history-data', { activities });
  }

  async function sendErpStatus() {
    try {
      const settings = await getRuntimeSettings();
      const response = await fetch(`${settings.backendUrl}/erp/status/${encodeURIComponent(settings.organizationId)}`);
      if (!response.ok) return;
      const status = await response.json();
      emit('clearledgr:erp-status', status);
    } catch (err) {
      console.warn('[Clearledgr] ERP status fetch failed:', err?.message || err);
      emit('clearledgr:erp-status', { error: 'unavailable' });
    }
  }

  async function sendAgingData() {
    try {
      const settings = await getRuntimeSettings();
      const response = await fetch(
        `${settings.backendUrl}/ap/aging/summary?organization_id=${encodeURIComponent(settings.organizationId)}`
      );
      if (!response.ok) return;
      const data = await response.json();
      emit('clearledgr:aging-data', data);
    } catch (err) {
      console.warn('[Clearledgr] Aging fetch failed:', err?.message || err);
      emit('clearledgr:aging-data', { buckets: [], total: 0, error: 'unavailable' });
    }
  }

  // ---------------------------------------------------------------------------
  // EVENT HANDLERS (called by InboxSDK UI)
  // ---------------------------------------------------------------------------

  function handleScanInbox() {
    queueManager?.scanInbox?.();
    toast('Scanning inbox for invoices...', 'info');
  }

  async function handleTriageThread(e) {
    const { threadId, messageId, subject, sender, date, source } = e.detail || {};
    if (!threadId || !queueManager?.triageThreadEmail) return;
    await queueManager.triageThreadEmail({
      id: threadId,
      messageId,
      subject: subject || '',
      sender: sender || '',
      date: date || '',
      source: source || 'thread_view'
    });
    dispatchPipelineData({ source: 'thread_triage' });
  }

  async function handleResetScan(e) {
    if (!queueManager?.resetScanState) return;
    await queueManager.resetScanState();
    dispatchPipelineData({ source: 'reset_scan' });
    toast('Scan state reset. Re-scanning inbox...', 'info');
    queueManager.scanInbox?.();
  }

  async function handleRequestPipelineData() {
    try {
      if (queueManager?.syncQueueWithBackend) await queueManager.syncQueueWithBackend();
    } finally {
      dispatchPipelineData({ source: 'request' });
    }
  }

  async function handleQueueEmail(e) {
    const { threadId, subject, source } = e.detail || {};
    if (!threadId || !queueManager) return;

    try {
      await queueManager.addToQueue({
        id: threadId,
        thread_id: threadId,
        gmail_id: threadId,
        subject: subject || 'No subject',
        sender: 'unknown',
        from: 'unknown',
        detectedAt: new Date().toISOString(),
        source: source || 'manual'
      });
      toast('Added to Clearledgr queue', 'success');
      dispatchPipelineData({ source: 'queue_email' });
    } catch (err) {
      console.warn('[Clearledgr] Queue email failed:', err?.message || err);
      toast('Failed to add to queue', 'error');
    }
  }

  async function handleApproveInvoice(e) {
    const { emailId } = e.detail || {};
    if (!emailId || !queueManager) return;
    await queueManager.updateStatus(emailId, 'approved');
    toast('Invoice approved', 'success');
    dispatchPipelineData({ source: 'approve' });
  }

  async function handleRejectInvoice(e) {
    const { emailId, reason } = e.detail || {};
    if (!emailId || !queueManager) return;
    await queueManager.updateStatus(emailId, 'rejected', { rejectionReason: reason || 'unspecified' });
    toast('Invoice rejected', 'info');
    dispatchPipelineData({ source: 'reject' });
  }

  async function handleBulkApprove(e) {
    const emailIds = e.detail?.emailIds || [];
    if (!queueManager || emailIds.length === 0) return;
    for (const id of emailIds) {
      await queueManager.updateStatus(id, 'approved', { source: 'bulk' });
    }
    toast(`Approved ${emailIds.length} invoice(s)`, 'success');
    dispatchPipelineData({ source: 'bulk_approve' });
  }

  async function handleBulkReject(e) {
    const emailIds = e.detail?.emailIds || [];
    const reason = e.detail?.reason || 'bulk_rejected';
    if (!queueManager || emailIds.length === 0) return;
    for (const id of emailIds) {
      await queueManager.updateStatus(id, 'rejected', { rejectionReason: reason, source: 'bulk' });
    }
    toast(`Rejected ${emailIds.length} invoice(s)`, 'info');
    dispatchPipelineData({ source: 'bulk_reject' });
  }

  async function handleRetryInvoice(e) {
    const { emailId } = e.detail || {};
    if (!emailId || !queueManager) return;
    await queueManager.updateStatus(emailId, 'pending', { source: 'retry' });
    toast('Invoice queued for retry', 'info');
    dispatchPipelineData({ source: 'retry' });
  }

  async function handleRestoreInvoice(e) {
    const { emailId } = e.detail || {};
    if (!emailId || !queueManager) return;
    await queueManager.updateStatus(emailId, 'pending', { source: 'restore' });
    toast('Invoice restored to pending', 'info');
    dispatchPipelineData({ source: 'restore' });
  }

  function handleDismissInvoice(e) {
    const { emailId } = e.detail || {};
    if (!emailId || !queueManager) return;
    queueManager.removeFromQueue(emailId);
    toast('Invoice dismissed', 'info');
    dispatchPipelineData({ source: 'dismiss' });
  }

  async function handleMarkPaid(e) {
    const { emailId } = e.detail || {};
    if (!emailId || !queueManager) return;
    await queueManager.updateStatus(emailId, 'paid', { source: 'mark_paid' });
    toast('Invoice marked as paid', 'success');
    dispatchPipelineData({ source: 'mark_paid' });
  }

  function handleRequestEmailData(e) {
    const { subject } = e.detail || {};
    const queue = getQueue();

    // Best-effort find by subject (matches InboxSDK request shape).
    let email = null;
    if (subject) {
      email = queue.find((it) => it.subject === subject) || queue.find((it) => String(it.subject || '').includes(String(subject).slice(0, 30)));
    }

    const vendor =
      email?.detected?.vendor ||
      email?.vendor ||
      email?.sender ||
      (email?.from ? String(email.from).split('@')[0] : null) ||
      null;

    emit('clearledgr:email-data-response', {
      subject: subject || null,
      amount: email?.detected?.amount || email?.amount || null,
      vendor,
      dueDate: email?.detected?.dueDate || email?.dueDate || null,
      invoiceNumber: email?.detected?.invoiceNumber || email?.invoiceNumber || null,
      confidence: typeof email?.confidence === 'number' ? email.confidence : null,
      status: email?.status || 'new',
      vendorInvoiceCount: vendor ? queue.filter((it) => (it.detected?.vendor || it.vendor || it.sender) === vendor).length : null,
      vendorTotalSpend: vendor ? queue.reduce((sum, it) => {
        const v = it.detected?.vendor || it.vendor || it.sender;
        if (v !== vendor) return sum;
        const a = safeParseAmount(it.detected?.amount ?? it.amount);
        return sum + (a || 0);
      }, 0) : null,
      avgPaymentTime: null
    });
  }

  function handleRequestEmailForFix(e) {
    const { emailId } = e.detail || {};
    const email = findQueueItemById(emailId);
    emit('clearledgr:email-for-fix-data', { emailId, email: email || null });
  }

  async function handleFixInvoice(e) {
    const { emailId, updates } = e.detail || {};
    if (!emailId || !queueManager) return;

    const email = findQueueItemById(emailId);
    if (!email) return;

    // Apply updates locally; backend re-triage can be triggered separately if needed.
    email.detected = { ...(email.detected || {}), ...(updates || {}) };
    await queueManager.saveQueue?.();

    toast('Invoice updated', 'success');
    dispatchPipelineData({ source: 'fix' });
  }

  function handleRequestDuplicateData(e) {
    const { emailId } = e.detail || {};
    const email = findQueueItemById(emailId);
    const current = email
      ? {
          vendor: email.detected?.vendor || email.vendor || email.sender || 'Unknown',
          amount: email.detected?.amount || email.amount || '--',
          date: email.detectedAt || email.receivedAt || email.date || '--',
          invoiceNumber: email.detected?.invoiceNumber || email.invoiceNumber || '--'
        }
      : { vendor: 'Unknown', amount: '--', date: '--', invoiceNumber: '--' };

    const match = email?.duplicateWarning?.matches?.[0] || null;
    const original = match
      ? {
          vendor: match.vendor || 'Unknown',
          amount: match.amount || '--',
          date: match.date || '--',
          invoiceNumber: match.invoiceNumber || '--'
        }
      : { vendor: 'Unknown', amount: '--', date: '--', invoiceNumber: '--' };

    emit('clearledgr:duplicate-data', { emailId, current, original });
  }

  function handleDismissDuplicate(e) {
    const { emailId } = e.detail || {};
    if (!emailId || !queueManager) return;
    queueManager.removeFromQueue(emailId);
    toast('Duplicate dismissed', 'info');
    dispatchPipelineData({ source: 'dismiss_duplicate' });
  }

  async function handleKeepDuplicate(e) {
    const { emailId } = e.detail || {};
    const email = findQueueItemById(emailId);
    if (!email || !queueManager) return;
    email.isDuplicate = false;
    email.duplicateConfirmed = true;
    await queueManager.saveQueue?.();
    toast('Invoice kept (not duplicate)', 'success');
    dispatchPipelineData({ source: 'keep_duplicate' });
  }

  async function handleMergeDuplicate(e) {
    const { emailId } = e.detail || {};
    const email = findQueueItemById(emailId);
    if (!email || !queueManager) return;
    email.isDuplicate = false;
    await queueManager.updateStatus(emailId, 'approved', { source: 'merge_duplicate' });
    toast('Duplicate merged and approved', 'success');
    dispatchPipelineData({ source: 'merge_duplicate' });
  }

  async function handleConnectErp(e) {
    const { erp } = e.detail || {};
    if (!erp) return;

    try {
      const settings = await getRuntimeSettings();
      const response = await fetch(`${settings.backendUrl}/erp/${encodeURIComponent(erp)}/connect`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ organization_id: settings.organizationId })
      });

      if (!response.ok) throw new Error(`Backend returned ${response.status}`);
      const result = await response.json();

      if (!result.auth_url) throw new Error('No auth URL returned');

      const authWindow = window.open(result.auth_url, `${erp}_oauth`, 'width=600,height=700');
      const pollInterval = setInterval(async () => {
        try {
          if (authWindow?.closed) {
            clearInterval(pollInterval);
            await sendErpStatus();
            emit('clearledgr:erp-connected', { erp, success: true });
          }
        } catch (_) {
          clearInterval(pollInterval);
        }
      }, 1000);
    } catch (err) {
      console.error('[Clearledgr] ERP connection error:', err);
      emit('clearledgr:erp-connected', { erp, success: false, error: err?.message || String(err) });
    }
  }

  async function handleClearData() {
    try {
      await new Promise((resolve) => {
        chrome.storage.local.remove(['clearledgrQueue', 'clearledgrProcessedIds', 'vendorConfig', 'glConfig'], resolve);
      });
      vendorConfig = {};
      glConfig = { accounts: [], rules: [] };
      await queueManager?.loadQueue?.();
      dispatchPipelineData({ source: 'clear_data' });
      toast('Local Clearledgr data cleared', 'info');
    } catch (err) {
      toast('Failed to clear data', 'error');
    }
  }

  function handleExportCsv() {
    const queue = getQueue();

    const headers = [
      'email_id',
      'subject',
      'vendor',
      'amount',
      'currency',
      'invoice_number',
      'due_date',
      'status',
      'confidence'
    ];

    const rows = queue.map((email) => {
      const id = email.id || email.gmail_id || '';
      const vendor = email.detected?.vendor || email.vendor || email.sender || '';
      const amount = safeParseAmount(email.detected?.amount ?? email.amount);
      const currency = email.detected?.currency || email.currency || '';
      const inv = email.detected?.invoiceNumber || email.invoiceNumber || '';
      const due = email.detected?.dueDate || email.dueDate || '';
      const status = email.status || '';
      const conf = typeof email.confidence === 'number' ? email.confidence : '';

      return [
        escapeCSV(id),
        escapeCSV(email.subject || ''),
        escapeCSV(vendor),
        amount === null ? '' : amount,
        escapeCSV(currency),
        escapeCSV(inv),
        escapeCSV(due),
        escapeCSV(status),
        conf === '' ? '' : conf
      ].join(',');
    });

    const csv = [headers.join(','), ...rows].join('\n');
    const filename = `clearledgr-invoices-${new Date().toISOString().split('T')[0]}.csv`;

    // UI layer will trigger the download (this file is data-only).
    emit('clearledgr:export-csv-ready', { filename, csv });
  }

  function escapeCSV(str) {
    if (str === null || str === undefined) return '';
    const s = String(str);
    if (s.includes(',') || s.includes('"') || s.includes('\n')) return `"${s.replace(/\"/g, '""')}"`;
    return s;
  }

  // ---------------------------------------------------------------------------
  // UTIL
  // ---------------------------------------------------------------------------

  function formatTimeAgo(timestamp) {
    if (!timestamp) return '';
    const date = new Date(timestamp);
    const now = new Date();
    const diff = Math.floor((now - date) / 1000);

    if (diff < 60) return 'just now';
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return `${Math.floor(diff / 86400)}d ago`;
  }

  function formatRelativeTime(timestamp) {
    if (!timestamp) return '';
    const date = new Date(timestamp);
    const now = new Date();
    const diff = Math.floor((now - date) / 1000);

    if (diff < 60) return 'Just now';
    if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)} hours ago`;
    if (diff < 604800) return `${Math.floor(diff / 86400)} days ago`;
    return date.toLocaleDateString();
  }

  // ---------------------------------------------------------------------------
  // INIT
  // ---------------------------------------------------------------------------

  async function init() {
    // Load persisted configs
    try {
      const result = await new Promise((resolve) => {
        chrome.storage.local.get(['vendorConfig', 'glConfig'], resolve);
      });
      vendorConfig = result.vendorConfig || {};
      glConfig = result.glConfig || {
        accounts: [
          { code: '5000', name: 'Operating Expenses' },
          { code: '5100', name: 'Office Supplies' },
          { code: '5200', name: 'Software & Subscriptions' },
          { code: '5300', name: 'Professional Services' }
        ],
        rules: []
      };
    } catch (_) {
      // ignore
    }

    // Best-effort: ensure labels exist (OAuth may be prompted by Chrome)
    try {
      await chrome.runtime.sendMessage({ action: 'initializeLabels' });
    } catch (_) {
      // ignore
    }

    // Queue manager (scanning + backend triage)
    queueManager = new ClearledgrQueueManager();
    await queueManager.init();

    // Auto-scan on inbox navigation/refresh (debounced).
    let lastAutoScanAt = 0;
    const AUTO_SCAN_COOLDOWN_MS = 15000;
    const maybeAutoScan = (reason) => {
      if (!queueManager?.scanInbox) return;
      if (typeof queueManager.isInInboxRoute === 'function' && !queueManager.isInInboxRoute()) return;
      if (typeof queueManager.isInListView === 'function' && !queueManager.isInListView()) return;
      const now = Date.now();
      if (now - lastAutoScanAt < AUTO_SCAN_COOLDOWN_MS) return;
      lastAutoScanAt = now;
      queueManager.scanInbox();
    };

    window.addEventListener('hashchange', () => maybeAutoScan('route_change'));
    window.addEventListener('focus', () => maybeAutoScan('focus'));
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) maybeAutoScan('visibility');
    });

    // Stream queue updates to UI
    queueManager.subscribe((evt) => {
      if (!evt?.type) return;
      if (evt.type === 'QUEUE_UPDATED' || evt.type === 'ITEM_PROCESSED' || evt.type === 'AUTO_POSTED' || evt.type === 'BATCH_COMPLETE') {
        dispatchPipelineData({ source: evt.type });
      }
    });

    // Content-script runtime messages (popup/background)
    chrome.runtime.onMessage.addListener((request, _sender, sendResponse) => {
      if (request?.action === 'OPEN_CLEARLEDGR') {
        emit('clearledgr:open-home', { source: 'runtime' });
        sendResponse?.({ success: true });
        return true;
      }
      sendResponse?.({ success: false });
      return false;
    });

    // InboxSDK -> data bridge events
    window.addEventListener('clearledgr:scan-inbox', handleScanInbox);
    window.addEventListener('clearledgr:triage-thread', handleTriageThread);
    window.addEventListener('clearledgr:reset-scan', handleResetScan);
    window.addEventListener('clearledgr:request-pipeline-data', handleRequestPipelineData);
    window.addEventListener('clearledgr:queue-email', handleQueueEmail);
    window.addEventListener('clearledgr:approve-invoice', handleApproveInvoice);
    window.addEventListener('clearledgr:reject-invoice', handleRejectInvoice);
    window.addEventListener('clearledgr:bulk-approve', handleBulkApprove);
    window.addEventListener('clearledgr:bulk-reject', handleBulkReject);
    window.addEventListener('clearledgr:retry-invoice', handleRetryInvoice);
    window.addEventListener('clearledgr:restore-invoice', handleRestoreInvoice);
    window.addEventListener('clearledgr:dismiss-invoice', handleDismissInvoice);
    window.addEventListener('clearledgr:mark-paid', handleMarkPaid);

    window.addEventListener('clearledgr:request-email-data', handleRequestEmailData);
    window.addEventListener('clearledgr:request-email-for-fix', handleRequestEmailForFix);
    window.addEventListener('clearledgr:fix-invoice', handleFixInvoice);

    window.addEventListener('clearledgr:request-duplicate-data', handleRequestDuplicateData);
    window.addEventListener('clearledgr:dismiss-duplicate', handleDismissDuplicate);
    window.addEventListener('clearledgr:keep-duplicate', handleKeepDuplicate);
    window.addEventListener('clearledgr:merge-duplicate', handleMergeDuplicate);

    window.addEventListener('clearledgr:request-vendors', sendVendorData);
    window.addEventListener('clearledgr:search-vendors', (e) => {
      vendorSearch = String(e.detail?.query || '').toLowerCase();
      sendVendorData();
    });
    window.addEventListener('clearledgr:update-vendor', (e) => {
      const { vendorId, updates } = e.detail || {};
      if (!vendorId || !updates) return;
      vendorConfig[vendorId] = { ...(vendorConfig[vendorId] || {}), ...updates };
      chrome.storage.local.set({ vendorConfig });
      toast('Vendor settings saved', 'success');
      sendVendorData();
    });
    window.addEventListener('clearledgr:merge-vendor', (e) => {
      const { sourceId, targetName } = e.detail || {};
      if (!sourceId || !targetName) return;
      const queue = getQueue();
      let mergedCount = 0;
      queue.forEach((email) => {
        const emailVendor = email.vendor || email.detected?.vendor || email.sender || (email.from ? String(email.from).split('@')[0] : null);
        if (emailVendor === sourceId || vendorConfig[emailVendor]?.id === sourceId) {
          email.vendor = targetName;
          email.detected = { ...(email.detected || {}), vendor: targetName };
          mergedCount += 1;
        }
      });
      delete vendorConfig[sourceId];
      chrome.storage.local.set({ vendorConfig });
      queueManager?.saveQueue?.();
      toast(`Merged ${mergedCount} invoice(s) to ${targetName}`, 'success');
      sendVendorData();
      dispatchPipelineData({ source: 'merge_vendor' });
    });

    window.addEventListener('clearledgr:request-analytics', sendAnalyticsData);

    window.addEventListener('clearledgr:request-history', sendHistoryData);
    window.addEventListener('clearledgr:filter-history', (e) => {
      historyFilter = e.detail?.filter || 'all';
      sendHistoryData();
    });

    window.addEventListener('clearledgr:request-gl-config', () => emit('clearledgr:gl-config-data', glConfig));
    window.addEventListener('clearledgr:add-gl-account', (e) => {
      const { code, name } = e.detail || {};
      if (!code || !name) return;
      if (glConfig.accounts.some((a) => a.code === code)) {
        toast('GL account already exists', 'info');
        return;
      }
      glConfig.accounts.push({ code, name });
      glConfig.accounts.sort((a, b) => a.code.localeCompare(b.code));
      chrome.storage.local.set({ glConfig });
      emit('clearledgr:gl-config-data', glConfig);
      toast(`Added GL account ${code}`, 'success');
    });
    window.addEventListener('clearledgr:delete-gl-account', (e) => {
      const { code } = e.detail || {};
      if (!code) return;
      glConfig.accounts = glConfig.accounts.filter((a) => a.code !== code);
      glConfig.rules = glConfig.rules.filter((r) => r.glCode !== code);
      chrome.storage.local.set({ glConfig });
      emit('clearledgr:gl-config-data', glConfig);
      toast(`Removed GL account ${code}`, 'info');
    });
    window.addEventListener('clearledgr:add-gl-rule', (e) => {
      const { type, value, glCode } = e.detail || {};
      if (!type || !value || !glCode) return;
      glConfig.rules.push({ type, value, glCode });
      chrome.storage.local.set({ glConfig });
      emit('clearledgr:gl-config-data', glConfig);
      toast('Added categorization rule', 'success');
    });
    window.addEventListener('clearledgr:delete-gl-rule', (e) => {
      const { index } = e.detail || {};
      if (typeof index !== 'number') return;
      glConfig.rules.splice(index, 1);
      chrome.storage.local.set({ glConfig });
      emit('clearledgr:gl-config-data', glConfig);
      toast('Removed categorization rule', 'info');
    });

    window.addEventListener('clearledgr:connect-erp', handleConnectErp);
    window.addEventListener('clearledgr:request-erp-status', sendErpStatus);
    window.addEventListener('clearledgr:request-aging-data', sendAgingData);

    window.addEventListener('clearledgr:clear-data', handleClearData);
    window.addEventListener('clearledgr:export-csv', handleExportCsv);

    // Initial push so UI has data without waiting.
    dispatchPipelineData({ source: 'init' });

    console.log('[Clearledgr] Content bridge ready (UI-free)');
  }

  init().catch((err) => {
    console.error('[Clearledgr] Content bridge init failed:', err);
    toast('Clearledgr failed to initialize', 'error');
  });
})();
