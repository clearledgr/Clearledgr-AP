/**
 * Clearledgr Queue Manager
 * Handles background scanning, queue storage, and batch processing
 * Per design specification v1.1 - Intake Layer
 */
class ClearledgrQueueManager {
  constructor() {
    this.queue = [];
    this.processedIds = new Set();
    this.isScanning = false;
    this.scanInterval = null;
    this.listeners = [];
    this.currentIndex = 0;
    this.batchStats = { processed: 0, posted: 0, exceptions: 0, skipped: 0, totalAmount: 0 };
    this.activityFeed = [];

    // Background AP scan state (Gmail API search -> triage -> queue)
    this.apScan = {
      pendingIds: [],
      nextPageToken: null,
      exhausted: false,
      lastFetchAt: 0,
      burstStartedAt: 0,
      burstRuns: 0,
      pendingThreadMap: {},
    };
  }

  getThreadId(email) {
    return email?.thread_id || email?.threadId || email?.id || email?.gmail_id || null;
  }

  getBackendEmailId(email) {
    return email?.gmail_id || email?.gmailId || email?.id || null;
  }

  getLabelTargetId(email) {
    return this.getBackendEmailId(email) || this.getThreadId(email);
  }

  getClassifierInput(email) {
    if (!email) return {};
    return {
      subject: email.subject || '',
      sender: email.sender || '',
      senderEmail: email.senderEmail || '',
      snippet: email.snippet || '',
      attachments: email.attachments || []
    };
  }

  // Persist minimal per-thread cache for InboxSDK row rendering ("magic columns").
  // Best-effort only: failures here should not break scanning/queueing.
  cacheThreadSnapshot(email) {
    try {
      const threadId = this.getThreadId(email);
      if (!threadId) return;

      const statusPayload = {
        status: email.status || null,
        updatedAt: Date.now()
      };
      localStorage.setItem(`clearledgr_status_${threadId}`, JSON.stringify(statusPayload));

      const detected = email.detected || {};
      const reasoning = email.agentDecision?.reasoning || email.reasoning || {};
      const amountValue = this.parseAmount(detected.amount ?? email.amount);
      const invoicePayload = {
        vendor: detected.vendor || email.vendor || email.sender || null,
        amount: amountValue,
        currency: detected.currency || email.currency || null,
        dueDate: detected.dueDate || email.dueDate || null,
        invoiceNumber: detected.invoiceNumber || email.invoiceNumber || null,
        confidence: typeof email.confidence === 'number' ? email.confidence : null,
        decision: email.agentDecision?.decision || email.decision || null,
        reasoningSummary: reasoning.summary || null,
        reasoningFactors: Array.isArray(reasoning.factors) ? reasoning.factors : [],
        reasoningRisks: Array.isArray(reasoning.risks) ? reasoning.risks : []
      };
      localStorage.setItem(`clearledgr_invoice_${threadId}`, JSON.stringify(invoicePayload));
    } catch (_) {
      // ignore
    }
  }

  // Simple hash function for creating stable IDs
  hashString(str) {
    let hash = 0;
    for (let i = 0; i < str.length; i++) {
      const char = str.charCodeAt(i);
      hash = ((hash << 5) - hash) + char;
      hash = hash & hash;
    }
    return Math.abs(hash).toString(36);
  }

  // ==================== INITIALIZATION ====================

  async init() {
    console.log('[Clearledgr Queue] Initializing...');
    await this.loadQueue();
    await this.loadProcessedIds();
    await this.loadScanState();
    this.setScanStatus({
      state: 'idle',
      mode: this.apScan.pendingIds.length > 0 || this.apScan.nextPageToken ? 'gmail_api' : 'dom',
      candidates: 0,
      added: 0,
      error: null
    });
    await this.syncQueueWithBackend();
    this.startPeriodicScan();
    console.log('[Clearledgr Queue] Ready. Queue size:', this.queue.length);
  }

  // Safe message sending - handles extension context invalidation
  async safeSendMessage(message) {
    try {
      // Check if runtime is still valid
      if (!chrome.runtime?.id) {
        console.warn('[Clearledgr] Extension context invalidated, skipping message');
        return null;
      }
      return await chrome.runtime.sendMessage(message);
    } catch (e) {
      if (e.message?.includes('Extension context invalidated')) {
        console.warn('[Clearledgr] Extension was reloaded - please refresh Gmail');
        // Could show a toast to user here
      } else {
        console.warn('[Clearledgr] Message failed:', e.message);
      }
      return null;
    }
  }

