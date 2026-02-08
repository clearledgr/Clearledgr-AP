importScripts("config.js");
// Clearledgr Background Service Worker
// Note: config.js is injected by build.sh - don't add importScripts here

// ==========================================================================
// RETRY CONFIGURATION - Exponential backoff for API calls
// ==========================================================================
const RetryConfig = {
  maxRetries: 3,
  baseDelay: 1000,
  maxDelay: 30000,
  backoffMultiplier: 2,
  retryableStatusCodes: [408, 429, 500, 502, 503, 504],
};

function calculateBackoff(attempt) {
  const delay = RetryConfig.baseDelay * Math.pow(RetryConfig.backoffMultiplier, attempt);
  const jitter = Math.random() * 0.3 * delay;
  return Math.min(delay + jitter, RetryConfig.maxDelay);
}

async function fetchWithRetry(url, options = {}, maxRetries = RetryConfig.maxRetries) {
  let lastError;
  
  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 30000);
      
      const response = await fetch(url, {
        ...options,
        signal: controller.signal,
      });
      
      clearTimeout(timeoutId);
      
      if (!response.ok && RetryConfig.retryableStatusCodes.includes(response.status)) {
        if (attempt < maxRetries) {
          const delay = calculateBackoff(attempt);
          console.log(`[Clearledgr] Request to ${url} failed (${response.status}), retry ${attempt + 1}/${maxRetries} in ${Math.round(delay)}ms`);
          await new Promise(r => setTimeout(r, delay));
          continue;
        }
      }
      
      return response;
    } catch (error) {
      lastError = error;
      
      if (error.name === 'AbortError' || error.message?.includes('fetch')) {
        if (attempt < maxRetries) {
          const delay = calculateBackoff(attempt);
          console.log(`[Clearledgr] Network error, retry ${attempt + 1}/${maxRetries} in ${Math.round(delay)}ms`);
          await new Promise(r => setTimeout(r, delay));
          continue;
        }
      }
      
      throw error;
    }
  }
  
  throw lastError || new Error('Request failed after retries');
}

// ==========================================================================
// INBOXSDK INTEGRATION - Required for pageWorld.js injection
// ==========================================================================
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === 'inboxsdk__injectPageWorld' && sender.tab) {
    if (chrome.scripting) {
      let documentIds;
      let frameIds;
      if (sender.documentId) {
        documentIds = [sender.documentId];
      } else {
        frameIds = [sender.frameId];
      }
      chrome.scripting.executeScript({
        target: { tabId: sender.tab.id, documentIds, frameIds },
        world: 'MAIN',
        files: ['dist/pageWorld.js'],
      });
      sendResponse(true);
    } else {
      sendResponse(false);
    }
    return true;
  }
});

// Get backend URL from settings (defaults to localhost for dev)
function normalizeBackendUrl(raw) {
  let url = String(raw || '').trim();
  if (!url) return 'http://127.0.0.1:8010';
  if (!/^https?:\/\//i.test(url)) {
    url = `http://${url}`;
  }
  if (url.endsWith('/v1')) {
    url = url.slice(0, -3);
  }
  try {
    const parsed = new URL(url);
    if (parsed.hostname === '0.0.0.0' || parsed.hostname === 'localhost') {
      parsed.hostname = '127.0.0.1';
    }
    if (parsed.hostname === '127.0.0.1' && parsed.port === '8000') {
      parsed.port = '8010';
    }
    return parsed.toString().replace(/\/+$/, '');
  } catch (_) {
    return url.replace(/\/+$/, '');
  }
}

async function getMergedSyncSettings() {
  const data = await chrome.storage.sync.get([
    'settings',
    'backendUrl',
    'organizationId',
    'userEmail',
    'slackChannel'
  ]);
  const nested = data.settings || {};
  return {
    ...nested,
    // Top-level keys take precedence for backward compatibility
    backendUrl: data.backendUrl || nested.backendUrl || nested.apiEndpoint || null,
    organizationId: data.organizationId || nested.organizationId || null,
    userEmail: data.userEmail || nested.userEmail || null,
    slackChannel: data.slackChannel || nested.slackChannel || null,
  };
}

async function getBackendUrl() {
  const settings = await getMergedSyncSettings();
  return normalizeBackendUrl(settings.backendUrl);
}

async function getOrganizationId() {
  const settings = await getMergedSyncSettings();
  return settings.organizationId || 'default';
}

async function getUserEmail() {
  const settings = await getMergedSyncSettings();
  return settings.userEmail || 'extension';
}

// Label configuration - matches screenshot hierarchy
const CLEARLEDGR_LABELS = [
  { name: 'Clearledgr', color: 'green', parent: null },
  { name: 'Clearledgr/Exceptions', color: 'pink', parent: 'Clearledgr' },
  { name: 'Clearledgr/Invoices', color: 'red', parent: 'Clearledgr' },
  { name: 'Clearledgr/Needs Review', color: 'orange', parent: 'Clearledgr' },
  { name: 'Clearledgr/Payment Requests', color: 'green', parent: 'Clearledgr' },
  { name: 'Clearledgr/Processed', color: 'cyan', parent: 'Clearledgr' }
];
const AUTOPILOT_CONNECT_ATTEMPT_KEY = 'clearledgr_autopilot_connect_attempt';

async function getAutopilotConnectAttempt() {
  const result = await chrome.storage.local.get([AUTOPILOT_CONNECT_ATTEMPT_KEY]);
  return result[AUTOPILOT_CONNECT_ATTEMPT_KEY] || null;
}

async function setAutopilotConnectAttempt(update) {
  const current = await getAutopilotConnectAttempt();
  const next = {
    ...(current || {}),
    ...(update || {}),
    updatedAt: Date.now()
  };
  await chrome.storage.local.set({ [AUTOPILOT_CONNECT_ATTEMPT_KEY]: next });
  return next;
}

// 1. Installation & Initialization
chrome.runtime.onInstalled.addListener(async () => {
  console.log('[Clearledgr] Extension installed');
  
  // Set default settings
  const existing = await chrome.storage.sync.get(['settings', 'backendUrl', 'organizationId', 'userEmail']);
  const nested = existing.settings || {};
  const backendUrl = normalizeBackendUrl(existing.backendUrl || nested.backendUrl || nested.apiEndpoint || 'http://127.0.0.1:8010');
  const organizationId = existing.organizationId || nested.organizationId || 'default';
  const userEmail = existing.userEmail || nested.userEmail || 'extension';

  await chrome.storage.sync.set({
    apiKey: null,
    settings: {
      ...nested,
      backendUrl,
      organizationId,
      userEmail,
      autoMatch: true,
      notifications: true
    },
    backendUrl,
    organizationId,
    userEmail
  });
});

