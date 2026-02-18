importScripts("config.js");
// Clearledgr AP v1 Background Service Worker
// config.js is injected by build.sh

const RetryConfig = {
  maxRetries: 2,
  baseDelay: 800,
  maxDelay: 10000,
  backoffMultiplier: 2,
  retryableStatusCodes: [408, 429, 500, 502, 503, 504]
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
      const timeoutId = setTimeout(() => controller.abort(), 20000);
      const response = await fetch(url, { ...options, signal: controller.signal });
      clearTimeout(timeoutId);
      if (!response.ok && RetryConfig.retryableStatusCodes.includes(response.status) && attempt < maxRetries) {
        const delay = calculateBackoff(attempt);
        await new Promise((resolve) => setTimeout(resolve, delay));
        continue;
      }
      return response;
    } catch (error) {
      lastError = error;
      if (attempt < maxRetries) {
        const delay = calculateBackoff(attempt);
        await new Promise((resolve) => setTimeout(resolve, delay));
        continue;
      }
    }
  }
  throw lastError || new Error('Request failed');
}

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
        files: ['dist/pageWorld.js']
      });
      sendResponse(true);
    } else {
      sendResponse(false);
    }
    return true;
  }
});

// Settings helpers
function normalizeBackendUrl(raw) {
  let url = String(raw || '').trim();
  if (!url) return 'http://127.0.0.1:8000';
  if (!/^https?:\/\//i.test(url)) url = `http://${url}`;
  if (url.endsWith('/v1')) url = url.slice(0, -3);
  try {
    const parsed = new URL(url);
    if (parsed.hostname === '0.0.0.0' || parsed.hostname === 'localhost') {
      parsed.hostname = '127.0.0.1';
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
    backendUrl: data.backendUrl || nested.backendUrl || nested.apiEndpoint || null,
    organizationId: data.organizationId || nested.organizationId || null,
    userEmail: data.userEmail || nested.userEmail || null,
    slackChannel: data.slackChannel || nested.slackChannel || null
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

// OAuth configuration
const OAUTH_CONFIG = {
  webClientId: '333271407440-j42m0b6sh4j42bvlkr0vko7l058uf3ja.apps.googleusercontent.com',
  scopes: [
    'https://www.googleapis.com/auth/gmail.labels',
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/gmail.readonly'
  ]
};

let cachedToken = null;
let tokenExpiry = null;

async function getAuthToken(interactive = true) {
  if (cachedToken && tokenExpiry && Date.now() < tokenExpiry) return cachedToken;
  const stored = await chrome.storage.local.get(['gmail_token', 'gmail_token_expiry']);
  if (stored.gmail_token && stored.gmail_token_expiry && Date.now() < stored.gmail_token_expiry) {
    cachedToken = stored.gmail_token;
    tokenExpiry = stored.gmail_token_expiry;
    return cachedToken;
  }
  if (!interactive) throw new Error('No valid token');
  return launchWebAuthFlow();
}

async function clearCachedAuthToken() {
  const tokenToRemove = cachedToken;
  cachedToken = null;
  tokenExpiry = null;

  try {
    await chrome.storage.local.remove(['gmail_token', 'gmail_token_expiry']);
  } catch (_) {
    // ignore
  }

  if (tokenToRemove && chrome.identity?.removeCachedAuthToken) {
    await new Promise((resolve) => {
      chrome.identity.removeCachedAuthToken({ token: tokenToRemove }, () => resolve());
    });
  }
}

function launchWebAuthFlow() {
  return new Promise((resolve, reject) => {
    const redirectUrl = chrome.identity.getRedirectURL();
    const authUrl = new URL('https://accounts.google.com/o/oauth2/v2/auth');
    authUrl.searchParams.set('client_id', OAUTH_CONFIG.webClientId);
    authUrl.searchParams.set('redirect_uri', redirectUrl);
    authUrl.searchParams.set('response_type', 'token');
    authUrl.searchParams.set('scope', OAUTH_CONFIG.scopes.join(' '));
    authUrl.searchParams.set('prompt', 'consent');

    chrome.identity.launchWebAuthFlow({ url: authUrl.toString(), interactive: true }, (responseUrl) => {
      if (chrome.runtime.lastError) {
        reject(new Error(chrome.runtime.lastError.message));
        return;
      }
      if (!responseUrl) {
        reject(new Error('No OAuth response URL'));
        return;
      }
      const url = new URL(responseUrl);
      const params = new URLSearchParams(url.hash.slice(1));
      const token = params.get('access_token');
      const expiresIn = parseInt(params.get('expires_in') || '3600', 10);
      if (!token) {
        reject(new Error('No access token'));
        return;
      }
      cachedToken = token;
      tokenExpiry = Date.now() + (expiresIn * 1000) - 60000;
      chrome.storage.local.set({ gmail_token: token, gmail_token_expiry: tokenExpiry });
      resolve(token);
    });
  });
}

function getTokenTtlSeconds() {
  if (!tokenExpiry) return 3600;
  const ttl = Math.floor((tokenExpiry - Date.now()) / 1000);
  return Math.max(60, ttl);
}

function getProfileUserInfo() {
  return new Promise((resolve) => {
    if (!chrome.identity?.getProfileUserInfo) {
      resolve({ email: '', id: '' });
      return;
    }
    chrome.identity.getProfileUserInfo((info) => {
      resolve(info || { email: '', id: '' });
    });
  });
}

async function registerGmailTokenWithBackend(accessToken) {
  const backendUrl = await getBackendUrl();
  const profile = await getProfileUserInfo();
  const response = await fetchWithRetry(`${backendUrl}/extension/gmail/register-token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      access_token: accessToken,
      expires_in: getTokenTtlSeconds(),
      email: profile?.email || null
    })
  }, 1);

  if (!response.ok) {
    let detail = `backend_register_failed_${response.status}`;
    try {
      const payload = await response.json();
      if (payload?.detail) detail = String(payload.detail);
    } catch (_) {
      // ignore
    }
    throw new Error(detail);
  }

  return response.json().catch(() => ({ success: true }));
}

async function ensureGmailAuthWithBackend(interactive = true) {
  let attemptedFreshToken = false;
  while (true) {
    const token = await getAuthToken(interactive);
    try {
      return await registerGmailTokenWithBackend(token);
    } catch (error) {
      const message = String(error?.message || '');
      if (!attemptedFreshToken && message.includes('invalid_google_access_token')) {
        attemptedFreshToken = true;
        await clearCachedAuthToken();
        continue;
      }
      throw error;
    }
  }
}

const GMAIL_API = 'https://gmail.googleapis.com/gmail/v1/users/me';
const DEFAULT_AP_SCAN_QUERY = [
  'in:inbox',
  '(has:attachment OR filename:pdf OR filename:png OR filename:jpg OR filename:jpeg OR filename:docx)',
  '(subject:(invoice OR bill OR "invoice is available" OR "your invoice" OR "invoice available" OR "payment request" OR "amount due" OR "total due" OR "due date") OR "invoice number" OR "amount due" OR "total due")',
  '-subject:(receipt OR confirmation OR paid OR "payment received" OR refund OR chargeback OR dispute OR declined OR "payment failed" OR "card declined" OR "security alert" OR "password" OR "verify" OR newsletter OR promotion OR offer OR webinar OR event)',
  '-category:promotions',
  '-category:social',
  '-category:updates'
].join(' ');

async function searchApEmails({ query, maxResults = 50, pageToken = null, interactive = true } = {}) {
  const token = await getAuthToken(!!interactive);
  const q = String(query || DEFAULT_AP_SCAN_QUERY).trim() || DEFAULT_AP_SCAN_QUERY;

  const url = new URL(`${GMAIL_API}/messages`);
  url.searchParams.set('q', q);
  url.searchParams.set('maxResults', String(Math.max(1, Math.min(200, Number(maxResults) || 50))));
  url.searchParams.set('includeSpamTrash', 'false');
  if (pageToken) url.searchParams.set('pageToken', String(pageToken));

  const response = await fetch(url.toString(), {
    headers: { Authorization: `Bearer ${token}` }
  });

  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err?.error?.message || `Gmail search failed ${response.status}`);
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

async function fetchEmailWithAttachments(emailId) {
  const token = await getAuthToken();
  if (!token) return null;

  let response = await fetch(`${GMAIL_API}/messages/${emailId}?format=full`, {
    headers: { Authorization: `Bearer ${token}` }
  });

  let message = null;
  let messageIdForAttachments = emailId;

  if (response.ok) {
    message = await response.json();
    messageIdForAttachments = message?.id || emailId;
  } else {
    const threadResponse = await fetch(`${GMAIL_API}/threads/${emailId}?format=full`, {
      headers: { Authorization: `Bearer ${token}` }
    });
    if (!threadResponse.ok) return null;
    const thread = await threadResponse.json();
    message = thread.messages?.[0] || null;
    messageIdForAttachments = message?.id || emailId;
  }

  if (!message) return null;

  const attachments = [];
  const allParts = [];

  const flattenParts = (part) => {
    if (!part) return;
    allParts.push(part);
    if (Array.isArray(part.parts)) {
      part.parts.forEach(flattenParts);
    }
  };

  flattenParts(message.payload);

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

  for (const part of allParts) {
    if (!part || !part.filename || !part.body?.attachmentId) continue;
    if (!isSupportedAttachment({ mimeType: part.mimeType, filename: part.filename })) continue;
    if (attachments.length >= MAX_ATTACHMENT_COUNT) break;

    const size = Number(part.body.size || 0);
    if (size > MAX_ATTACHMENT_BYTES) continue;

    const attResponse = await fetch(
      `${GMAIL_API}/messages/${messageIdForAttachments}/attachments/${part.body.attachmentId}`,
      { headers: { Authorization: `Bearer ${token}` } }
    );

    if (!attResponse.ok) continue;
    const attData = await attResponse.json().catch(() => ({}));
    if (!attData?.data) continue;

    const base64 = String(attData.data).replace(/-/g, '+').replace(/_/g, '/');

    attachments.push({
      filename: part.filename,
      content_type: part.mimeType,
      content_base64: base64,
      size: size
    });
  }

  let bodyText = '';
  if (message.payload?.body?.data) {
    bodyText = atob(message.payload.body.data.replace(/-/g, '+').replace(/_/g, '/'));
  }

  return { subject, sender, date, snippet, body: bodyText, attachments };
}

async function triageEmail(emailData) {
  try {
    const backendUrl = await getBackendUrl();
    const organizationId = await getOrganizationId();
    const userEmail = await getUserEmail();

    const enriched = await fetchEmailWithAttachments(emailData.id);
    const subject = emailData.subject || enriched?.subject || '';
    const sender = emailData.sender || enriched?.sender || '';
    const snippet = emailData.snippet || enriched?.snippet || '';
    const body = enriched?.body || snippet || '';
    const attachments = enriched?.attachments || [];

    const response = await fetchWithRetry(`${backendUrl}/extension/triage`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Organization-ID': organizationId },
      body: JSON.stringify({
        email_id: emailData.id,
        subject,
        sender,
        snippet,
        body,
        attachments,
        organization_id: organizationId,
        user_email: userEmail,
        thread_id: emailData.threadId || emailData.id,
        message_id: emailData.id
      })
    });

    if (!response.ok) {
      throw new Error(`triage_failed_${response.status}`);
    }

    const result = await response.json();
    result._gmail = { subject, sender, snippet, date: enriched?.date || null };
    return result;
  } catch (error) {
    return { success: false, error: error.message };
  }
}

async function listBrowserTabs() {
  const tabs = await chrome.tabs.query({});
  return (tabs || []).map((tab) => ({
    tabId: tab.id,
    title: tab.title || '',
    url: tab.url || '',
    active: Boolean(tab.active),
    windowId: tab.windowId
  }));
}

async function resolveTargetTab(command = {}) {
  const target = command.target || {};
  const requestedTabId = Number(target.tab_id || target.tabId);
  if (Number.isFinite(requestedTabId) && requestedTabId > 0) {
    try {
      const tab = await chrome.tabs.get(requestedTabId);
      if (tab?.id) return tab.id;
    } catch (_) {
      // fallback below
    }
  }

  const targetUrl = String(target.url || command.url || '').trim();
  if (targetUrl) {
    const tabs = await chrome.tabs.query({});
    const match = (tabs || []).find((tab) => String(tab.url || '').startsWith(targetUrl));
    if (match?.id) return match.id;
  }

  const [activeTab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return activeTab?.id || null;
}

function runBrowserCommand(command) {
  const tool = String(command?.tool_name || '').toLowerCase();
  const params = command?.params || {};
  const target = command?.target || {};
  const selector = String(params.selector || target.selector || '').trim();

  const collectText = (element) => {
    if (!element) return '';
    return String(element.innerText || element.textContent || '').replace(/\s+/g, ' ').trim();
  };

  const selectorCandidates = (() => {
    const candidates = [];
    if (Array.isArray(params.selector_candidates)) {
      for (const value of params.selector_candidates) {
        const candidate = String(value || '').trim();
        if (candidate) candidates.push(candidate);
      }
    } else if (typeof params.selector_candidates === 'string') {
      for (const value of params.selector_candidates.split('||')) {
        const candidate = String(value || '').trim();
        if (candidate) candidates.push(candidate);
      }
    }
    if (selector) candidates.unshift(selector);
    return Array.from(new Set(candidates));
  })();

  const resolveElement = () => {
    for (const candidate of selectorCandidates) {
      try {
        const element = document.querySelector(candidate);
        if (element) return { element, selector: candidate };
      } catch (_) {
        // continue to next selector candidate.
      }
    }
    return { element: null, selector: selectorCandidates[0] || selector };
  };

  if (tool === 'read_page') {
    const headings = Array.from(document.querySelectorAll('h1, h2, h3'))
      .slice(0, 15)
      .map((el) => collectText(el))
      .filter(Boolean);
    const bodyText = collectText(document.body).slice(0, 8000);
    return {
      ok: true,
      url: window.location.href,
      title: document.title,
      headings,
      body_text: bodyText
    };
  }

  if (tool === 'extract_table') {
    const tableSelector = selector || 'table';
    const table = document.querySelector(tableSelector);
    if (!table) return { ok: false, error: 'table_not_found', selector: tableSelector };
    const rows = Array.from(table.querySelectorAll('tr'))
      .slice(0, 50)
      .map((row) =>
        Array.from(row.querySelectorAll('th,td'))
          .slice(0, 20)
          .map((cell) => collectText(cell))
      );
    return {
      ok: true,
      selector: tableSelector,
      rows
    };
  }

  if (tool === 'find_element') {
    if (!selectorCandidates.length) return { ok: false, error: 'selector_required' };
    const resolved = resolveElement();
    const element = resolved.element;
    if (!element) return { ok: false, error: 'not_found', selector: resolved.selector };
    return {
      ok: true,
      selector: resolved.selector,
      tag: element.tagName?.toLowerCase() || '',
      text: collectText(element).slice(0, 1000)
    };
  }

  if (tool === 'query_selector_all') {
    if (!selectorCandidates.length) return { ok: false, error: 'selector_required' };
    let appliedSelector = '';
    let elements = [];
    for (const candidate of selectorCandidates) {
      try {
        const matches = Array.from(document.querySelectorAll(candidate));
        appliedSelector = candidate;
        elements = matches;
        if (matches.length > 0) break;
      } catch (_) {
        continue;
      }
    }
    if (!appliedSelector) {
      return { ok: false, error: 'invalid_selector', selector: selectorCandidates[0] };
    }
    const limitRaw = Number(params.limit);
    const limit = Number.isFinite(limitRaw) ? Math.max(1, Math.min(50, Math.floor(limitRaw))) : 20;
    return {
      ok: true,
      selector: appliedSelector,
      count: elements.length,
      matches: elements.slice(0, limit).map((element) => ({
        tag: element.tagName?.toLowerCase() || '',
        text: collectText(element).slice(0, 240),
        href: element.getAttribute?.('href') || '',
        value: element.getAttribute?.('value') || ''
      }))
    };
  }

  if (tool === 'click') {
    if (!selectorCandidates.length) return { ok: false, error: 'selector_required' };
    const resolved = resolveElement();
    const element = resolved.element;
    if (!element) return { ok: false, error: 'not_found', selector: resolved.selector };
    element.click();
    return { ok: true, selector: resolved.selector };
  }

  if (tool === 'type') {
    if (!selectorCandidates.length) return { ok: false, error: 'selector_required' };
    const resolved = resolveElement();
    const element = resolved.element;
    if (!element) return { ok: false, error: 'not_found', selector: resolved.selector };
    const value = String(params.value ?? '');
    if ('value' in element) {
      element.value = value;
      element.dispatchEvent(new Event('input', { bubbles: true }));
      element.dispatchEvent(new Event('change', { bubbles: true }));
      return { ok: true, selector: resolved.selector, value_length: value.length };
    }
    return { ok: false, error: 'element_not_input', selector: resolved.selector };
  }

  if (tool === 'select') {
    if (!selectorCandidates.length) return { ok: false, error: 'selector_required' };
    const resolved = resolveElement();
    const element = resolved.element;
    if (!element) return { ok: false, error: 'not_found', selector: resolved.selector };
    const value = String(params.value ?? '');
    if (element.tagName?.toLowerCase() === 'select') {
      element.value = value;
      element.dispatchEvent(new Event('change', { bubbles: true }));
      return { ok: true, selector: resolved.selector, value };
    }
    return { ok: false, error: 'element_not_select', selector: resolved.selector };
  }

  if (tool === 'upload_file') {
    if (!selectorCandidates.length) return { ok: false, error: 'selector_required' };
    const resolved = resolveElement();
    const element = resolved.element;
    if (!element) return { ok: false, error: 'not_found', selector: resolved.selector };
    if (element.tagName?.toLowerCase() !== 'input' || String(element.type || '').toLowerCase() !== 'file') {
      return { ok: false, error: 'element_not_file_input', selector: resolved.selector };
    }
    element.click();
    return {
      ok: true,
      selector: resolved.selector,
      status: 'awaiting_user_file_selection',
      note: 'Browser security requires manual file picker confirmation.'
    };
  }

  if (tool === 'drag_drop') {
    const sourceSelector = String(params.source_selector || '').trim();
    const targetSelector = String(params.target_selector || '').trim();
    if (!sourceSelector || !targetSelector) {
      return { ok: false, error: 'source_and_target_required' };
    }
    const source = document.querySelector(sourceSelector);
    const dropTarget = document.querySelector(targetSelector);
    if (!source) return { ok: false, error: 'source_not_found', selector: sourceSelector };
    if (!dropTarget) return { ok: false, error: 'target_not_found', selector: targetSelector };
    try {
      const transfer = typeof DataTransfer !== 'undefined' ? new DataTransfer() : null;
      const dispatchDrag = (eventType, element) => {
        const event = new DragEvent(eventType, {
          bubbles: true,
          cancelable: true,
          dataTransfer: transfer || undefined
        });
        element.dispatchEvent(event);
      };
      dispatchDrag('dragstart', source);
      dispatchDrag('dragenter', dropTarget);
      dispatchDrag('dragover', dropTarget);
      dispatchDrag('drop', dropTarget);
      dispatchDrag('dragend', source);
    } catch (error) {
      return { ok: false, error: 'drag_drop_failed', detail: String(error?.message || error) };
    }
    return {
      ok: true,
      source_selector: sourceSelector,
      target_selector: targetSelector
    };
  }

  if (tool === 'capture_evidence') {
    const resolved = selectorCandidates.length ? resolveElement() : { element: document.body, selector: 'body' };
    const element = resolved.element || document.body;
    return {
      ok: true,
      selector: resolved.selector || 'body',
      html_excerpt: String(element?.outerHTML || '').slice(0, 4000),
      url: window.location.href,
      title: document.title
    };
  }

  return { ok: false, error: `unsupported_tool:${tool}` };
}

async function executeBrowserToolCommand(command = {}) {
  const tool = String(command.tool_name || '').toLowerCase();
  if (tool === 'open_tab') {
    const url = String(command?.target?.url || command?.url || '').trim();
    if (!url) return { ok: false, error: 'url_required' };
    const tab = await chrome.tabs.create({ url, active: false });
    return {
      ok: true,
      tool_name: tool,
      tab_id: tab.id,
      url: tab.url
    };
  }

  if (tool === 'switch_tab') {
    const tabId = Number(command?.target?.tab_id || command?.target?.tabId);
    if (!Number.isFinite(tabId) || tabId <= 0) return { ok: false, error: 'tab_id_required' };
    await chrome.tabs.update(tabId, { active: true });
    return { ok: true, tool_name: tool, tab_id: tabId };
  }

  const tabId = await resolveTargetTab(command);
  if (!tabId) return { ok: false, error: 'target_tab_not_found' };

  const result = await chrome.scripting.executeScript({
    target: { tabId },
    world: 'ISOLATED',
    func: runBrowserCommand,
    args: [command]
  });

  const payload = result?.[0]?.result || { ok: false, error: 'no_result' };
  return {
    ...payload,
    tab_id: tabId,
    tool_name: tool
  };
}

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === 'ensureGmailAuth') {
    ensureGmailAuthWithBackend(!!request.interactive)
      .then((payload) => sendResponse({
        success: true,
        email: payload?.email || null,
        userId: payload?.user_id || null
      }))
      .catch((error) => sendResponse({ success: false, error: error.message }));
    return true;
  }

  if (request.action === 'searchApEmails') {
    searchApEmails({
      query: request.query,
      maxResults: request.maxResults,
      pageToken: request.pageToken,
      interactive: request.interactive
    })
      .then((result) => sendResponse(result))
      .catch((error) => sendResponse({ success: false, error: error.message }));
    return true;
  }

  if (request.action === 'triageEmail') {
    triageEmail(request.data)
      .then((result) => sendResponse(result))
      .catch((error) => sendResponse({ success: false, error: error.message }));
    return true;
  }

  if (request.action === 'listBrowserTabs') {
    listBrowserTabs()
      .then((tabs) => sendResponse({ success: true, tabs }))
      .catch((error) => sendResponse({ success: false, error: error.message }));
    return true;
  }

  if (request.action === 'executeBrowserToolCommand') {
    executeBrowserToolCommand(request.command || {})
      .then((result) => sendResponse({ success: true, result }))
      .catch((error) => sendResponse({ success: false, error: error.message }));
    return true;
  }
});