  async getSyncConfig() {
    const data = await new Promise(resolve => {
      chrome.storage.sync.get(['settings', 'backendUrl', 'organizationId', 'userEmail', 'slackChannel'], resolve);
    });
    const nested = data.settings || {};
    let backendUrl = data.backendUrl || nested.backendUrl || nested.apiEndpoint || 'http://127.0.0.1:8010';
    backendUrl = String(backendUrl).trim();
    if (!/^https?:\/\//i.test(backendUrl)) {
      backendUrl = `http://${backendUrl}`;
    }
    if (backendUrl.endsWith('/v1')) {
      backendUrl = backendUrl.slice(0, -3);
    }
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

  getBackendCandidates(baseUrl) {
    const original = String(baseUrl || '').trim();
    if (!original) return [];

    const candidates = [original];
    try {
      const parsed = new URL(original);
      const isLoopback = ['127.0.0.1', 'localhost', '0.0.0.0'].includes(parsed.hostname);
      if (!isLoopback) return candidates;

      if (parsed.hostname === '0.0.0.0') {
        const normalized = new URL(parsed.toString());
        normalized.hostname = '127.0.0.1';
        candidates.push(normalized.toString().replace(/\/+$/, ''));
      }

      const addWithPort = (port) => {
        const next = new URL(parsed.toString());
        if (next.hostname === '0.0.0.0') next.hostname = '127.0.0.1';
        next.port = String(port);
        candidates.push(next.toString().replace(/\/+$/, ''));
      };

      if (parsed.port === '8000') addWithPort(8010);
      else if (parsed.port === '8010') addWithPort(8000);
      else if (!parsed.port) {
        addWithPort(8010);
        addWithPort(8000);
      }
    } catch (_) {
      // keep original only
    }

    return Array.from(new Set(candidates));
  }

  async fetchBackendWithFallback(baseUrl, path) {
    const candidates = this.getBackendCandidates(baseUrl);
    let lastError = null;

    for (const candidate of candidates) {
      try {
        const response = await fetch(`${candidate}${path}`);
        if (candidate !== baseUrl && response?.ok) {
          this.setBackendStatus('online');
        }
        return response;
      } catch (error) {
        lastError = error;
      }
    }

    throw lastError || new Error('backend_fetch_failed');
  }

  setBackendStatus(status, detail = null) {
    try {
      localStorage.setItem('clearledgr_backend_status', JSON.stringify({
        status,
        detail,
        updatedAt: Date.now()
      }));
    } catch (_) {
      // ignore
    }
  }

  setScanStatus(partial = {}) {
    try {
      const existingRaw = localStorage.getItem('clearledgr_scan_status');
      const existing = existingRaw ? JSON.parse(existingRaw) : {};
      const payload = {
        ...existing,
        ...partial,
        updatedAt: Date.now()
      };
      localStorage.setItem('clearledgr_scan_status', JSON.stringify(payload));
    } catch (_) {
      // ignore
    }
  }

  async resetScanState() {
    this.processedIds = new Set();
    await this.saveProcessedIds();
    this.apScan = {
      pendingIds: [],
      nextPageToken: null,
      exhausted: false,
      lastFetchAt: 0,
      burstStartedAt: 0,
      burstRuns: 0,
      pendingThreadMap: {},
    };
    this.setScanStatus({
      state: 'idle',
      mode: null,
      candidates: 0,
      added: 0,
      error: null
    });
    await this.saveScanState();
  }

  async loadScanState() {
    try {
      const result = await chrome.storage.local.get(['clearledgrApScanState']);
      const stored = result.clearledgrApScanState || {};
      this.apScan = {
        ...this.apScan,
        pendingIds: Array.isArray(stored.pendingIds) ? stored.pendingIds : this.apScan.pendingIds,
        nextPageToken: typeof stored.nextPageToken === 'string' ? stored.nextPageToken : this.apScan.nextPageToken,
        exhausted: Boolean(stored.exhausted),
        pendingThreadMap: stored.pendingThreadMap && typeof stored.pendingThreadMap === 'object'
          ? stored.pendingThreadMap
          : this.apScan.pendingThreadMap
      };
    } catch (e) {
      console.warn('[Clearledgr Queue] Failed to load scan state:', e);
    }
  }

  async saveScanState() {
    try {
      await chrome.storage.local.set({
        clearledgrApScanState: {
          pendingIds: this.apScan.pendingIds.slice(0, 500),
          nextPageToken: this.apScan.nextPageToken || null,
          exhausted: Boolean(this.apScan.exhausted),
          pendingThreadMap: this.apScan.pendingThreadMap || {},
          updatedAt: Date.now()
        }
      });
    } catch (e) {
      console.warn('[Clearledgr Queue] Failed to save scan state:', e);
    }
  }

  // ==================== QUEUE STORAGE ====================

  async loadQueue() {
    try {
      const result = await chrome.storage.local.get(['clearledgrQueue']);
      this.queue = result.clearledgrQueue || [];
    } catch (e) {
      console.warn('[Clearledgr Queue] Failed to load queue:', e);
      this.queue = [];
    }
  }

  async saveQueue() {
    try {
      await chrome.storage.local.set({ clearledgrQueue: this.queue });
    } catch (e) {
      console.warn('[Clearledgr Queue] Failed to save queue:', e);
    }
  }

  async syncQueueWithBackend() {
    try {
      const settings = await this.getSyncConfig();
      const backendUrl = settings.backendUrl;
      const organizationId = settings.organizationId;

      const response = await this.fetchBackendWithFallback(
        backendUrl,
        `/extension/pipeline?organization_id=${encodeURIComponent(organizationId)}`
      );
      if (!response.ok) return;

      const pipeline = await response.json();
      const invoices = Object.values(pipeline || {}).flat();

      if (!Array.isArray(invoices) || invoices.length === 0) return;

      let updated = false;
      for (const invoice of invoices) {
        const key = invoice.thread_id || invoice.gmail_id || invoice.email_id || invoice.id;
        if (!key) continue;

        const existing = this.queue.find(e => this.getThreadId(e) === key || this.getBackendEmailId(e) === key);
        if (!existing) continue;

        if (invoice.status && existing.status !== invoice.status) {
          existing.status = invoice.status;
          existing.statusHistory = existing.statusHistory || [];
          existing.statusHistory.push({
            status: invoice.status,
            timestamp: invoice.updated_at || new Date().toISOString(),
            source: 'backend'
          });
          this.cacheThreadSnapshot(existing);
          await this.applyBackendStatusLabel(existing, invoice.status);
          updated = true;
        }

        existing.detected = existing.detected || {};
        if (!existing.detected.vendor && invoice.vendor) existing.detected.vendor = invoice.vendor;
        if (!existing.detected.amount && invoice.amount) existing.detected.amount = invoice.amount;
        if (!existing.detected.currency && invoice.currency) existing.detected.currency = invoice.currency;
        if (!existing.detected.invoiceNumber && invoice.invoice_number) existing.detected.invoiceNumber = invoice.invoice_number;
        this.cacheThreadSnapshot(existing);
      }

      if (updated) {
        await this.saveQueue();
        this.notifyListeners('QUEUE_UPDATED', { count: this.queue.length, newCount: 0 });
      }
      this.setBackendStatus('online');
    } catch (e) {
      console.warn('[Clearledgr Queue] Backend sync skipped:', e.message);
      this.setBackendStatus('offline', e?.message || 'backend_sync_failed');
    }
  }

  async applyBackendStatusLabel(email, status) {
    if (!email || !status) return;
    const labelTarget = this.getLabelTargetId(email);
    if (!labelTarget) return;

    const statusLabelMap = {
      posted: 'Clearledgr/Processed',
      approved: 'Clearledgr/Processed',
      pending_approval: 'Clearledgr/Needs Review',
      rejected: 'Clearledgr/Exceptions'
    };

    const label = statusLabelMap[status];
    if (!label) return;

    await this.safeSendMessage({
      action: 'applyLabel',
      emailId: labelTarget,
      label
    });

    const removeMap = {
      posted: ['Clearledgr/Needs Review', 'Clearledgr/Exceptions'],
      approved: ['Clearledgr/Needs Review', 'Clearledgr/Exceptions'],
      rejected: ['Clearledgr/Needs Review', 'Clearledgr/Processed'],
      pending_approval: ['Clearledgr/Processed']
    };

    const labelsToRemove = removeMap[status] || [];
    for (const removeLabel of labelsToRemove) {
      await this.safeSendMessage({
        action: 'removeLabel',
        emailId: labelTarget,
        label: removeLabel
      });
    }
  }

  async loadProcessedIds() {
    try {
      const result = await chrome.storage.local.get(['clearledgrProcessedIds']);
      this.processedIds = new Set(result.clearledgrProcessedIds || []);
    } catch (e) {
      this.processedIds = new Set();
    }
  }

  async saveProcessedIds() {
    try {
      // Keep only last 1000 IDs to prevent unbounded growth
      const ids = Array.from(this.processedIds).slice(-1000);
      await chrome.storage.local.set({ clearledgrProcessedIds: ids });
    } catch (e) {
      console.warn('[Clearledgr Queue] Failed to save processed IDs:', e);
    }
  }

  // ==================== BACKGROUND SCANNING ====================

  isApQueueType(type) {
    if (!type) return false;
    const normalized = String(type).toLowerCase();
    return normalized === 'invoice' || normalized === 'payment_request' || normalized === 'payment request';
  }

  isApClassification(type) {
    if (!type) return false;
    const normalized = String(type).toUpperCase();
    return normalized === 'INVOICE' || normalized === 'PAYMENT_REQUEST' || normalized === 'PAYMENT REQUEST';
  }

  shouldQueueEmail(emailData, scanMode = 'gmail_api') {
    if (!emailData) return false;
    const resolvedType = String(
      emailData.type ||
      emailData.classification?.type ||
      ''
    ).toLowerCase().replace(/\s+/g, '_');
    if (!this.isApQueueType(resolvedType)) return false;

    const detected = emailData.detected || {};
    const amountValue = this.parseAmount(detected.amount ?? emailData.amount);
    const hasAmount = amountValue !== null && amountValue !== undefined;
    const hasInvoice = Boolean(detected.invoiceNumber || detected.invoice_number || emailData.invoiceNumber);
    const hasAttachment =
      Boolean(detected.has_attachments) ||
      (Array.isArray(emailData.attachments) && emailData.attachments.length > 0);
    const hasVendor = Boolean(detected.vendor || emailData.vendor || emailData.sender);

    // Payment requests should be stricter than invoices.
    if (String(resolvedType || '').toLowerCase().includes('payment')) {
      return hasAmount && hasVendor;
    }

    // DOM scans are strict to avoid false positives.
    if (scanMode === 'dom') {
      return hasAmount || hasInvoice;
    }

    // Gmail API scans: allow if we have amount, invoice number, or invoice-like attachment.
    return hasAmount || hasInvoice || hasAttachment;
  }

  resolveApTypeFromTriage(triageResult, fallbackEmail = null) {
    const raw = String(triageResult?.classification?.type || '').toUpperCase();
    if (raw === 'INVOICE') return 'invoice';
    if (raw === 'PAYMENT_REQUEST' || raw === 'PAYMENT REQUEST') return 'payment_request';
    return this.detectType(fallbackEmail);
  }

  buildQueueItemFromTriage(email, triageResult, localScore = 0, source = 'scan') {
    const extraction = triageResult?.extraction || {};
    const isAIPowered = triageResult?.ai_powered ||
      extraction.method === 'llm' ||
      triageResult?.classification?.method === 'llm';

    const gmailMeta = triageResult?._gmail || {};
    const subject = gmailMeta.subject || email.subject || '';
    const senderRaw = gmailMeta.sender || email.sender || '';
    const senderEmail =
      email.senderEmail ||
      (String(senderRaw).match(/<([^>]+)>/)?.[1] || '');
    const date = gmailMeta.date || email.date || '';
    const snippet = gmailMeta.snippet || email.snippet || '';

    const agentDecision = triageResult?.agent_decision || null;
    const decisionConfidence = Number.isFinite(agentDecision?.confidence) ? Number(agentDecision.confidence) : null;
    const extractionConfidence = Number.isFinite(extraction.confidence) ? Number(extraction.confidence) : 0;
    const classificationConfidence = Number.isFinite(triageResult?.classification?.confidence)
      ? Number(triageResult.classification.confidence)
      : 0;
    const baseConfidence = Math.max(extractionConfidence, classificationConfidence, localScore / 100);
    const resolvedConfidence = Math.max(
      baseConfidence,
      decisionConfidence !== null ? decisionConfidence : 0
    );
    const threadId = email.thread_id || email.threadId || email.id;
    const gmailId = email.gmail_id || email.gmailId || email.id;

    return {
      id: threadId,
      thread_id: threadId,
      gmail_id: gmailId,
      rowIndex: email.rowIndex,
      subject,
      sender: senderRaw,
      senderEmail,
      date,
      snippet,
      attachments: email.attachments,
      confidence: Math.max(0, Math.min(1, resolvedConfidence)),
      type: this.resolveApTypeFromTriage(triageResult, email),
      detected: {
        ...this.extractDetectedFields(email),
        ...extraction,
        vendor: extraction.vendor || senderRaw,
        amount: extraction.amount,
        invoiceNumber: extraction.invoice_number,
        dueDate: extraction.due_date,
        currency: extraction.currency
      },
      classification: triageResult?.classification,
      agentDecision,
      reasoning: agentDecision?.reasoning || null,
      decision: agentDecision?.decision || null,
      aiPowered: isAIPowered,
      extractionMethod: extraction.method || 'unknown',
      classificationMethod: triageResult?.classification?.method || 'unknown',
      llmProvider: extraction.provider || triageResult?.classification?.provider,
      intelligence: triageResult?.intelligence || null,
      vendorIntelligence: extraction.vendor_intelligence || null,
      policyCompliance: extraction.policy_compliance || null,
      priority: extraction.priority || null,
      budgetImpact: extraction.budget_impact || null,
      crossInvoiceAnalysis: extraction.cross_invoice_analysis || null,
      detectedAt: new Date().toISOString(),
      addedAt: Date.now(),
      source
    };
  }

  startPeriodicScan() {
    // Scan immediately
    this.scanInbox();
    
    // Then every 5 minutes
    this.scanInterval = setInterval(() => {
      this.scanInbox();
    }, 2 * 60 * 1000); // Scan every 2 minutes
  }

  stopPeriodicScan() {
    if (this.scanInterval) {
      clearInterval(this.scanInterval);
      this.scanInterval = null;
    }
  }

  // Conservative AP search query (focus: invoices/bills/payment requests).
  // This is intentionally strict to avoid queueing noise.
  getApScanQuery() {
    return [
      'in:inbox',
      '(has:attachment OR filename:pdf OR filename:png OR filename:jpg OR filename:jpeg OR filename:docx)',
      '(subject:(invoice OR bill OR \"invoice is available\" OR \"your invoice\" OR \"invoice available\" OR \"payment request\" OR \"amount due\" OR \"total due\" OR \"due date\" OR \"payable\") OR \"invoice number\" OR \"amount due\" OR \"total due\")',
      '-subject:(receipt OR confirmation OR paid OR \"payment received\" OR refund OR chargeback OR dispute OR declined OR \"payment failed\" OR \"card declined\" OR \"security alert\" OR \"password\" OR \"verify\" OR newsletter OR promotion OR offer OR webinar OR event)',
      '-category:promotions',
      '-category:social',
      '-category:updates'
    ].join(' ');
  }

  async fetchMoreApCandidates({ maxResults = 50 } = {}) {
    if (this.apScan.exhausted) return 0;

    const firstAttempt = !this.apScan.lastFetchAt;
    const response = await this.safeSendMessage({
      action: 'searchApEmails',
      query: this.getApScanQuery(),
      maxResults,
      pageToken: this.apScan.nextPageToken,
      // First attempt can be interactive to acquire Gmail OAuth if needed.
      interactive: firstAttempt
    });

    if (!response || response.success === false) {
      const error = response?.error || 'AP search unavailable';
      throw new Error(error);
    }

    const messages = Array.isArray(response.messages) ? response.messages : [];
    const before = this.apScan.pendingIds.length;

    for (const m of messages) {
      const messageId = m?.id;
      const threadId = m?.threadId || m?.id;
      if (!messageId) continue;
      if (this.processedIds.has(messageId) || (threadId && this.processedIds.has(threadId))) continue;
      if (this.apScan.pendingIds.includes(messageId)) continue;
      if (this.queue.find(e => this.getBackendEmailId(e) === messageId || (threadId && this.getThreadId(e) === threadId))) continue;
      this.apScan.pendingIds.push(messageId);
      if (threadId) {
        this.apScan.pendingThreadMap[messageId] = threadId;
      }
    }

    this.apScan.nextPageToken = response.nextPageToken || null;
    this.apScan.exhausted = !this.apScan.nextPageToken;
    this.apScan.lastFetchAt = Date.now();
    await this.saveScanState();

    return this.apScan.pendingIds.length - before;
  }

  async scanInbox() {
    if (this.isScanning) return;
    this.isScanning = true;
    
    console.log('[Clearledgr Queue] Scanning inbox...');

    // Track which scan strategy we used so we can decide whether to keep scanning in a burst.
    let scanMode = 'gmail_api';
    this.setScanStatus({ state: 'running', mode: scanMode });
    let processedChanged = false;
    const markProcessed = (id) => {
      if (!id) return;
      if (!this.processedIds.has(id)) {
        this.processedIds.add(id);
        processedChanged = true;
      }
    };
    const markEmailProcessed = (email) => {
      if (!email) return;
      markProcessed(this.getThreadId(email));
      markProcessed(this.getBackendEmailId(email));
    };
    
    try {
      // Prefer a true inbox-wide scan (Gmail API search) when available.
      // Falls back to DOM scraping (visible rows) if Gmail API is unavailable.
      let emails = [];

      try {
        if (this.apScan.pendingIds.length < 10 && !this.apScan.exhausted) {
          await this.fetchMoreApCandidates({ maxResults: 50 });
        }
      } catch (e) {
        scanMode = 'dom';
      }

      if (scanMode === 'gmail_api' && this.apScan.pendingIds.length > 0) {
        // Convert pending IDs into minimal email stubs; backend triage will enrich via Gmail API.
        const nextIds = this.apScan.pendingIds.splice(0, 25);
        emails = nextIds.map((messageId, index) => {
          const threadId = this.apScan.pendingThreadMap[messageId] || messageId;
          delete this.apScan.pendingThreadMap[messageId];
          return {
            id: threadId,
            thread_id: threadId,
            gmail_id: messageId,
            rowIndex: index,
            sender: '',
            senderEmail: '',
            subject: '',
            snippet: '',
            date: '',
            attachments: [{ filename: 'Attachment' }]
          };
        });
        await this.saveScanState();
      } else {
        scanMode = 'dom';
        // Only scan via DOM when we are in the inbox list view.
        if (!this.isInListView() || !this.isInInboxRoute()) {
          this.setScanStatus({ state: 'idle', mode: scanMode, candidates: 0, added: 0, error: 'not_in_inbox' });
          this.isScanning = false;
          return;
        }
        emails = this.getInboxEmails();
      }
      let newCount = 0;
      this.setScanStatus({ state: 'running', mode: scanMode, candidates: emails.length, error: null });
      
      for (const email of emails) {
        const threadId = this.getThreadId(email);
        const backendId = this.getBackendEmailId(email);
        if (threadId && this.processedIds.has(threadId)) continue;
        if (backendId && this.processedIds.has(backendId)) continue;
        
        // Backend triage is authoritative for AP classification and extraction.
        const localScore = 0;
        
        // Try backend triage for accurate AP classification
        let emailData;
        try {
          const triageId = this.getBackendEmailId(email) || this.getThreadId(email);
          const triageResult = await this.safeSendMessage({
            action: 'triageEmail',
            data: {
              id: triageId,
              subject: email.subject,
              sender: email.sender,
              snippet: email.snippet,
              attachments: email.attachments
            }
          });

          // If the backend/extension context is unavailable, safeSendMessage returns null.
          // Also normalize background error payloads: {success:false, error:"..."}.
          if (!triageResult || triageResult?.success === false) {
            throw new Error(triageResult?.error || 'Backend triage unavailable');
          }

          this.setBackendStatus('online');
          
          const classificationType = triageResult?.classification?.type;
          if (classificationType === 'NOISE') {
            markEmailProcessed(email);
            continue; // Backend AI says not financial
          }

          if (classificationType && !this.isApClassification(classificationType)) {
            markEmailProcessed(email);
            continue; // Not AP (only INVOICE/RECEIPT)
          }
          emailData = this.buildQueueItemFromTriage(email, triageResult, localScore, 'scan');

          if (!this.shouldQueueEmail(emailData, scanMode)) {
            markEmailProcessed(email);
            continue;
          }

          if (emailData.aiPowered) {
            console.log('[Clearledgr] AI extracted:', emailData.detected.vendor, emailData.detected.amount);
          }
          
        } catch (e) {
          // In gmail_api scan mode we don't have enough local metadata to safely fallback.
          // Re-queue the id and retry on the next scan tick (backend may be temporarily down).
          if (scanMode === 'gmail_api') {
            console.log('[Clearledgr Queue] Triage unavailable; will retry AP candidate later:', e.message);
            this.setBackendStatus('offline', e.message);
            const retryId = this.getBackendEmailId(email) || this.getThreadId(email);
            if (retryId && !this.apScan.pendingIds.includes(retryId)) {
              this.apScan.pendingIds.unshift(retryId);
              this.apScan.pendingThreadMap[retryId] = this.apScan.pendingThreadMap[retryId] || retryId;
              await this.saveScanState();
            }
            break;
          }
          // DOM mode: do not fallback to local classification to avoid false positives.
          this.setBackendStatus('offline', e.message);
          break;
        }
        
        // Only process if confidence is high enough (60%+)
        if (emailData.confidence >= 0.60) {
          if (!this.isApQueueType(emailData.type)) {
            markEmailProcessed(email);
            continue;
          }
          // Autonomous processing - high confidence auto-posts
          await this.addToQueue(emailData);
          newCount++;
        }
        
        markEmailProcessed(email);
      }
      
      if (newCount > 0) {
        await this.saveQueue();
        this.notifyListeners('QUEUE_UPDATED', { count: this.queue.length, newCount });
        console.log('[Clearledgr Queue] Added', newCount, 'emails to queue');
      }
      if (processedChanged) {
        await this.saveProcessedIds();
      }

      this.setScanStatus({ state: 'idle', mode: scanMode, candidates: emails.length, added: newCount, error: null });
      if (scanMode === 'gmail_api') {
        await this.saveScanState();
      }
    } catch (e) {
      console.warn('[Clearledgr Queue] Scan failed:', e);
      this.setScanStatus({ state: 'error', mode: scanMode, error: e?.message || 'scan_failed' });
    }
    
    this.isScanning = false;

    // If we're doing a Gmail API scan, run a small "burst" (multiple passes back-to-back)
    // so the user sees results quickly without waiting for the periodic timer.
    if (scanMode === 'gmail_api') {
      const now = Date.now();
      if (!this.apScan.burstStartedAt || now - this.apScan.burstStartedAt > 10 * 60 * 1000) {
        this.apScan.burstStartedAt = now;
        this.apScan.burstRuns = 0;
      }

      const moreToScan = this.apScan.pendingIds.length > 0 || !this.apScan.exhausted;
      if (moreToScan && (this.apScan.burstRuns || 0) < 5) {
        this.apScan.burstRuns = (this.apScan.burstRuns || 0) + 1;
        setTimeout(() => this.scanInbox(), 3000);
      }
    }
  }

  isInInboxRoute() {
    const hash = String(window.location.hash || '').toLowerCase();
    if (hash.includes('#inbox')) return true;
    const inboxLink =
      document.querySelector('[aria-label="Inbox"][aria-current="page"]') ||
      document.querySelector('[data-tooltip="Inbox"][aria-selected="true"]') ||
      document.querySelector('a[href*="#inbox"][aria-current="page"]');
    return !!inboxLink;
  }

  async triageThreadEmail({ id, messageId, subject, sender, date, source = 'thread_view' } = {}) {
    if (!id) return;
    const threadId = id;
    const gmailId = messageId || id;
    if (this.processedIds.has(threadId) || this.processedIds.has(gmailId)) return;
    if (this.queue.find(e =>
      (threadId && this.getThreadId(e) === threadId) ||
      (gmailId && this.getBackendEmailId(e) === gmailId)
    )) return;

    const markProcessed = () => {
      if (threadId) this.processedIds.add(threadId);
      if (gmailId) this.processedIds.add(gmailId);
    };

    const emailStub = {
      id: threadId,
      thread_id: threadId,
      gmail_id: gmailId,
      subject: subject || '',
      sender: sender || '',
      snippet: '',
      date: date || '',
      attachments: []
    };

    try {
      const triageId = this.getBackendEmailId(emailStub) || this.getThreadId(emailStub);
      const triageResult = await this.safeSendMessage({
        action: 'triageEmail',
        data: {
          id: triageId,
          subject: emailStub.subject,
          sender: emailStub.sender,
          snippet: ''
        }
      });

      if (!triageResult || triageResult?.success === false) {
        throw new Error(triageResult?.error || 'Backend triage unavailable');
      }

      this.setBackendStatus('online');

      const classificationType = triageResult?.classification?.type;
      if (classificationType === 'NOISE' || !this.isApClassification(classificationType)) {
        markProcessed();
        await this.saveProcessedIds();
        return;
      }

      const emailData = this.buildQueueItemFromTriage(emailStub, triageResult, 0, source);

      if (!this.shouldQueueEmail(emailData, 'dom')) {
        markProcessed();
        await this.saveProcessedIds();
        return;
      }

      if (emailData.confidence >= 0.6 && this.isApQueueType(emailData.type)) {
        await this.addToQueue(emailData);
        await this.saveQueue();
        this.notifyListeners('QUEUE_UPDATED', { count: this.queue.length, newCount: 1 });
      }

      markProcessed();
      await this.saveProcessedIds();
    } catch (err) {
      console.warn('[Clearledgr Queue] Thread triage failed:', err?.message || err);
      this.setBackendStatus('offline', err?.message || 'triage_failed');
    }
  }

  getInboxEmails() {
    const emails = [];
    
    // Get email rows from inbox view
    const rows = document.querySelectorAll('[role="main"] tr[jscontroller]');
    
    rows.forEach((row, index) => {
      try {
        // Get subject first
        const subjectEl = row.querySelector('.bog') || row.querySelector('[data-thread-id] span');
        const subject = subjectEl?.textContent?.trim() || 'No Subject';
        
        const messageId =
          row.getAttribute('data-legacy-message-id') ||
          row.getAttribute('data-message-id') ||
          row.querySelector('[data-legacy-message-id]')?.getAttribute('data-legacy-message-id') ||
          row.querySelector('[data-message-id]')?.getAttribute('data-message-id') ||
          null;

        const threadId =
          row.getAttribute('data-thread-id') ||
          row.querySelector('[data-thread-id]')?.getAttribute('data-thread-id') ||
          null;

        // Prefer thread id for UI, message id for backend.
        const id = threadId || messageId || `row-${index}-${this.hashString(subject)}`;
        
        // Get sender
        const senderEl = row.querySelector('[email]') || row.querySelector('.yX.xY span');
        const sender = senderEl?.getAttribute('name') || senderEl?.textContent?.trim() || 'Unknown';
        const senderEmail = senderEl?.getAttribute('email') || '';
        
        // Get snippet
        const snippetEl = row.querySelector('.y2');
        const snippet = snippetEl?.textContent?.trim() || '';
        
        // Get date
        const dateEl = row.querySelector('.xW.xY span') || row.querySelector('[title]');
        const date = dateEl?.getAttribute('title') || dateEl?.textContent?.trim() || '';
        
        // Check for attachments
        const hasAttachment = row.querySelector('[aria-label*="attachment"]') !== null ||
                             row.querySelector('.yf.xY') !== null;
        
        emails.push({
          id,
          thread_id: threadId || id,
          gmail_id: messageId || threadId || id,
          rowIndex: index,
          sender,
          senderEmail,
          subject,
          snippet,
          date,
          attachments: hasAttachment ? [{ filename: 'Attachment' }] : []
        });
      } catch (e) {
        // Skip problematic rows
      }
    });
    
    return emails;
  }

  // ==================== CONFIDENCE CALCULATION ====================

  calculateConfidence(email, mode = 'dom') {
    const confidence = Number(email?.confidence ?? 0);
    if (!Number.isFinite(confidence)) return 0;
    return Math.max(0, Math.min(100, confidence <= 1 ? confidence * 100 : confidence));
  }

  detectType(email) {
    const text = String([
      email?.subject || '',
      email?.snippet || '',
      email?.sender || ''
    ].join(' ')).toLowerCase();
    if (/\bpayment request\b|\bamount due\b|\bdue date\b/.test(text)) return 'payment_request';
    if (/\binvoice\b|\bbill\b|\bstatement\b/.test(text)) return 'invoice';
    return 'unknown';
  }

  extractDetectedFields(email) {
    const combined = `${email.subject} ${email.sender} ${email.snippet}`;
    const detected = {};
    
    // Vendor (sender)
    if (email.sender && email.sender !== 'Unknown') {
      detected.vendor = email.sender;
    }
    
    // Amount - multiple currency support
    const amountPatterns = [
      /[\$€£][\d,]+\.?\d*/,  // $1,234.56
      /\b(USD|EUR|GBP|CHF)\s*[\d,]+\.?\d*/i,  // USD 1234.56
      /[\d,]+\.?\d*\s*(USD|EUR|GBP|CHF)/i  // 1234.56 EUR
    ];
    for (const pattern of amountPatterns) {
      const match = combined.match(pattern);
      if (match) {
        detected.amount = match[0];
        // Extract currency
        const currMatch = match[0].match(/USD|EUR|GBP|CHF|[\$€£]/i);
        if (currMatch) {
          const currMap = { '$': 'USD', '€': 'EUR', '£': 'GBP' };
          detected.currency = currMap[currMatch[0]] || currMatch[0].toUpperCase();
        }
        break;
      }
    }
    
    // Attachment
    if (email.attachments?.length > 0) {
      detected.attachment = true;
      detected.attachmentCount = email.attachments.length;
    }
    
    // Invoice/PO number - extended patterns
    const poPatterns = [
      /(?:inv|invoice)[-#:\s]*(\d{4,})/i,
      /(?:po|p\.o\.|purchase\s*order)[-#:\s]*(\d{4,})/i,
      /(?:order|ref|reference)[-#:\s]*(\d{4,})/i,
      /#(\d{5,})/  // Generic # followed by numbers
    ];
    for (const pattern of poPatterns) {
      const match = combined.match(pattern);
      if (match) {
        detected.invoiceNumber = match[1];
        break;
      }
    }
    
    // Due Date extraction - comprehensive patterns
    const dueDateInfo = this.extractDueDate(combined);
    if (dueDateInfo) {
      detected.dueDate = dueDateInfo.dateString;
      detected.dueDateParsed = dueDateInfo.date;
      detected.isOverdue = dueDateInfo.isOverdue;
      detected.daysUntilDue = dueDateInfo.daysUntilDue;
    }
    
    // Payment terms
    const termsMatch = combined.match(/(?:net|terms?)[\s:]*(\d{1,3})/i);
    if (termsMatch) {
      detected.paymentTerms = `Net ${termsMatch[1]}`;
    }
    
    return detected;
  }

  extractDueDate(text) {
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    
    // Due date keyword patterns
    const dueDatePatterns = [
      // "due by MM/DD/YYYY" or "due: MM/DD/YYYY"
      /due\s*(?:by|date|on)?[:\s]*(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4})/i,
      // "payment due MM/DD/YYYY"
      /payment\s+due[:\s]*(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4})/i,
      // "payable by MM/DD/YYYY"
      /payable\s+(?:by|before)[:\s]*(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4})/i,
      // "expires MM/DD/YYYY"
      /expir(?:es?|y)[:\s]*(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4})/i,
      // "deadline MM/DD/YYYY"
      /deadline[:\s]*(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4})/i
    ];
    