// 2. Message Handling
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === 'getAutopilotStatus') {
    (async () => {
      try {
        const status = await getAutopilotStatus();
        const attempt = await getAutopilotConnectAttempt();
        sendResponse({ ...status, autopilot_connect_attempt: attempt });
      } catch (error) {
        const attempt = await getAutopilotConnectAttempt();
        sendResponse({ success: false, error: error.message, autopilot_connect_attempt: attempt });
      }
    })();
    return true;
  }

  if (request.action === 'connectGmailAutopilot') {
    connectGmailAutopilot(request.userId || null)
      .then(result => sendResponse(result))
      .catch(error => sendResponse({ success: false, error: error.message }));
    return true;
  }

  // Handle 'match_email' (and 'CHECK_MATCH' from content script)
  if (request.action === 'match_email' || request.action === 'CHECK_MATCH') {
    if (sender.tab) {
      matchEmailToERP(request.data || request.email, sender.tab.id);
    }
    sendResponse({ status: 'processing' });
    return true; // Async response
  }

  // Handle 'post_to_ledger' (and 'POST_TO_LEDGER' from sidebar)
  if (request.action === 'post_to_ledger' || request.action === 'POST_TO_LEDGER') {
    handlePostToLedger(request.data)
      .then(result => sendResponse(result))
      .catch(error => sendResponse({ success: false, error: error.message }));
    return true; // Async response
  }
  
  // Handle side panel opening (if using native side panel in future)
  if (request.action === 'openSidePanel') {
    if (sender.tab) {
      // Chrome 116+ supports opening side panel programmatically via user action
      // chrome.sidePanel.open({ tabId: sender.tab.id });
    }
  }
  
  // Handle label initialization
  if (request.action === 'initializeLabels') {
    initializeGmailLabels()
      .then(result => sendResponse(result))
      .catch(error => sendResponse({ success: false, error: error.message }));
    return true;
  }
  
  // Handle get label stats
  if (request.action === 'getLabelStats') {
    getLabelStats()
      .then(stats => sendResponse(stats))
      .catch(() => sendResponse({}));
    return true;
  }
  
  // Handle apply label to email
  if (request.action === 'applyLabel') {
    const label = request.label || request.labelName;
    applyLabelToEmail(request.emailId, label)
      .then(result => sendResponse(result))
      .catch(error => sendResponse({ success: false, error: error.message }));
    return true;
  }
  
  // Handle remove label from email
  if (request.action === 'removeLabel') {
    const label = request.label || request.labelName;
    removeLabelFromEmail(request.emailId, label)
      .then(result => sendResponse(result))
      .catch(error => sendResponse({ success: false, error: error.message }));
    return true;
  }
  
  // Handle escalation
  if (request.action === 'escalate') {
    if (sender.tab) {
      escalateToManager(request.data, sender.tab.id);
    }
    sendResponse({ status: 'escalating' });
    return true;
  }
  
  // Handle bulk scan
  if (request.action === 'bulkScan') {
    bulkScanEmails(request.emailIds, request.organizationId)
      .then(result => sendResponse(result))
      .catch(error => sendResponse({ success: false, error: error.message }));
    return true;
  }
  
  // Handle triage single email
  if (request.action === 'triageEmail') {
    triageEmail(request.data)
      .then(result => sendResponse(result))
      .catch(error => sendResponse({ success: false, error: error.message }));
    return true;
  }

  // Search the user's inbox for AP candidates (invoices/bills/payment requests)
  if (request.action === 'searchApEmails') {
    searchApEmails({
      query: request.query,
      maxResults: request.maxResults,
      pageToken: request.pageToken,
      interactive: request.interactive
    })
      .then(result => sendResponse(result))
      .catch(error => sendResponse({ success: false, error: error.message }));
    return true;
  }
});

async function getAutopilotStatus() {
  const backendUrl = await getBackendUrl();
  const response = await fetchWithRetry(`${backendUrl}/autonomous/status`, {
    method: 'GET',
    headers: { 'Accept': 'application/json' }
  });
  if (!response.ok) {
    throw new Error(`autopilot_status_${response.status}`);
  }
  return await response.json();
}

// ==========================================================================
// GMAIL API - AP INBOX SEARCH (Streak-style background scanning)
// ==========================================================================

// NOTE: This query is intentionally conservative (AP workflow focus).
// It should minimize false positives (marketing/newsletters).
const DEFAULT_AP_SCAN_QUERY = [
  'in:inbox',
  // Prefer invoice-like emails with an attachment; this is AP-centric.
  '(has:attachment OR filename:pdf OR filename:png OR filename:jpg OR filename:jpeg OR filename:docx)',
  // Invoice/bill terms common in vendor billing emails.
  '(subject:(invoice OR bill OR \"invoice is available\" OR \"your invoice\" OR \"invoice available\" OR \"payment request\" OR \"amount due\" OR \"total due\" OR \"due date\") OR \"invoice number\" OR \"amount due\" OR \"total due\")',
  // Exclude non-AP subjects
  '-subject:(receipt OR confirmation OR paid OR \"payment received\" OR refund OR chargeback OR dispute OR declined OR \"payment failed\" OR \"card declined\" OR \"security alert\" OR \"password\" OR \"verify\" OR newsletter OR promotion OR offer OR webinar OR event)',
  // Aggressively exclude Gmail categories that are usually noise.
  '-category:promotions',
  '-category:social',
  '-category:updates'
].join(' ');

async function searchApEmails({ query, maxResults = 50, pageToken = null, interactive = true } = {}) {
  const token = await getAuthToken(!!interactive);
  const q = String(query || DEFAULT_AP_SCAN_QUERY).trim() || DEFAULT_AP_SCAN_QUERY;

  const url = new URL(`${GMAIL_API}/messages`);
  url.searchParams.set('q', q);
  url.searchParams.set('maxResults', String(Math.max(1, Math.min(500, Number(maxResults) || 50))));
  url.searchParams.set('includeSpamTrash', 'false');
  if (pageToken) url.searchParams.set('pageToken', String(pageToken));

  const response = await fetch(url.toString(), {
    headers: { Authorization: `Bearer ${token}` }
  });

  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err?.error?.message || `Gmail search failed: ${response.status}`);
  }

  const data = await response.json();
  return {
    success: true,
    query: q,
    messages: data.messages || [],
    nextPageToken: data.nextPageToken || null,
    resultSizeEstimate: data.resultSizeEstimate || 0
  };
}

// 3. Match Email Logic - calls real backend
async function matchEmailToERP(emailData, tabId) {
  try {
    const backendUrl = await getBackendUrl();
    const organizationId = await getOrganizationId();
    const userEmail = await getUserEmail();

    const { body, attachments } = await enrichEmailForTriage(emailData);
    
    // Step 1: Triage the email (classify + extract)
    const triageResponse = await fetch(`${backendUrl}/extension/triage`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Organization-ID': organizationId
      },
      body: JSON.stringify({
        email_id: emailData.id,
        subject: emailData.subject,
        sender: emailData.sender,
        snippet: emailData.snippet,
        body: body,
        attachments: attachments,
        organization_id: organizationId,
        user_email: userEmail
      })
    });
    
    if (!triageResponse.ok) {
      throw new Error(`Triage failed: ${triageResponse.status}`);
    }
    
    const triageResult = await triageResponse.json();
    const extraction = triageResult.extraction || {};
    
    // Step 2: Match against bank feed
    let bankMatch = null;
    try {
      const bankResponse = await fetch(`${backendUrl}/extension/match-bank`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Organization-ID': organizationId
        },
        body: JSON.stringify({
          extraction,
          organization_id: organizationId
        })
      });
      
      if (bankResponse.ok) {
        bankMatch = await bankResponse.json();
      }
    } catch (e) {
      console.warn('[Clearledgr] Bank match failed:', e);
    }
    
    // Step 3: Match against ERP
    let erpMatch = null;
    try {
      const erpResponse = await fetch(`${backendUrl}/extension/match-erp`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Organization-ID': organizationId
        },
        body: JSON.stringify({
          extraction,
          organization_id: organizationId
        })
      });
      
      if (erpResponse.ok) {
        erpMatch = await erpResponse.json();
      }
    } catch (e) {
      console.warn('[Clearledgr] ERP match failed:', e);
    }
    
    // Step 4: Verify confidence (HITL check)
    let confidenceResult = { confidence_pct: 50, can_post: false };
    try {
      const confResponse = await fetch(`${backendUrl}/extension/verify-confidence`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Organization-ID': organizationId
        },
        body: JSON.stringify({
          email_id: emailData.id,
          extraction,
          bank_match: bankMatch,
          erp_match: erpMatch,
          organization_id: organizationId
        })
      });
      
      if (confResponse.ok) {
        confidenceResult = await confResponse.json();
      }
    } catch (e) {
      console.warn('[Clearledgr] Confidence check failed:', e);
    }
    
    // Build match result for content script
    const matchResult = {
      confidence: (confidenceResult.confidence_pct || 50) / 100,
      canPost: confidenceResult.can_post || false,
      vendor: extraction.vendor || emailData.sender,
      amount: extraction.amount || '$0.00',
      currency: extraction.currency || 'USD',
      invoiceNumber: extraction.invoice_number,
      dueDate: extraction.due_date,
      classification: triageResult.classification?.type,
      glCode: erpMatch?.gl_code || '9999',
      glDescription: erpMatch?.gl_description || 'Uncategorized',
      poNumber: erpMatch?.po_number,
      poMatch: erpMatch?.po_match_score,
      vendorMatch: erpMatch?.vendor_match_score || 0.70,
      bankRef: bankMatch?.reference,
      bankMatch: bankMatch?.match_score,
      amountMatch: confidenceResult.amount_match_score || 0.60,
      dateMatch: confidenceResult.date_match_score || 0.80,
      mismatches: confidenceResult.mismatches || [],
      date: new Date().toISOString(),
      // Store for posting
      _extraction: extraction,
      _bankMatch: bankMatch,
      _erpMatch: erpMatch
    };
    
    // Send result back to content script
    chrome.tabs.sendMessage(tabId, {
      action: 'MATCH_FOUND',
      data: matchResult
    });

  } catch (error) {
    console.error('[Clearledgr] Match failed:', error);
    
    // Send error to content script
    chrome.tabs.sendMessage(tabId, {
      action: 'MATCH_ERROR',
      error: error.message
    });
  }
}

// Handle Posting to Ledger with Multi-System Routing (ERP + Slack + Sheets)
async function handlePostToLedger(matchData) {
  const backendUrl = await getBackendUrl();
  const organizationId = await getOrganizationId();
  const userEmail = await getUserEmail();
  
  const timestamp = new Date().toISOString();
  
  try {
    // DIFFERENTIATOR: Multi-System Routing via Backend
    // Backend handles: HITL check, ERP post, Slack update, audit trail
    const postResult = await fetch(`${backendUrl}/extension/approve-and-post`, {
      method: 'POST',
      headers: { 
        'Content-Type': 'application/json',
        'X-Organization-ID': organizationId
      },
      body: JSON.stringify({
        email_id: matchData?.emailId,
        extraction: matchData?._extraction || {
          vendor: matchData?.vendor,
          amount: matchData?.amount,
          invoice_number: matchData?.invoiceNumber,
          due_date: matchData?.dueDate,
          currency: matchData?.currency
        },
        bank_match: matchData?._bankMatch,
        erp_match: matchData?._erpMatch,
        override: matchData?.override || false,
        organization_id: organizationId,
        user_email: userEmail
      })
    });
    
    const result = await postResult.json();
    
    if (!postResult.ok) {
      // HITL: Blocked due to low confidence
      if (result.status === 'blocked') {
        return {
          success: false,
          blocked: true,
          reason: result.reason,
          confidence: result.confidence,
          mismatches: result.mismatches,
          actionRequired: result.action_required
        };
      }
      throw new Error(result.detail || 'Post failed');
    }
    
    console.log('[Clearledgr] Posted via backend:', result.clearledgr_audit_id);
    
    return {
      success: true,
      ledgerId: result.clearledgr_audit_id,
      erpDocument: result.erp_document,
      confidence: result.confidence,
      timestamp,
      slackUpdated: result.slack_updated,
      backendSynced: true
    };
    
  } catch (e) {
    console.error('[Clearledgr] Backend post failed:', e);
    
    // Return error - no fallback for production
    return {
      success: false,
      error: e.message,
      backendSynced: false
    };
  }
}

// Multi-system routing: Notify Slack via backend or webhook
async function notifySlack(data) {
  try {
    const settings = await getMergedSyncSettings();
    
    // Try backend first (preferred - maintains single source of truth)
    const backendUrl = await getBackendUrl();
    const slackChannel = settings?.slackChannel;
    
    if (backendUrl && slackChannel) {
      try {
        const response = await fetch(`${backendUrl}/slack/notify`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            type: 'message',
            channel: slackChannel,
            text: `Invoice Posted: ${data.vendor} - ${data.amount}\nAudit ID: ${data.auditId}`
          })
        });
        
        if (response.ok) {
          console.log('[Clearledgr] Slack notified via backend:', data.auditId);
          return;
        }
      } catch (e) {
        console.warn('[Clearledgr] Backend notification failed, trying webhook');
      }
    }
    
    // Fallback: Direct Slack webhook
    const webhookUrl = settings?.slackWebhookUrl;
    if (webhookUrl) {
      await fetch(webhookUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          text: `Invoice Posted | ${data.vendor} | ${data.amount}`,
          blocks: [
            {
              type: 'header',
              text: { type: 'plain_text', text: 'Invoice Posted to ERP' }
            },
            {
              type: 'section',
              fields: [
                { type: 'mrkdwn', text: `*Vendor:*\n${data.vendor}` },
                { type: 'mrkdwn', text: `*Amount:*\n${data.amount}` }
              ]
            },
            {
              type: 'context',
              elements: [
                { type: 'mrkdwn', text: `Audit ID: \`${data.auditId}\` | ${data.timestamp}` }
              ]
            }
          ]
        })
      });
      console.log('[Clearledgr] Slack notified via webhook:', data.auditId);
    }
  } catch (e) {
    console.warn('[Clearledgr] Slack notification failed:', e);
  }
}

// Send exception to Slack for review
async function notifySlackException(exception) {
  try {
    const settings = await getMergedSyncSettings();
    const backendUrl = await getBackendUrl();
    const slackChannel = settings?.slackChannel;
    
    if (backendUrl && slackChannel) {
      await fetch(`${backendUrl}/slack/notify`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          type: 'exception',
          channel: slackChannel,
          exception: {
            id: exception.auditId,
            priority: exception.priority || 'MEDIUM',
            vendor: exception.vendor,
            amount: exception.amount,
            type: exception.type || 'Invoice Exception'
          }
        })
      });
      console.log('[Clearledgr] Exception sent to Slack:', exception.auditId);
    }
  } catch (e) {
    console.warn('[Clearledgr] Slack exception notification failed:', e);
  }
}

// ==========================================================================
// GMAIL API - OAUTH & LABELS
// ==========================================================================

// OAuth configuration
const OAUTH_CONFIG = {
  // For launchWebAuthFlow, we need a Web Application client ID
  webClientId: '333271407440-j42m0b6sh4j42bvlkr0vko7l058uf3ja.apps.googleusercontent.com',
  scopes: [
    'https://www.googleapis.com/auth/gmail.labels',
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/gmail.readonly'
  ]
};