    for (const pattern of dueDatePatterns) {
      const match = text.match(pattern);
      if (match) {
        const parsed = this.parseDate(match[1]);
        if (parsed) {
          const daysUntil = Math.ceil((parsed - today) / (1000 * 60 * 60 * 24));
          return {
            dateString: match[1],
            date: parsed.toISOString(),
            isOverdue: daysUntil < 0,
            daysUntilDue: daysUntil
          };
        }
      }
    }
    
    // Also check for relative due dates
    const relativePatterns = [
      { pattern: /due\s+today/i, days: 0 },
      { pattern: /due\s+tomorrow/i, days: 1 },
      { pattern: /due\s+(?:in\s+)?(\d+)\s+days?/i, daysCapture: 1 },
      { pattern: /overdue/i, days: -1 },
      { pattern: /past\s+due/i, days: -1 },
      { pattern: /immediate(?:ly)?/i, days: 0 }
    ];
    
    for (const { pattern, days, daysCapture } of relativePatterns) {
      const match = text.match(pattern);
      if (match) {
        const daysValue = daysCapture ? parseInt(match[daysCapture], 10) : days;
        const dueDate = new Date(today);
        dueDate.setDate(dueDate.getDate() + daysValue);
        return {
          dateString: daysValue === 0 ? 'Today' : daysValue === 1 ? 'Tomorrow' : 
                     daysValue < 0 ? 'Overdue' : `${daysValue} days`,
          date: dueDate.toISOString(),
          isOverdue: daysValue < 0,
          daysUntilDue: daysValue
        };
      }
    }
    