// Token storage
let cachedToken = null;
let tokenExpiry = null;

// Get OAuth token using launchWebAuthFlow (works in all Chromium browsers)
async function getAuthToken(interactive = true) {
  // Check cached token
  if (cachedToken && tokenExpiry && Date.now() < tokenExpiry) {
    console.log('[Clearledgr] Using cached token');
    return cachedToken;
  }
  
  // Try to get from storage first
  const stored = await chrome.storage.local.get(['gmail_token', 'gmail_token_expiry']);
  if (stored.gmail_token && stored.gmail_token_expiry && Date.now() < stored.gmail_token_expiry) {
    cachedToken = stored.gmail_token;
    tokenExpiry = stored.gmail_token_expiry;
    console.log('[Clearledgr] Using stored token');
    return cachedToken;
  }
  
  if (!interactive) {
    throw new Error('No valid token and interactive mode disabled');
  }
  
  // Use launchWebAuthFlow (works in Chrome, Comet, Brave, Edge, etc.)
  return launchWebAuthFlow();
}

// launchWebAuthFlow - works in all Chromium browsers (Chrome, Comet, Brave, Edge, etc.)
function launchWebAuthFlow() {
  return new Promise((resolve, reject) => {
    const redirectUrl = chrome.identity.getRedirectURL();
    console.log('[Clearledgr] Using launchWebAuthFlow, redirect:', redirectUrl);
    
    const authUrl = new URL('https://accounts.google.com/o/oauth2/v2/auth');
    authUrl.searchParams.set('client_id', OAUTH_CONFIG.webClientId);
    authUrl.searchParams.set('redirect_uri', redirectUrl);
    authUrl.searchParams.set('response_type', 'token');
    authUrl.searchParams.set('scope', OAUTH_CONFIG.scopes.join(' '));
    authUrl.searchParams.set('prompt', 'consent');
    
    chrome.identity.launchWebAuthFlow(
      { url: authUrl.toString(), interactive: true },
      (responseUrl) => {
        if (chrome.runtime.lastError) {
          console.error('[Clearledgr] WebAuthFlow error:', chrome.runtime.lastError.message);
          reject(new Error(chrome.runtime.lastError.message));
          return;
        }
        
        if (!responseUrl) {
          reject(new Error('No response from OAuth flow'));
          return;
        }
        
        // Extract token from URL fragment
        const url = new URL(responseUrl);
        const params = new URLSearchParams(url.hash.slice(1));
        const token = params.get('access_token');
        const expiresIn = parseInt(params.get('expires_in') || '3600', 10);
        
        if (!token) {
          reject(new Error('No access token in response'));
          return;
        }
        
        // Cache token
        cachedToken = token;
        tokenExpiry = Date.now() + (expiresIn * 1000) - 60000;
        
        chrome.storage.local.set({
          gmail_token: token,
          gmail_token_expiry: tokenExpiry
        });
        
        console.log('[Clearledgr] OAuth token obtained via launchWebAuthFlow');
        console.log('[Clearledgr] Token preview:', token.substring(0, 20) + '...');
        resolve(token);
      }
    );
  });
}

function parseOAuthCodeFromResponse(responseUrl) {
  const url = new URL(responseUrl);
  const fromQuery = url.searchParams.get('code');
  if (fromQuery) return fromQuery;
  const hash = new URLSearchParams((url.hash || '').replace(/^#/, ''));
  return hash.get('code');
}

function parseOAuthStateFromResponse(responseUrl) {
  const url = new URL(responseUrl);
  const fromQuery = url.searchParams.get('state');
  if (fromQuery) return fromQuery;
  const hash = new URLSearchParams((url.hash || '').replace(/^#/, ''));
  return hash.get('state');
}

// Extension OAuth for backend Gmail autopilot.
// Uses chromiumapp redirect URL and exchanges code server-side at /gmail/callback.
async function connectGmailAutopilot(userIdOverride = null) {
  const backendUrl = await getBackendUrl();
  const userId = userIdOverride || await getUserEmail() || 'default';
  const redirectUrl = chrome.identity.getRedirectURL();
  await setAutopilotConnectAttempt({
    status: 'in_progress',
    message: 'Waiting for Google authorization',
    userId
  });
  try {
    const statePayload = {
      user_id: userId,
      redirect_url: '',
      oauth_redirect_uri: redirectUrl
    };
    const state = btoa(JSON.stringify(statePayload));

    const authUrl = new URL('https://accounts.google.com/o/oauth2/v2/auth');
    authUrl.searchParams.set('client_id', OAUTH_CONFIG.webClientId);
    authUrl.searchParams.set('redirect_uri', redirectUrl);
    authUrl.searchParams.set('response_type', 'code');
    authUrl.searchParams.set('scope', [
      'https://www.googleapis.com/auth/gmail.readonly',
      'https://www.googleapis.com/auth/gmail.modify'
    ].join(' '));
    authUrl.searchParams.set('access_type', 'offline');
    authUrl.searchParams.set('prompt', 'consent');
    authUrl.searchParams.set('state', state);

    const responseUrl = await new Promise((resolve, reject) => {
      chrome.identity.launchWebAuthFlow(
        { url: authUrl.toString(), interactive: true },
        (resultUrl) => {
          if (chrome.runtime.lastError) {
            reject(new Error(chrome.runtime.lastError.message || 'Authorization was not completed'));
            return;
          }
          if (!resultUrl) {
            reject(new Error('No OAuth response URL received'));
            return;
          }
          resolve(resultUrl);
        }
      );
    });

    const code = parseOAuthCodeFromResponse(responseUrl);
    const returnedState = parseOAuthStateFromResponse(responseUrl) || state;
    if (!code) {
      await setAutopilotConnectAttempt({
        status: 'failed',
        message: 'Connection did not complete. Please try again.',
        userId
      });
      throw new Error('OAuth code missing in redirect response');
    }
    await setAutopilotConnectAttempt({
      status: 'in_progress',
      message: 'Finalizing secure connection',
      userId
    });

    const callbackUrl = new URL(`${backendUrl}/gmail/callback`);
    callbackUrl.searchParams.set('code', code);
    callbackUrl.searchParams.set('state', returnedState);

    const callbackResponse = await fetch(callbackUrl.toString(), { method: 'GET' });
    const payload = await callbackResponse.json().catch(() => ({}));
    if (!callbackResponse.ok) {
      await setAutopilotConnectAttempt({
        status: 'failed',
        message: 'Clearledgr could not complete Gmail connection. Please retry.',
        userId
      });
      throw new Error(payload?.detail || payload?.error || `Backend callback failed (${callbackResponse.status})`);
    }
    await setAutopilotConnectAttempt({
      status: 'success',
      message: 'Gmail Autopilot connected',
      userId
    });
    return { success: true, data: payload };
  } catch (error) {
    await setAutopilotConnectAttempt({
      status: 'failed',
      message: 'Connection could not be completed. Please try again.',
      userId
    });
    throw error;
  }
}

// Clear token (for logout or token refresh)
async function clearAuthToken() {
  cachedToken = null;
  tokenExpiry = null;
  await chrome.storage.local.remove(['gmail_token', 'gmail_token_expiry']);
}

// Gmail API base
const GMAIL_API = 'https://gmail.googleapis.com/gmail/v1/users/me';

// Label color mapping (Gmail API color names)
const LABEL_COLORS = {
  'Clearledgr': { backgroundColor: '#16a765', textColor: '#ffffff' },
  'Clearledgr/Exceptions': { backgroundColor: '#f691b2', textColor: '#000000' },
  'Clearledgr/Invoices': { backgroundColor: '#fb4c2f', textColor: '#ffffff' },
  'Clearledgr/Needs Review': { backgroundColor: '#ffad46', textColor: '#000000' },
  'Clearledgr/Payment Requests': { backgroundColor: '#16a765', textColor: '#ffffff' },
  'Clearledgr/Processed': { backgroundColor: '#89d3b2', textColor: '#000000' }
};

// Cache label IDs
let labelIdCache = {};

// Initialize Gmail labels
async function initializeGmailLabels() {
  try {
    const token = await getAuthToken();
    console.log('[Clearledgr] Got token, fetching labels...');
    
    // Get existing labels
    const response = await fetch(`${GMAIL_API}/labels`, {
      headers: { 'Authorization': `Bearer ${token}` }
    });
    
    if (!response.ok) {
      const errorBody = await response.text();
      console.error('[Clearledgr] Gmail API error:', response.status, errorBody);
      throw new Error(`Failed to fetch labels: ${response.status}`);
    }
    
    const { labels: existingLabels } = await response.json();
    
    // Build cache of existing label IDs
    existingLabels.forEach(label => {
      labelIdCache[label.name] = label.id;
    });
    
    // Create missing Clearledgr labels
    const createdLabels = [];
    for (const labelConfig of CLEARLEDGR_LABELS) {
      if (!labelIdCache[labelConfig.name]) {
        const newLabel = await createGmailLabel(token, labelConfig.name);
        if (newLabel) {
          labelIdCache[newLabel.name] = newLabel.id;
          createdLabels.push(newLabel.name);
        }
      }
    }
    
    console.log('[Clearledgr] Labels ready. Created:', createdLabels.length);
    return { success: true, labels: CLEARLEDGR_LABELS, created: createdLabels };
  } catch (error) {
    console.error('[Clearledgr] Label initialization failed:', error);
    return { success: false, error: error.message };
  }
}

// Create a Gmail label
async function createGmailLabel(token, labelName) {
  try {
    const colors = LABEL_COLORS[labelName] || {};
    
    const response = await fetch(`${GMAIL_API}/labels`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        name: labelName,
        labelListVisibility: 'labelShow',
        messageListVisibility: 'show',
        color: colors
      })
    });
    
    if (!response.ok) {
      const err = await response.json();
      throw new Error(err.error?.message || 'Failed to create label');
    }
    
    return await response.json();
  } catch (error) {
    console.error('[Clearledgr] Create label failed:', labelName, error);
    return null;
  }
}

// Get label ID by name (with caching)
async function getLabelId(labelName) {
  if (labelIdCache[labelName]) {
    return labelIdCache[labelName];
  }
  
  // Refresh cache
  await initializeGmailLabels();
  return labelIdCache[labelName];
}

// Apply label to email
async function applyLabelToEmail(emailId, labelName) {
  try {
    const token = await getAuthToken();
    const labelId = await getLabelId(labelName);
    
    if (!labelId) {
      throw new Error(`Label not found: ${labelName}`);
    }
    
    const response = await fetch(`${GMAIL_API}/messages/${emailId}/modify`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        addLabelIds: [labelId],
        removeLabelIds: []
      })
    });
    
    if (!response.ok) {
      // Fallback to thread modify if message id was actually a thread id
      const threadResponse = await fetch(`${GMAIL_API}/threads/${emailId}/modify`, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          addLabelIds: [labelId],
          removeLabelIds: []
        })
      });
      
      if (!threadResponse.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.error?.message || 'Failed to apply label');
      }
    }
    
    console.log('[Clearledgr] Label applied:', labelName, 'to', emailId);
    return { success: true, label: labelName };
  } catch (error) {
    console.error('[Clearledgr] Apply label failed:', error);
    return { success: false, error: error.message };
  }
}

// Remove label from email
async function removeLabelFromEmail(emailId, labelName) {
  try {
    const token = await getAuthToken();
    const labelId = await getLabelId(labelName);
    
    if (!labelId) {
      throw new Error(`Label not found: ${labelName}`);
    }
    
    const response = await fetch(`${GMAIL_API}/messages/${emailId}/modify`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        addLabelIds: [],
        removeLabelIds: [labelId]
      })
    });
    
    if (!response.ok) {
      const threadResponse = await fetch(`${GMAIL_API}/threads/${emailId}/modify`, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          addLabelIds: [],
          removeLabelIds: [labelId]
        })
      });
      
      if (!threadResponse.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.error?.message || 'Failed to remove label');
      }
    }
    
    console.log('[Clearledgr] Label removed:', labelName, 'from', emailId);
    return { success: true };
  } catch (error) {
    console.error('[Clearledgr] Remove label failed:', error);
    return { success: false, error: error.message };
  }
}

// Get labels for an email
async function getEmailLabels(emailId) {
  try {
    const token = await getAuthToken(false);
    
    const response = await fetch(`${GMAIL_API}/messages/${emailId}?format=metadata&metadataHeaders=labelIds`, {
      headers: { 'Authorization': `Bearer ${token}` }
    });
    
    if (!response.ok) return [];
    
    const { labelIds = [] } = await response.json();
    
    // Reverse lookup label names
    const names = [];
    for (const [name, id] of Object.entries(labelIdCache)) {
      if (labelIds.includes(id)) {
        names.push(name);
      }
    }
    
    return names;
  } catch (error) {
    console.warn('[Clearledgr] Get labels failed:', error);
    return [];
  }
}

// Get label statistics
async function getLabelStats() {
  try {
    const token = await getAuthToken(false);
    const stats = {};
    
    const labelQueries = {
      invoices: 'Clearledgr/Invoices',
      paymentRequests: 'Clearledgr/Payment Requests',
      needsReview: 'Clearledgr/Needs Review',
      exceptions: 'Clearledgr/Exceptions',
      processed: 'Clearledgr/Processed'
    };
    
    for (const [key, labelName] of Object.entries(labelQueries)) {
      const labelId = labelIdCache[labelName];
      if (labelId) {
        const response = await fetch(
          `${GMAIL_API}/messages?labelIds=${labelId}&maxResults=1`,
          { headers: { 'Authorization': `Bearer ${token}` } }
        );
        if (response.ok) {
          const data = await response.json();
          stats[key] = data.resultSizeEstimate || 0;
        }
      }
    }
    
    return stats;
  } catch (error) {
    console.warn('[Clearledgr] Get label stats failed:', error);
    return {};
  }
}

// ==========================================================================
// PDF/IMAGE EXTRACTION VIA CLOUD VISION API
// ==========================================================================

const VISION_API_ENDPOINT = 'https://vision.googleapis.com/v1/images:annotate';
const DOCUMENT_AI_ENDPOINT = 'https://documentai.googleapis.com/v1';

// Extract data from PDF/Image attachment
async function extractFromAttachment(attachmentData, mimeType) {
  try {
    const token = await getAuthToken();
    
    // Use Cloud Vision for images, Document AI for PDFs
    if (mimeType.includes('pdf')) {
      return await extractFromPDF(attachmentData, token);
    } else if (mimeType.includes('image')) {
      return await extractFromImage(attachmentData, token);
    }
    
    return null;
  } catch (error) {
    console.error('[Clearledgr] Extraction failed:', error);
    return null;
  }
}