    return null;
  }

  parseDate(dateStr) {
    if (!dateStr) return null;
    
    // Normalize separators
    const normalized = dateStr.replace(/[\.\-]/g, '/');
    const parts = normalized.split('/');
    
    if (parts.length !== 3) return null;
    
    let year, month, day;
    
    // Determine format based on part lengths
    if (parts[2].length === 4) {
      // MM/DD/YYYY or DD/MM/YYYY
      const first = parseInt(parts[0], 10);
      const second = parseInt(parts[1], 10);
      year = parseInt(parts[2], 10);
      
      // Assume MM/DD/YYYY for US-style
      if (first > 12) {
        day = first;
        month = second - 1;
      } else {
        month = first - 1;
        day = second;
      }
    } else if (parts[0].length === 4) {
      // YYYY/MM/DD
      year = parseInt(parts[0], 10);
      month = parseInt(parts[1], 10) - 1;
      day = parseInt(parts[2], 10);
    } else {
      // MM/DD/YY
      month = parseInt(parts[0], 10) - 1;
      day = parseInt(parts[1], 10);
      year = parseInt(parts[2], 10);
      if (year < 100) year += 2000;
    }
    
    const date = new Date(year, month, day);
    
    // Validate the date
    if (isNaN(date.getTime()) || date.getMonth() !== month) {
      return null;
    }
    
    return date;
  }

  // ==================== QUEUE MANAGEMENT ====================

  // Invoice status constants
  static STATUS = {
    NEW: 'new',
    PENDING_APPROVAL: 'pending_approval',
    APPROVED: 'approved',
    POSTED: 'posted',
    PAID: 'paid',
    REJECTED: 'rejected'
  };

  async addToQueue(email) {
    const markProcessed = async () => {
      let changed = false;
      const threadId = this.getThreadId(email);
      const backendId = this.getBackendEmailId(email);
      if (threadId && !this.processedIds.has(threadId)) {
        this.processedIds.add(threadId);
        changed = true;
      }
      if (backendId && !this.processedIds.has(backendId)) {
        this.processedIds.add(backendId);
        changed = true;
      }
      if (changed) {
        await this.saveProcessedIds();
      }
    };

    // Don't add exact ID duplicates
    const threadId = this.getThreadId(email);
    const backendId = this.getBackendEmailId(email);
    if (this.queue.find(e =>
      (threadId && this.getThreadId(e) === threadId) ||
      (backendId && this.getBackendEmailId(e) === backendId)
    )) return;
    
    // Initialize status tracking
    email.status = ClearledgrQueueManager.STATUS.NEW;
    email.statusHistory = [{
      status: ClearledgrQueueManager.STATUS.NEW,
      timestamp: new Date().toISOString(),
      source: 'gmail_extension'
    }];
    
    // Apply initial "Clearledgr/Invoices" label when first detected
    const labelTarget = this.getLabelTargetId(email);
    await this.safeSendMessage({
      action: 'applyLabel',
      emailId: labelTarget,
      label: 'Clearledgr/Invoices'
    });

    // Check for potential duplicates (same vendor + similar amount)
    const duplicateInfo = this.checkForDuplicates(email);
    if (duplicateInfo.isDuplicate) {
      email.duplicateWarning = duplicateInfo;
      email.isDuplicate = true;
      email.confidence = Math.min(email.confidence, 0.70); // Lower confidence for duplicates
    }

    const agentDecision = email.agentDecision?.decision || email.decision;

    // Agent-directed autonomy (deep decisioning)
    if (agentDecision === 'reject') {
      await this.rejectInvoice(email, 'agent_reject');
      await markProcessed();
      return;
    }

    if (agentDecision === 'auto_approve' && !duplicateInfo.isDuplicate) {
      const result = await this.submitForApproval(email);
      if (result?.status || result?.success) await markProcessed();
      return;
    }

    if (agentDecision === 'send_for_approval') {
      const result = await this.submitForApproval(email);
      if (result?.status || result?.success) await markProcessed();
      return;
    }

    // AUTONOMOUS BEHAVIOR: Auto-process high confidence items (only if not duplicate)
    if (email.confidence >= 0.95 && !duplicateInfo.isDuplicate) {
      // High confidence - submit for auto-approval
      const result = await this.submitForApproval(email);
      if (result?.status || result?.success) await markProcessed();
      return;
    }

    // Medium/low confidence or duplicates - add to review queue for HITL
    email.status = ClearledgrQueueManager.STATUS.PENDING_APPROVAL;
    this.updateStatusHistory(email, ClearledgrQueueManager.STATUS.PENDING_APPROVAL, 'needs_review');
    this.queue.push(email);
    
    // Apply "Needs Review" label
    await this.safeSendMessage({
      action: 'applyLabel',
      emailId: labelTarget,
      label: 'Clearledgr/Needs Review'
    });
    
    // Sort by: overdue first, then duplicates, then by confidence
    this.queue.sort((a, b) => {
      // Overdue items first
      const aOverdue = a.detected?.isOverdue ? 1 : 0;
      const bOverdue = b.detected?.isOverdue ? 1 : 0;
      if (bOverdue !== aOverdue) return bOverdue - aOverdue;
      
      // Duplicates next (need human review)
      const aDup = a.duplicateWarning?.isDuplicate ? 1 : 0;
      const bDup = b.duplicateWarning?.isDuplicate ? 1 : 0;
      if (bDup !== aDup) return bDup - aDup;
      
      // Then by confidence (highest first)
      return b.confidence - a.confidence;
    });

    await markProcessed();
  }

  checkForDuplicates(email) {
    const vendor = email.detected?.vendor || email.sender;
    const amount = email.detected?.amount;
    
    // Check against existing queue
    const queueDuplicates = this.queue.filter(existing => {
      const existingVendor = existing.detected?.vendor || existing.sender;
      const existingAmount = existing.detected?.amount;
      
      // Same vendor
      if (!this.vendorsMatch(vendor, existingVendor)) return false;
      
      // Similar amount (within 1%)
      if (amount && existingAmount && this.amountsMatch(amount, existingAmount)) {
        return true;
      }
      
      // Same invoice number
      if (email.detected?.invoiceNumber && existing.detected?.invoiceNumber) {
        if (email.detected.invoiceNumber === existing.detected.invoiceNumber) {
          return true;
        }
      }
      
      return false;
    });
    
    // Check against recently processed (from storage)
    const recentlyProcessed = this.getRecentlyProcessed();
    const processedDuplicates = recentlyProcessed.filter(processed => {
      if (!this.vendorsMatch(vendor, processed.vendor)) return false;
      if (amount && processed.amount && this.amountsMatch(amount, processed.amount)) {
        return true;
      }
      return false;
    });
    
    const allDuplicates = [...queueDuplicates, ...processedDuplicates];
    
    if (allDuplicates.length > 0) {
      return {
        isDuplicate: true,
        count: allDuplicates.length,
        matches: allDuplicates.slice(0, 3).map(d => ({
          vendor: d.detected?.vendor || d.vendor || d.sender,
          amount: d.detected?.amount || d.amount,
          date: d.date || d.addedAt
        })),
        reason: queueDuplicates.length > 0 ? 'In queue' : 'Recently processed'
      };
    }
    
    return { isDuplicate: false };
  }

  vendorsMatch(vendor1, vendor2) {
    if (!vendor1 || !vendor2) return false;
    
    // Normalize vendor names for comparison
    const normalize = (v) => v.toLowerCase()
      .replace(/[^a-z0-9]/g, '')
      .replace(/(inc|llc|ltd|corp|co|company)$/g, '');
    
    const v1 = normalize(vendor1);
    const v2 = normalize(vendor2);
    
    // Exact match after normalization
    if (v1 === v2) return true;
    
    // One contains the other
    if (v1.includes(v2) || v2.includes(v1)) return true;
    
    // Levenshtein distance for typos (only for longer names)
    if (v1.length > 5 && v2.length > 5) {
      const distance = this.levenshteinDistance(v1, v2);
      if (distance <= 2) return true;
    }
    
    return false;
  }

  amountsMatch(amount1, amount2) {
    // Parse amounts to numbers
    const parse = (a) => parseFloat(String(a).replace(/[^0-9.-]/g, ''));
    const num1 = parse(amount1);
    const num2 = parse(amount2);
    
    if (isNaN(num1) || isNaN(num2)) return false;
    
    // Exact match
    if (num1 === num2) return true;
    
    // Within 1% tolerance (for rounding differences)
    const tolerance = Math.max(num1, num2) * 0.01;
    return Math.abs(num1 - num2) <= tolerance;
  }

  levenshteinDistance(str1, str2) {
    const m = str1.length;
    const n = str2.length;
    const dp = Array(m + 1).fill(null).map(() => Array(n + 1).fill(0));
    
    for (let i = 0; i <= m; i++) dp[i][0] = i;
    for (let j = 0; j <= n; j++) dp[0][j] = j;
    
    for (let i = 1; i <= m; i++) {
      for (let j = 1; j <= n; j++) {
        if (str1[i - 1] === str2[j - 1]) {
          dp[i][j] = dp[i - 1][j - 1];
        } else {
          dp[i][j] = 1 + Math.min(dp[i - 1][j], dp[i][j - 1], dp[i - 1][j - 1]);
        }
      }
    }
    
    return dp[m][n];
  }

  getRecentlyProcessed() {
    // Get from localStorage (last 30 days)
    try {
      const stored = localStorage.getItem('clearledgr_processed_history');
      if (!stored) return [];
      
      const history = JSON.parse(stored);
      const thirtyDaysAgo = Date.now() - (30 * 24 * 60 * 60 * 1000);
      
      return history.filter(item => item.timestamp > thirtyDaysAgo);
    } catch {
      return [];
    }
  }

  addToProcessedHistory(email, auditId) {
    try {
      const history = this.getRecentlyProcessed();
      history.push({
        vendor: email.detected?.vendor || email.sender,
        amount: email.detected?.amount,
        invoiceNumber: email.detected?.invoiceNumber,
        auditId,
        timestamp: Date.now()
      });
      
      // Keep only last 500 entries
      const trimmed = history.slice(-500);
      localStorage.setItem('clearledgr_processed_history', JSON.stringify(trimmed));
    } catch (e) {
      console.warn('[Clearledgr] Failed to save processed history:', e);
    }
  }

  // Update status with history tracking
  updateStatusHistory(email, newStatus, source = 'user') {
    email.status = newStatus;
    if (!email.statusHistory) email.statusHistory = [];
    email.statusHistory.push({
      status: newStatus,
      timestamp: new Date().toISOString(),
      source: source
    });

    // Keep inbox row decorations in sync (best-effort).
    this.cacheThreadSnapshot(email);
  }

  // Update status by email ID (called from UI)
  async updateStatus(emailId, newStatus, options = {}) {
    const email = this.queue.find(e =>
      this.getThreadId(e) === emailId || this.getBackendEmailId(e) === emailId
    );
    if (!email) {
      console.warn('[Clearledgr] Email not found in queue:', emailId);
      return false;
    }
    
    // Valid state transitions
    const validTransitions = {
      'pending': ['needs_review', 'approved', 'rejected'],
      'new': ['needs_review', 'approved', 'rejected'],
      'needs_review': ['approved', 'rejected', 'pending_approval'],
      'pending_approval': ['approved', 'rejected', 'posted'],
      'approved': ['posted', 'rejected'],
      'posted': [], // Terminal state
      'rejected': ['pending'] // Can re-open if needed
    };
    
    const currentStatus = email.status || 'pending';
    const allowed = validTransitions[currentStatus] || [];
    
    if (!allowed.includes(newStatus) && newStatus !== currentStatus) {
      console.warn(`[Clearledgr] Invalid transition: ${currentStatus} -> ${newStatus}`);
      // Allow anyway for flexibility, but log warning
    }
    
    // Update status
    this.updateStatusHistory(email, newStatus, options.source || 'user_action');
    
    // Handle specific status changes
    const labelTarget = this.getLabelTargetId(email);
    const activityId = this.getThreadId(email) || this.getBackendEmailId(email);

    if (newStatus === 'rejected') {
      email.rejectionReason = options.rejectionReason || 'unspecified';
      email.rejectedAt = new Date().toISOString();
      
      // Apply rejected label
      await this.safeSendMessage({
        action: 'applyLabel',
        emailId: labelTarget,
        label: 'Clearledgr/Exceptions'
      });
      
      // Remove from needs review label
      await this.safeSendMessage({
        action: 'removeLabel',
        emailId: labelTarget,
        label: 'Clearledgr/Needs Review'
      });
      
      this.addActivity({
        type: 'rejected',
        message: `Rejected: ${email.subject?.substring(0, 40)}... - ${options.rejectionReason}`,
        timestamp: new Date().toISOString(),
        emailId: activityId
      });
      
    } else if (newStatus === 'approved') {
      email.approvedAt = new Date().toISOString();
      
      // Apply approved label and remove needs review
      await this.safeSendMessage({
        action: 'applyLabel',
        emailId: labelTarget,
        label: 'Clearledgr/Processed'
      });
      await this.safeSendMessage({
        action: 'removeLabel',
        emailId: labelTarget,
        label: 'Clearledgr/Needs Review'
      });
      
      this.addActivity({
        type: 'approved',
        message: `Approved: ${email.subject?.substring(0, 40)}...`,
        timestamp: new Date().toISOString(),
        emailId: activityId
      });
      
      // Trigger ERP posting if connected
      this.postToERP(email);
      
    } else if (newStatus === 'posted') {
      email.postedAt = new Date().toISOString();
      this.addToProcessedHistory(email, options.billId);
    }
    
    await this.saveQueue();
    return true;
  }

  // Post approved invoice to ERP
  async postToERP(email) {
    try {
      const settings = await this.getSyncConfig();
      const backendUrl = settings.backendUrl;
      const organizationId = settings.organizationId;
      const backendEmailId = this.getBackendEmailId(email);
      const activityId = this.getThreadId(email) || backendEmailId;
      
      const parsedAmount = this.parseAmount(email.detected?.amount || email.amount);
      if (parsedAmount === null) {
        this.addActivity({
          type: 'error',
          message: 'ERP post skipped: amount missing',
          timestamp: new Date().toISOString(),
          emailId: activityId
        });
        return;
      }

      const response = await fetch(`${backendUrl}/extension/approve-and-post`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email_id: backendEmailId,
          extraction: {
            vendor: email.detected?.vendor || email.vendor,
            amount: parsedAmount,
            currency: email.detected?.currency || 'USD',
            invoice_number: email.detected?.invoiceNumber || email.invoiceNumber,
            due_date: email.detected?.dueDate || email.dueDate
          },
          bank_match: email.bankMatch || null,
          erp_match: email.erpMatch || null,
          override: true,
          organization_id: organizationId,
          user_email: settings.userEmail || null
        })
      });
      
      if (response.ok) {
        const result = await response.json();
        this.updateStatusHistory(email, ClearledgrQueueManager.STATUS.POSTED, 'erp_posted');
        email.billId = result.bill_id;
        this.addToProcessedHistory(email, result.bill_id);
        console.log('[Clearledgr] Posted to ERP:', result);
      }
    } catch (e) {
      console.warn('[Clearledgr] ERP posting failed (backend may be offline):', e.message);
      // Don't change status - will retry when backend is available
    }
  }

  // Reject invoice via backend and update local queue state
  async rejectInvoice(email, reason = 'rejected') {
    try {
      const settings = await this.getSyncConfig();
      const backendUrl = settings.backendUrl;
      const organizationId = settings.organizationId;
      const backendEmailId = this.getBackendEmailId(email);

      await fetch(`${backendUrl}/extension/reject-invoice`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email_id: backendEmailId,
          reason,
          organization_id: organizationId,
          user_email: settings.userEmail || null
        })
      });
    } catch (e) {
      console.warn('[Clearledgr] Reject invoice failed (backend may be offline):', e.message);
    }

    email.status = ClearledgrQueueManager.STATUS.REJECTED;
    this.updateStatusHistory(email, ClearledgrQueueManager.STATUS.REJECTED, reason);

    const labelTarget = this.getLabelTargetId(email);
    await this.safeSendMessage({
      action: 'applyLabel',
      emailId: labelTarget,
      label: 'Clearledgr/Exceptions'
    });

    this.addToProcessedHistory(email, null);
    this.notifyListeners('QUEUE_UPDATED', { count: this.queue.length });
  }

  // Submit invoice for approval via backend workflow
  async submitForApproval(email) {
    try {
      console.log('[Clearledgr] Submitting for approval:', email.subject);
      
      // Get settings for backend URL
      const settings = await this.getSyncConfig();
      const backendUrl = settings.backendUrl;
      const organizationId = settings.organizationId;
      const backendEmailId = this.getBackendEmailId(email);
      
      const parsedAmount = this.parseAmount(email.detected?.amount);
      const amountMissing = parsedAmount === null;
      const insightList = Array.isArray(email.intelligence?.insights)
        ? email.intelligence.insights.map(i => ({ title: i }))
        : [];
      if (amountMissing) {
        insightList.unshift({ title: 'Amount not detected' });
      }

      // Call the new submit-for-approval endpoint with intelligence
      const response = await fetch(`${backendUrl}/extension/submit-for-approval`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email_id: backendEmailId,
          subject: email.subject,
          sender: email.sender,
          vendor: email.detected?.vendor || email.sender,
          amount: parsedAmount ?? 0,
          currency: email.detected?.currency || 'USD',
          invoice_number: email.detected?.invoiceNumber,
          due_date: email.detected?.dueDate,
          po_number: email.detected?.poNumber,
          confidence: email.confidence,
          organization_id: organizationId,
          slack_channel: settings.slackChannel || '#finance-approvals',
          agent_decision: email.agentDecision || null,
          agent_confidence: email.agentDecision?.confidence ?? email.confidence,
          reasoning_summary: email.agentDecision?.reasoning?.summary || null,
          reasoning_factors: email.agentDecision?.reasoning?.factors || null,
          reasoning_risks: email.agentDecision?.reasoning?.risks || null,
          // Pass intelligence data from triage
          vendor_intelligence: email.intelligence?.vendor_info || null,
          policy_compliance: email.intelligence?.policy_compliance || null,
          priority: email.intelligence?.priority ? {
            priority: email.intelligence.priority,
            priority_label: email.intelligence.priority_label,
            days_until_due: email.intelligence.days_until_due,
            alerts: email.intelligence.alerts
          } : null,
          budget_impact: email.intelligence?.budget_warnings?.length > 0 ? email.intelligence.budget_warnings : null,
          potential_duplicates: email.intelligence?.potential_duplicates || 0,
          insights: insightList.length > 0 ? insightList : null
        })
      });
      
      if (!response.ok) {
        const errorText = await response.text().catch(() => '');
        this.setBackendStatus('offline', `submit_for_approval ${response.status}`);
        throw new Error(errorText || `Submit failed (${response.status})`);
      }

      const result = await response.json();

      if (response.ok) {
        this.setBackendStatus('online');
      }
      
      if (result.status === 'auto_approved') {
        // High confidence - auto-approved and posted
        this.updateStatusHistory(email, ClearledgrQueueManager.STATUS.POSTED, 'auto_approved');
        this.addToProcessedHistory(email, result.erp_result?.bill_id);

        // If the email was already queued, remove it to avoid duplicates
        const threadId = this.getThreadId(email);
        const backendId = this.getBackendEmailId(email);
        const existingIndex = this.queue.findIndex(e =>
          (threadId && this.getThreadId(e) === threadId) ||
          (backendId && this.getBackendEmailId(e) === backendId)
        );
        if (existingIndex !== -1) {
          this.queue.splice(existingIndex, 1);
          await this.saveQueue();
        }
        
        this.addActivity({
          type: 'auto_posted',
          message: `Auto-posted: ${email.sender} - ${email.detected?.amount}`,
          timestamp: new Date().toISOString(),
          emailId: threadId || backendId,
          billId: result.erp_result?.bill_id
        });
        
        console.log('[Clearledgr] Auto-approved and posted:', result);
        
      } else if (result.status === 'pending_approval') {
        // Sent to Slack for approval
        email.status = ClearledgrQueueManager.STATUS.PENDING_APPROVAL;
        email.slackChannel = result.slack_channel;
        email.slackTs = result.slack_ts;
        this.updateStatusHistory(email, ClearledgrQueueManager.STATUS.PENDING_APPROVAL, 'sent_to_slack');

        // Add or update queue entry for tracking (dedupe by Gmail/thread ID)
        const threadId = this.getThreadId(email);
        const backendId = this.getBackendEmailId(email);
        const existing = this.queue.find(e =>
          (threadId && this.getThreadId(e) === threadId) ||
          (backendId && this.getBackendEmailId(e) === backendId)
        );
        if (existing) {
          Object.assign(existing, email);
        } else {
          this.queue.push(email);
        }
        await this.saveQueue();
        
        this.addActivity({
          type: 'sent_for_approval',
          message: `Sent for approval: ${email.sender} - ${email.detected?.amount}`,
          timestamp: new Date().toISOString(),
          emailId: threadId || backendId
        });
        
        console.log('[Clearledgr] Sent to Slack for approval:', result);
        
      } else {
        console.warn('[Clearledgr] Submit failed:', result);
      }
      
      this.notifyListeners('QUEUE_UPDATED', { count: this.queue.length });
      return result;
      
    } catch (error) {
      console.error('[Clearledgr] Submit for approval failed:', error);
      this.setBackendStatus('offline', error.message);
      // Fallback to legacy autoPost
      return await this.autoPost(email);
    }
  }

  // Parse amount string to number
  parseAmount(amountStr) {
    if (amountStr === null || amountStr === undefined || amountStr === '') return null;
    if (typeof amountStr === 'number') {
      if (!Number.isFinite(amountStr)) return null;
      if (Number.isInteger(amountStr) && amountStr >= 1900 && amountStr <= 2100) return null;
      return amountStr;
    }
    const cleaned = String(amountStr).replace(/[^0-9.-]/g, '');
    if (!cleaned) return null;
    const value = parseFloat(cleaned);
    if (!Number.isFinite(value)) return null;
    if (Number.isInteger(value) && value >= 1900 && value <= 2100) return null;
    return value;
  }

  // Legacy autonomous posting (fallback)
  async autoPost(email) {
    try {
      console.log('[Clearledgr Agent] Auto-posting:', email.subject);
      const backendId = this.getBackendEmailId(email);
      const labelTarget = this.getLabelTargetId(email);
      
      const result = await this.safeSendMessage({
        action: 'POST_TO_LEDGER',
        data: {
          emailId: backendId,
          vendor: email.sender,
          amount: email.detected?.amount || '$0.00',
          currency: email.detected?.currency || 'USD',
          invoiceNumber: email.detected?.invoiceNumber,
          dueDate: email.detected?.dueDate,
          confidence: email.confidence,
          autoPosted: true,
          // Pass extraction data if available
          _extraction: email.detected,
          _bankMatch: email.bankMatch,
          _erpMatch: email.erpMatch
        }
      });

      if (result?.success) {
        this.batchStats.posted++;
        this.batchStats.totalAmount += parseFloat((email.detected?.amount || '0').replace(/[^0-9.]/g, ''));
        
        // Add to processed history for duplicate detection
        this.addToProcessedHistory(email, result.ledgerId);
        
        // Apply appropriate label based on email type
        const labelMap = {
          'invoice': 'Clearledgr/Invoices',
          'payment_request': 'Clearledgr/Payment Requests',
          'payment request': 'Clearledgr/Payment Requests'
        };
        const typeLabel = labelMap[email.type] || 'Clearledgr/Invoices';
        
        // Apply type label and processed label
        await this.safeSendMessage({
          action: 'applyLabel',
          emailId: labelTarget,
          label: typeLabel
        });
        await this.safeSendMessage({
          action: 'applyLabel',
          emailId: labelTarget,
          label: 'Clearledgr/Processed'
        });
        
        this.addToActivityFeed(`Auto-posted: ${email.sender} - ${result.ledgerId}`);
        this.notifyListeners('AUTO_POSTED', { 
          email, 
          auditId: result.ledgerId,
          erpDocument: result.erpDocument
        });
        
      } else if (result?.blocked) {
        // HITL: Backend blocked due to low confidence
        console.log('[Clearledgr Agent] Post blocked by HITL:', result.reason);
        email.blockedReason = result.reason;
        email.mismatches = result.mismatches;
        this.queue.push(email);
        await this.safeSendMessage({
          action: 'applyLabel',
          emailId: labelTarget,
          label: 'Clearledgr/Needs Review'
        });
        this.addToActivityFeed(`Review needed: ${email.sender} (${result.reason})`);
        this.notifyListeners('QUEUE_UPDATED', { count: this.queue.length });
        
      } else {
        // Posting failed
        throw new Error(result?.error || 'Post returned unsuccessful');
      }
    } catch (e) {
      console.warn('[Clearledgr Agent] Auto-post failed, adding to review:', e.message);
      // If auto-post fails, add to review queue and label for review
      this.queue.push(email);
      await this.safeSendMessage({
        action: 'applyLabel',
        emailId: labelTarget,
        label: 'Clearledgr/Needs Review'
      });
      this.addToActivityFeed(`Failed: ${email.sender} - ${e.message}`);
      this.notifyListeners('QUEUE_UPDATED', { count: this.queue.length });
    }
  }

  // Activity feed for transparency
  addToActivityFeed(message) {
    const feed = this.activityFeed || [];
    feed.unshift({ time: new Date(), message });
    this.activityFeed = feed.slice(0, 20); // Keep last 20
    this.notifyListeners('ACTIVITY', { message });
  }

  removeFromQueue(emailId) {
    this.queue = this.queue.filter(e =>
      this.getThreadId(e) !== emailId && this.getBackendEmailId(e) !== emailId
    );
    this.saveQueue();
    this.notifyListeners('QUEUE_UPDATED', { count: this.queue.length });
  }

  getQueue() {
    return [...this.queue];
  }

  getQueueCount() {
    return this.queue.length;
  }

  getCurrentEmail() {
    return this.queue[this.currentIndex] || null;
  }

  getNextEmail() {
    if (this.currentIndex < this.queue.length - 1) {
      this.currentIndex++;
      return this.queue[this.currentIndex];
    }
    return null;
  }

  getPreviousEmail() {
    if (this.currentIndex > 0) {
      this.currentIndex--;
      return this.queue[this.currentIndex];
    }
    return null;
  }

  // ==================== BATCH PROCESSING ====================

  startBatchProcessing() {
    this.currentIndex = 0;
    this.batchStats = { processed: 0, posted: 0, exceptions: 0, skipped: 0, totalAmount: 0 };
    this.notifyListeners('BATCH_STARTED', { total: this.queue.length });
    return this.getCurrentEmail();
  }

  markAsProcessed(emailId, status, amount = 0) {
    this.batchStats.processed++;
    
    switch (status) {
      case 'posted':
        this.batchStats.posted++;
        this.batchStats.totalAmount += amount;
        break;
      case 'exception':
        this.batchStats.exceptions++;
        break;
      case 'skipped':
        this.batchStats.skipped++;
        break;
    }
    
    // Remove from queue
    this.removeFromQueue(emailId);
    
    // Check if batch complete
    if (this.queue.length === 0) {
      this.notifyListeners('BATCH_COMPLETE', this.batchStats);
      return null;
    }
    
    // Reset index since we removed an item
    this.currentIndex = Math.min(this.currentIndex, this.queue.length - 1);
    
    this.notifyListeners('ITEM_PROCESSED', {
      remaining: this.queue.length,
      stats: this.batchStats
    });
    
    return this.getCurrentEmail();
  }

  skipAll() {
    const skippedCount = this.queue.length;
    this.batchStats.skipped += skippedCount;
    this.queue = [];
    this.saveQueue();
    this.notifyListeners('BATCH_COMPLETE', this.batchStats);
  }

  getBatchStats() {
    return { ...this.batchStats };
  }

  getActivityFeed() {
    return [...(this.activityFeed || [])];
  }

  // Get only items needing human review (confidence < 95%)
  getReviewQueue() {
    return this.queue.filter(e => e.confidence < 0.95);
  }

  // ==================== EVENT SYSTEM ====================

  subscribe(callback) {
    this.listeners.push(callback);
    return () => {
      this.listeners = this.listeners.filter(l => l !== callback);
    };
  }

  notifyListeners(type, data) {
    this.listeners.forEach(listener => {
      try {
        listener({ type, data });
      } catch (e) {
        console.warn('[Clearledgr Queue] Listener error:', e);
      }
    });
  }

  // ==================== GMAIL NAVIGATION ====================

  navigateToEmail(emailId) {
    if (!emailId) {
      console.warn('[Clearledgr] No email ID for navigation');
      return false;
    }
    
    // Find the email in our queue to get subject for matching
    const email = this.queue.find(e =>
      this.getThreadId(e) === emailId || this.getBackendEmailId(e) === emailId
    );
    if (!email) {
      console.warn('[Clearledgr] Email not in queue:', emailId);
      return false;
    }
    
    // Check if we're already viewing this email (in thread view)
    const currentSubject = this.getCurrentEmailSubject();
    if (currentSubject && this.subjectsMatch(currentSubject, email.subject)) {
      console.log('[Clearledgr] Already viewing this email');
      return true;
    }
    
    // Check if we're in inbox/list view or thread view
    const isInListView = this.isInListView();
    
    if (!isInListView) {
      // We're viewing a different email - go back to inbox first
      console.log('[Clearledgr] Not in list view, navigating to inbox first');
      this.goToInbox();
      
      // Wait for inbox to load, then find the email
      setTimeout(() => {
        this.findAndClickEmail(email);
      }, 500);
      return true;
    }
    
    // We're in list view - find and click the email
    return this.findAndClickEmail(email);
  }

  getCurrentEmailSubject() {
    // Get subject from thread view header
    const subjectEl = document.querySelector('h2[data-thread-perm-id]') ||
                     document.querySelector('[role="main"] h2') ||
                     document.querySelector('.ha h2');
    return subjectEl?.textContent?.trim();
  }

  subjectsMatch(subject1, subject2) {
    if (!subject1 || !subject2) return false;
    // Normalize and compare (Gmail may add prefixes like "Re:", "Fwd:")
    const normalize = (s) => s.replace(/^(Re:|Fwd:|Fw:)\s*/gi, '').trim().toLowerCase();
    return normalize(subject1) === normalize(subject2) || 
           subject1.includes(subject2) || 
           subject2.includes(subject1);
  }

  isInListView() {
    // Check if inbox rows are visible (indicates list view)
    const rows = document.querySelectorAll('[role="main"] tr[jscontroller]');
    return rows.length > 5; // More than a few rows = list view
  }

  goToInbox() {
    // Click the Inbox link or use hash navigation
    const inboxLink = document.querySelector('a[href*="#inbox"]') ||
                     document.querySelector('[data-tooltip="Inbox"]');
    if (inboxLink) {
      inboxLink.click();
    } else {
      window.location.hash = '#inbox';
    }
  }

  findAndClickEmail(email) {
    // Get all inbox rows
    const rows = document.querySelectorAll('[role="main"] tr[jscontroller]');
    
    // Strategy 1: Try to find by row index if we have it and it's still valid
    if (email.rowIndex !== undefined && rows[email.rowIndex]) {
      const row = rows[email.rowIndex];
      const rowSubject = row.querySelector('.bog')?.textContent?.trim();
      
      // Verify it's still the same email
      if (rowSubject && rowSubject === email.subject) {
        if (this.clickEmailRow(row)) {
          console.log('[Clearledgr] Navigated via row index:', email.rowIndex);
          return true;
        }
      }
    }
    
    // Strategy 2: Find row by matching subject
    for (const row of rows) {
      const rowSubject = row.querySelector('.bog')?.textContent?.trim();
      if (rowSubject === email.subject) {
        if (this.clickEmailRow(row)) {
          console.log('[Clearledgr] Navigated via subject match:', email.subject.slice(0, 30));
          return true;
        }
      }
    }
    
    // Strategy 3: Find by sender + partial subject match
    for (const row of rows) {
      const rowSender = row.querySelector('[email]')?.getAttribute('name') || 
                       row.querySelector('.yX.xY span')?.textContent?.trim();
      const rowSubject = row.querySelector('.bog')?.textContent?.trim() || '';
      
      if (rowSender === email.sender && rowSubject.includes(email.subject.slice(0, 20))) {
        if (this.clickEmailRow(row)) {
          console.log('[Clearledgr] Navigated via sender+subject match');
          return true;
        }
      }
    }
    
    console.warn('[Clearledgr] Could not find email row to navigate');
    return false;
  }

  clickEmailRow(row) {
    try {
      // Try to click the subject link (most reliable)
      const subjectLink = row.querySelector('.bog')?.closest('td')?.querySelector('a') ||
                         row.querySelector('td.xY a') ||
                         row.querySelector('a');
      
      if (subjectLink && subjectLink.href) {
        subjectLink.click();
        return true;
      }
      
      // Try clicking the subject span directly
      const subjectSpan = row.querySelector('.bog');
      if (subjectSpan) {
        subjectSpan.click();
        return true;
      }
      
      // Last resort: click the row
      row.click();
      return true;
    } catch (e) {
      console.warn('[Clearledgr] Click failed:', e);
      return false;
    }
  }

  async archiveCurrentEmail() {
    // Simulate archive action (in real impl, use Gmail API)
    console.log('[Clearledgr Queue] Archiving email...');
    
    // Try to click Gmail's archive button
    const archiveBtn = document.querySelector('[aria-label="Archive"]') ||
                       document.querySelector('[data-tooltip="Archive"]');
    if (archiveBtn) {
      archiveBtn.click();
      return true;
    }
    
    return false;
  }

  // ==================== CLEANUP ====================

  destroy() {
    this.stopPeriodicScan();
    this.listeners = [];
  }
}

export { ClearledgrQueueManager };

// Export for use in other scripts
if (typeof window !== 'undefined') {
  window.ClearledgrQueueManager = ClearledgrQueueManager;
}