// Extract text and data from image using Cloud Vision
async function extractFromImage(imageBase64, token) {
  const requestBody = {
    requests: [{
      image: { content: imageBase64 },
      features: [
        { type: 'DOCUMENT_TEXT_DETECTION' },
        { type: 'TEXT_DETECTION' }
      ]
    }]
  };
  
  const response = await fetch(`${VISION_API_ENDPOINT}?key=${await getApiKey()}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(requestBody)
  });
  
  if (!response.ok) {
    throw new Error(`Vision API error: ${response.status}`);
  }
  
  const result = await response.json();
  const text = result.responses?.[0]?.fullTextAnnotation?.text || 
               result.responses?.[0]?.textAnnotations?.[0]?.description || '';
  
  return parseExtractedText(text);
}

// Extract data from PDF using Document AI or fallback OCR
async function extractFromPDF(pdfBase64, token) {
  // First try: Use Gmail's native PDF parsing if available
  // Fallback: Convert PDF to image and use Vision OCR
  
  // For now, we'll use a text extraction approach
  // In production, this would integrate with Document AI
  
  try {
    // Try Document AI if configured
    const processorId = await getDocumentAIProcessor();
    if (processorId) {
      return await processWithDocumentAI(pdfBase64, processorId, token);
    }
  } catch (e) {
    console.warn('[Clearledgr] Document AI not configured, using fallback');
  }
  
  // Fallback: Basic text extraction from PDF
  // This extracts embedded text but not scanned documents
  const text = await extractTextFromPDF(pdfBase64);
  return parseExtractedText(text);
}

// Process with Google Document AI
async function processWithDocumentAI(documentBase64, processorId, token) {
  const { settings } = await chrome.storage.sync.get('settings');
  const projectId = settings?.gcpProjectId;
  const location = settings?.gcpLocation || 'us';
  
  if (!projectId) {
    throw new Error('GCP Project ID not configured');
  }
  
  const endpoint = `${DOCUMENT_AI_ENDPOINT}/projects/${projectId}/locations/${location}/processors/${processorId}:process`;
  
  const response = await fetch(endpoint, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      rawDocument: {
        content: documentBase64,
        mimeType: 'application/pdf'
      }
    })
  });
  
  if (!response.ok) {
    throw new Error(`Document AI error: ${response.status}`);
  }
  
  const result = await response.json();
  return parseDocumentAIResult(result);
}

// Parse Document AI result into structured data
function parseDocumentAIResult(result) {
  const extracted = {
    rawText: result.document?.text || '',
    confidence: 0.85
  };
  
  // Extract entities if available
  const entities = result.document?.entities || [];
  for (const entity of entities) {
    const type = entity.type?.toLowerCase();
    const value = entity.mentionText || entity.normalizedValue?.text;
    
    if (type?.includes('amount') || type?.includes('total')) {
      extracted.amount = value;
    } else if (type?.includes('vendor') || type?.includes('supplier')) {
      extracted.vendor = value;
    } else if (type?.includes('invoice') && type?.includes('number')) {
      extracted.invoiceNumber = value;
    } else if (type?.includes('date') && type?.includes('due')) {
      extracted.dueDate = value;
    } else if (type?.includes('date') && type?.includes('invoice')) {
      extracted.invoiceDate = value;
    } else if (type?.includes('tax')) {
      extracted.taxId = value;
    } else if (type?.includes('currency')) {
      extracted.currency = value;
    }
  }
  
  return extracted;
}

// Basic PDF text extraction (embedded text only)
async function extractTextFromPDF(pdfBase64) {
  // Decode base64 and extract text using PDF.js pattern
  // This is a simplified version - production would use pdf.js library
  try {
    const binary = atob(pdfBase64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) {
      bytes[i] = binary.charCodeAt(i);
    }
    
    // Look for text streams in PDF
    const text = extractTextStreamsFromPDF(bytes);
    return text;
  } catch (e) {
    console.warn('[Clearledgr] PDF text extraction failed:', e);
    return '';
  }
}

// Extract text streams from PDF binary
function extractTextStreamsFromPDF(bytes) {
  // Simple text extraction - looks for text between BT and ET markers
  const decoder = new TextDecoder('latin1');
  const content = decoder.decode(bytes);
  
  const textParts = [];
  const regex = /\(([^)]+)\)/g;
  let match;
  
  while ((match = regex.exec(content)) !== null) {
    const text = match[1]
      .replace(/\\n/g, '\n')
      .replace(/\\r/g, '\r')
      .replace(/\\\(/g, '(')
      .replace(/\\\)/g, ')');
    if (text.length > 2 && /[a-zA-Z0-9]/.test(text)) {
      textParts.push(text);
    }
  }
  
  return textParts.join(' ');
}

// Parse extracted text into structured financial data
function parseExtractedText(text) {
  if (!text) return null;
  
  const extracted = {
    rawText: text,
    confidence: 0.70
  };
  
  // Invoice/PO Number patterns
  const invoicePatterns = [
    /invoice\s*(?:#|no\.?|number)?[:\s]*([A-Z0-9-]+)/i,
    /inv[:\s#]*([A-Z0-9-]+)/i,
    /(?:po|purchase\s*order)\s*(?:#|no\.?)?[:\s]*([A-Z0-9-]+)/i
  ];
  for (const pattern of invoicePatterns) {
    const match = text.match(pattern);
    if (match) {
      extracted.invoiceNumber = match[1];
      extracted.confidence += 0.05;
      break;
    }
  }
  
  // Amount patterns - multiple currencies
  const amountPatterns = [
    /(?:total|amount\s*due|balance|grand\s*total)[:\s]*[\$€£]?([\d,]+\.?\d*)/i,
    /[\$€£]([\d,]+\.?\d{2})/,
    /([\d,]+\.?\d{2})\s*(?:USD|EUR|GBP|CHF)/i
  ];
  for (const pattern of amountPatterns) {
    const match = text.match(pattern);
    if (match) {
      const amount = parseFloat(match[1].replace(/,/g, ''));
      if (amount > 0 && amount < 10000000) { // Sanity check
        extracted.amount = match[0].includes('€') ? `€${match[1]}` :
                          match[0].includes('£') ? `£${match[1]}` :
                          `$${match[1]}`;
        extracted.confidence += 0.10;
        break;
      }
    }
  }
  
  // Date patterns
  const datePatterns = [
    /(?:due\s*date|payment\s*due|pay\s*by)[:\s]*(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4})/i,
    /(?:due|payable)[:\s]*(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4})/i
  ];
  for (const pattern of datePatterns) {
    const match = text.match(pattern);
    if (match) {
      extracted.dueDate = match[1];
      extracted.confidence += 0.05;
      break;
    }
  }
  
  // Vendor/Company name (usually at top of invoice)
  const lines = text.split('\n').filter(l => l.trim());
  if (lines.length > 0) {
    // First non-empty line often contains company name
    const firstLine = lines[0].trim();
    if (firstLine.length > 3 && firstLine.length < 100) {
      extracted.vendor = firstLine;
    }
  }
  
  // Tax ID patterns
  const taxPatterns = [
    /(?:tax\s*id|vat|ein|tin)[:\s]*([A-Z0-9-]+)/i,
    /(?:ust\.?-?id\.?|mwst)[:\s]*([A-Z]{2}[0-9]+)/i
  ];
  for (const pattern of taxPatterns) {
    const match = text.match(pattern);
    if (match) {
      extracted.taxId = match[1];
      break;
    }
  }
  
  return extracted;
}

// Get API key for Cloud Vision (stored in settings)
async function getApiKey() {
  const { settings } = await chrome.storage.sync.get('settings');
  return settings?.cloudVisionApiKey || settings?.gcpApiKey;
}

// Get Document AI processor ID
async function getDocumentAIProcessor() {
  const { settings } = await chrome.storage.sync.get('settings');
  return settings?.documentAIProcessorId;
}

// Message handler for extraction
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === 'extractAttachment') {
    extractFromAttachment(request.data, request.mimeType)
      .then(result => sendResponse({ success: true, data: result }))
      .catch(error => sendResponse({ success: false, error: error.message }));
    return true;
  }
});

// ==========================================================================
// UTILITIES
// ==========================================================================

// 4. Utility - Audit ID per spec: CL-{date}-{hash}
function generateAuditId() {
  const now = new Date();
  const date = now.toISOString().slice(0, 10).replace(/-/g, ''); // YYYYMMDD
  const hash = Array.from(crypto.getRandomValues(new Uint8Array(4)))
    .map(b => b.toString(16).padStart(2, '0'))
    .join('');
  return `CL-${date}-${hash}`;
}

// Click handler for action icon - triggers auth and initializes labels
chrome.action.onClicked.addListener(async (tab) => {
  console.log('[Clearledgr] Extension icon clicked');
  
  // Check if we are on a valid URL (Gmail)
  if (tab.url && (tab.url.includes('mail.google.com') || tab.url.includes('gmail.google.com'))) {
    try {
      // First, ensure we have a valid OAuth token (will prompt if needed)
      console.log('[Clearledgr] Checking/requesting OAuth token...');
      const token = await getAuthToken(true);
      console.log('[Clearledgr] Token obtained:', token ? 'yes' : 'no');
      
      // Initialize labels
      const labelResult = await initializeGmailLabels();
      console.log('[Clearledgr] Labels initialized:', labelResult);
      
      // Tell the InboxSDK layer to open Clearledgr (Streak-style routes/sidebar)
      chrome.tabs.sendMessage(tab.id, { action: 'OPEN_CLEARLEDGR' }).catch(err => {
        console.log('Content script not ready, injecting scripts...', err);
        
        // Inject scripts manually if they aren't running (best-effort).
        injectClearledgrContentScripts(tab.id)
          .then(() => {
            // Try opening again after injection
            setTimeout(() => {
              chrome.tabs.sendMessage(tab.id, { action: 'OPEN_CLEARLEDGR' });
            }, 500);
          })
          .catch(e => console.error('Injection failed:', e));
      });
    } catch (err) {
      console.error('[Clearledgr] Auth/init failed:', err);
    }
  } else {
    console.log('[Clearledgr] Not on Gmail, ignoring click');
  }
});

async function injectClearledgrContentScripts(tabId) {
  const baseFiles = [
    'queue-manager.js',
    'content-script.js'
  ];

  // Dev loads InboxSDK bundle under dist/. Packaged builds have it at root.
  const inboxsdkCandidates = ['dist/inboxsdk-layer.js', 'inboxsdk-layer.js'];

  let lastError = null;
  for (const inboxsdkPath of inboxsdkCandidates) {
    try {
      await chrome.scripting.executeScript({
        target: { tabId },
        files: [...baseFiles, inboxsdkPath]
      });
      return;
    } catch (e) {
      lastError = e;
    }
  }

  throw lastError || new Error('Failed to inject Clearledgr content scripts');
}

// 5. Post to ERP Logic - calls real backend
async function postToERP(transactionData, tabId) {
  try {
    const result = await handlePostToLedger(transactionData);
    
    if (result.success) {
      sendToTab(tabId, {
        action: 'post_success',
        ledgerId: result.ledgerId,
        erpDocument: result.erpDocument,
        confidence: result.confidence
      });
    } else if (result.blocked) {
      // HITL: Blocked - requires review
      sendToTab(tabId, {
        action: 'post_blocked',
        reason: result.reason,
        confidence: result.confidence,
        mismatches: result.mismatches,
        actionRequired: result.actionRequired
      });
    } else {
      throw new Error(result.error || 'Post failed');
    }

  } catch (error) {
    console.error('[Clearledgr] Post failed:', error);
    sendToTab(tabId, {
      action: 'post_error',
      message: error.message || 'Failed to post to ERP'
    });
  }
}

// Escalate to manager via Slack
async function escalateToManager(escalationData, tabId) {
  try {
    const backendUrl = await getBackendUrl();
    const organizationId = await getOrganizationId();
    const userEmail = await getUserEmail();
    
    const response = await fetch(`${backendUrl}/extension/escalate`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Organization-ID': organizationId
      },
      body: JSON.stringify({
        email_id: escalationData.emailId,
        vendor: escalationData.vendor,
        amount: escalationData.amount,
        currency: escalationData.currency || 'USD',
        confidence: escalationData.confidence,
        mismatches: escalationData.mismatches || [],
        message: escalationData.message,
        channel: escalationData.channel || '#finance-escalations',
        organization_id: organizationId,
        user_email: userEmail
      })
    });
    
    if (!response.ok) {
      throw new Error('Escalation failed');
    }
    
    const result = await response.json();
    
    sendToTab(tabId, {
      action: 'escalation_sent',
      channel: result.channel,
      status: result.status
    });
    
  } catch (error) {
    console.error('[Clearledgr] Escalation failed:', error);
    sendToTab(tabId, {
      action: 'escalation_error',
      message: error.message
    });
  }
}

function sendToTab(tabId, message) {
  chrome.tabs.sendMessage(tabId, message).catch(err => {
    console.warn('[Clearledgr] Failed to send message to tab:', err);
  });
}

// Bulk scan emails via backend
async function bulkScanEmails(emailIds, orgId) {
  try {
    const backendUrl = await getBackendUrl();
    const organizationId = orgId || await getOrganizationId();
    const userEmail = await getUserEmail();
    
    const response = await fetch(`${backendUrl}/extension/scan`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Organization-ID': organizationId
      },
      body: JSON.stringify({
        email_ids: emailIds,
        organization_id: organizationId,
        user_email: userEmail
      })
    });
    
    if (!response.ok) {
      throw new Error(`Scan failed: ${response.status}`);
    }
    
    return await response.json();
  } catch (error) {
    console.error('[Clearledgr] Bulk scan failed:', error);
    return { success: false, error: error.message };
  }
}

// Fetch full email message with attachments from Gmail API
async function fetchEmailWithAttachments(emailId) {
  const token = await getGmailToken();
  if (!token) {
    console.log('[Clearledgr] No Gmail token for attachment fetch');
    return null;
  }
  
  try {
    // Fetch full message
    let messageIdForAttachments = emailId;
    let response = await fetch(
      `${GMAIL_API}/messages/${emailId}?format=full`,
      { headers: { Authorization: `Bearer ${token}` } }
    );
    
    let message = null;
    if (response.ok) {
      message = await response.json();
      messageIdForAttachments = message?.id || emailId;
    } else {
      // Fallback: treat emailId as thread id and fetch first message
      const threadResponse = await fetch(
        `${GMAIL_API}/threads/${emailId}?format=full`,
        { headers: { Authorization: `Bearer ${token}` } }
      );
      if (!threadResponse.ok) {
        console.log('[Clearledgr] Failed to fetch message/thread:', response.status);
        return null;
      }
      const thread = await threadResponse.json();
      message = thread.messages?.[0] || null;
      messageIdForAttachments = message?.id || emailId;
    }
    
    if (!message) return null;

    const attachments = [];

    const flattenParts = (part, acc) => {
      if (!part) return;
      acc.push(part);
      if (Array.isArray(part.parts)) {
        part.parts.forEach((p) => flattenParts(p, acc));
      }
    };

    const allParts = [];
    flattenParts(message.payload, allParts);

    // Helper: decode Gmail's base64url to text.
    const decodeBase64Url = (data) => {
      if (!data) return '';
      try {
        return atob(String(data).replace(/-/g, '+').replace(/_/g, '/'));
      } catch (_) {
        return '';
      }
    };

    // Extract headers we need for reliable classification.
    const headers = Array.isArray(message.payload?.headers) ? message.payload.headers : [];
    const headerMap = {};
    headers.forEach((h) => {
      const key = String(h?.name || '').toLowerCase();
      if (key) headerMap[key] = h?.value || '';
    });

    const subject = headerMap.subject || '';
    const sender = headerMap.from || '';
    const date = headerMap.date || '';
    const snippet = message.snippet || '';

    // Find attachment parts (recursive) so we don't miss nested multipart structures.
    for (const part of allParts) {
      if (!part || !part.filename || !part.body?.attachmentId) continue;
      if (!isSupportedAttachment({ mimeType: part.mimeType, filename: part.filename })) continue;
      if (attachments.length >= MAX_ATTACHMENT_COUNT) break;

      const size = Number(part.body.size || 0);
      if (size > MAX_ATTACHMENT_BYTES) {
        console.log('[Clearledgr] Skipping large attachment:', part.filename, size);
        continue;
      }

      const attResponse = await fetch(
        `${GMAIL_API}/messages/${messageIdForAttachments}/attachments/${part.body.attachmentId}`,
        { headers: { Authorization: `Bearer ${token}` } }
      );

      if (!attResponse.ok) continue;
      const attData = await attResponse.json().catch(() => ({}));
      if (!attData?.data) continue;

      // Gmail returns URL-safe base64, keep it base64url-normalized.
      const base64 = String(attData.data).replace(/-/g, '+').replace(/_/g, '/');

      attachments.push({
        filename: part.filename,
        content_type: part.mimeType,
        content_base64: base64,
        size: size
      });

      console.log(`[Clearledgr] Fetched attachment: ${part.filename} (${part.mimeType})`);
    }

    // Extract body text from any nested text/plain or text/html part.
    let body = '';
    const textPart =
      allParts.find((p) => p?.mimeType === 'text/plain' && p?.body?.data) ||
      allParts.find((p) => p?.mimeType === 'text/html' && p?.body?.data) ||
      (message.payload?.body?.data ? message.payload : null);

    if (textPart?.body?.data) {
      body = decodeBase64Url(textPart.body.data);
    }

    return { body, attachments, subject, sender, date, snippet };
  } catch (error) {
    console.error('[Clearledgr] Error fetching email attachments:', error);
    return null;
  }
}

// Backwards compatible alias used by older functions. Prefer getAuthToken.
async function getGmailToken(interactive = true) {
  try {
    return await getAuthToken(interactive);
  } catch (e) {
    console.warn('[Clearledgr] Gmail token unavailable:', e.message);
    return null;
  }
}

const MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024;
const MAX_ATTACHMENT_COUNT = 3;

function isSupportedAttachment(att) {
  const mime = (att?.mimeType || att?.content_type || '').toLowerCase();
  if (mime.includes('pdf') || mime.includes('png') || mime.includes('jpeg') || mime.includes('jpg') || mime.includes('wordprocessingml')) {
    return true;
  }
  const name = (att?.filename || '').toLowerCase();
  return /\.(pdf|png|jpe?g|docx)$/.test(name);
}

function arrayBufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  const chunkSize = 0x8000;
  let binary = '';
  for (let i = 0; i < bytes.length; i += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunkSize));
  }
  return btoa(binary);
}

async function fetchAttachmentsFromUrls(rawAttachments) {
  if (!Array.isArray(rawAttachments) || rawAttachments.length === 0) return [];

  const results = [];
  for (const att of rawAttachments) {
    if (results.length >= MAX_ATTACHMENT_COUNT) break;
    if (!isSupportedAttachment(att)) continue;

    const url = att.downloadUrl || att.url;
    if (!url) continue;

    try {
      const response = await fetch(url, { credentials: 'include' });
      if (!response.ok) continue;
      const buffer = await response.arrayBuffer();
      if (buffer.byteLength > MAX_ATTACHMENT_BYTES) continue;

      const base64 = arrayBufferToBase64(buffer);
      results.push({
        filename: att.filename || 'attachment',
        content_type: response.headers.get('content-type') || att.mimeType || 'application/octet-stream',
        content_base64: base64,
        size: buffer.byteLength
      });
    } catch (e) {
      console.warn('[Clearledgr] Failed to fetch attachment URL:', e.message);
    }
  }

  return results;
}

async function enrichEmailForTriage(emailData) {
  let body = emailData.body || '';
  let attachments = [];
  let subject = emailData.subject || '';
  let sender = emailData.sender || '';
  let date = emailData.date || '';
  let snippet = emailData.snippet || '';

  if (emailData.id) {
    const fullEmail = await fetchEmailWithAttachments(emailData.id);
    if (fullEmail) {
      body = fullEmail.body || body;
      attachments = fullEmail.attachments || [];
      subject = subject || fullEmail.subject || '';
      sender = sender || fullEmail.sender || '';
      date = date || fullEmail.date || '';
      snippet = snippet || fullEmail.snippet || '';
    }
  }

  if (!attachments.length && Array.isArray(emailData.attachments) && emailData.attachments.length > 0) {
    attachments = await fetchAttachmentsFromUrls(emailData.attachments);
  }

  if (!body && snippet) {
    body = snippet;
  }

  return { body, attachments, subject, sender, date, snippet };
}

// Triage single email via backend (with attachment content for Claude Vision)
  async function triageEmail(emailData) {
  try {
    const backendUrl = await getBackendUrl();
    const organizationId = await getOrganizationId();
    const userEmail = await getUserEmail();
    
    // ALWAYS fetch full email body + attachments + headers for accurate classification
    const enriched = await enrichEmailForTriage(emailData);
    const body = enriched.body;
    const attachments = enriched.attachments;
    const subject = emailData.subject || enriched.subject || '';
    const sender = emailData.sender || enriched.sender || '';
    const snippet = emailData.snippet || enriched.snippet || '';
    const date = emailData.date || enriched.date || '';
    
    const response = await fetch(`${backendUrl}/extension/triage`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Organization-ID': organizationId
      },
      body: JSON.stringify({
        email_id: emailData.id,
        subject: subject,
        sender: sender,
        snippet: snippet,
        body: body,
        attachments: attachments,
        organization_id: organizationId,
        user_email: userEmail
      })
    });
    
    if (!response.ok) {
      throw new Error(`Triage failed: ${response.status}`);
    }
    
    const result = await response.json();
    // Include enriched headers so the extension can render pipeline rows even when the
    // initial scan only had a threadId (Gmail API search).
    result._gmail = { subject, sender, snippet, date };
    return result;
  } catch (error) {
    console.error('[Clearledgr] Triage failed:', error);
    return { success: false, error: error.message };
  }
}
