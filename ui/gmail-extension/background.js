// Clearledgr AP v1 Background Service Worker
// config.js is injected by build.sh
try {
  importScripts('config.js');
} catch (_) {
  // Best effort. Built bundles may inject config directly.
}

const RetryConfig = {
  maxRetries: 2,
  baseDelay: 800,
  maxDelay: 10000,
  backoffMultiplier: 2,
  retryableStatusCodes: [408, 429, 500, 502, 503, 504]
};

const TAB_PENDING_DIRECT_ROUTE_PREFIX = '__clearledgr_tab_pending_direct_route_v1__';
const TAB_PENDING_DIRECT_ROUTE_TTL_MS = 30000;

function normalizeClearledgrHashFromUrl(rawUrl = '') {
  try {
    const parsed = new URL(String(rawUrl || ''));
    const hash = String(parsed.hash || '').trim().replace(/^#/, '').split('?')[0];
    return hash.startsWith('clearledgr/') ? hash : '';
  } catch (_) {
    return '';
  }
}

function getPendingDirectRouteStorageKey(tabId) {
  return `${TAB_PENDING_DIRECT_ROUTE_PREFIX}${tabId}`;
}

async function storePendingDirectRouteForTab(tabId, rawUrl) {
  const normalizedHash = normalizeClearledgrHashFromUrl(rawUrl);
  if (!Number.isFinite(Number(tabId)) || !normalizedHash || !chrome.storage?.session?.set) return;
  try {
    await chrome.storage.session.set({
      [getPendingDirectRouteStorageKey(tabId)]: {
        hash: normalizedHash,
        ts: Date.now(),
        pathname: (() => {
          try { return new URL(String(rawUrl || '')).pathname || ''; } catch (_) { return ''; }
        })(),
      },
    });
  } catch (_) {
    /* best effort */
  }
}

async function readPendingDirectRouteForTab(tabId) {
  if (!Number.isFinite(Number(tabId)) || !chrome.storage?.session?.get) return null;
  try {
    const key = getPendingDirectRouteStorageKey(tabId);
    const payload = await chrome.storage.session.get([key]);
    const pending = payload?.[key];
    const hash = String(pending?.hash || '').trim();
    const ts = Number(pending?.ts || 0);
    if (!hash.startsWith('clearledgr/')) return null;
    if (!Number.isFinite(ts) || (Date.now() - ts) > TAB_PENDING_DIRECT_ROUTE_TTL_MS) return null;
    return {
      hash,
      pathname: String(pending?.pathname || ''),
      ts,
    };
  } catch (_) {
    return null;
  }
}

async function clearPendingDirectRouteForTab(tabId) {
  if (!Number.isFinite(Number(tabId)) || !chrome.storage?.session?.remove) return;
  try {
    await chrome.storage.session.remove(getPendingDirectRouteStorageKey(tabId));
  } catch (_) {
    /* best effort */
  }
}

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  const candidateUrl = String(changeInfo?.url || tab?.url || '').trim();
  if (!candidateUrl) return;
  const normalizedHash = normalizeClearledgrHashFromUrl(candidateUrl);
  if (!normalizedHash) return;
  void storePendingDirectRouteForTab(tabId, candidateUrl);
});

chrome.tabs.onRemoved.addListener((tabId) => {
  void clearPendingDirectRouteForTab(tabId);
});

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
function getConfiguredBackendUrl() {
  const globalConfig =
    (typeof self !== 'undefined' && self.CLEARLEDGR_CONFIG)
    || (typeof globalThis !== 'undefined' && globalThis.CLEARLEDGR_CONFIG)
    || (typeof self !== 'undefined' && self.CONFIG)
    || (typeof globalThis !== 'undefined' && globalThis.CONFIG)
    || null;
  if (!globalConfig) return '';
  return String(globalConfig.API_URL || globalConfig.BACKEND_URL || '').trim();
}

function normalizeBackendUrl(raw) {
  let url = String(raw || '').trim();
  if (!url) {
    url = getConfiguredBackendUrl();
  }
  if (!url) return 'http://127.0.0.1:8010';
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

function selectBackendUrl(storedUrl, configuredUrl) {
  const configured = normalizeBackendUrl(configuredUrl);
  const stored = normalizeBackendUrl(storedUrl);
  if (!configuredUrl) return stored;
  if (!storedUrl) return configured;
  try {
    const configuredParsed = new URL(configured);
    const storedParsed = new URL(stored);
    const configuredHost = configuredParsed.hostname.toLowerCase();
    const storedHost = storedParsed.hostname.toLowerCase();
    const configuredSecure = configuredParsed.protocol === 'https:';
    const looksEphemeralStoredHost =
      storedHost === '127.0.0.1' ||
      storedHost === 'localhost' ||
      storedHost.endsWith('.trycloudflare.com') ||
      storedHost.endsWith('.up.railway.app');
    if (configuredSecure && configuredHost === 'api.clearledgr.com' && looksEphemeralStoredHost) {
      return configured;
    }
  } catch (_) {
    return configured || stored;
  }
  return stored;
}

function shouldClearStoredBackendOverride(storedUrl, configuredUrl) {
  const configured = normalizeBackendUrl(configuredUrl);
  const stored = normalizeBackendUrl(storedUrl);
  if (!configuredUrl || !storedUrl) return false;
  try {
    const configuredParsed = new URL(configured);
    const storedParsed = new URL(stored);
    const configuredHost = configuredParsed.hostname.toLowerCase();
    const storedHost = storedParsed.hostname.toLowerCase();
    return configuredParsed.protocol === 'https:' && configuredHost === 'api.clearledgr.com' && (
      storedHost === '127.0.0.1' ||
      storedHost === 'localhost' ||
      storedHost.endsWith('.trycloudflare.com') ||
      storedHost.endsWith('.up.railway.app')
    );
  } catch (_) {
    return false;
  }
}

async function clearStoredBackendOverride(data, nested, configuredBackendUrl) {
  const storedBackendUrl = data.backendUrl || nested.backendUrl || nested.apiEndpoint || null;
  if (!shouldClearStoredBackendOverride(storedBackendUrl, configuredBackendUrl)) return;
  const nextNested = { ...nested };
  delete nextNested.backendUrl;
  delete nextNested.apiEndpoint;
  try {
    await chrome.storage.sync.set({ settings: nextNested });
    await chrome.storage.sync.remove(['backendUrl']);
  } catch (_) {
    /* best effort */
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
  const configuredBackendUrl = getConfiguredBackendUrl();
  await clearStoredBackendOverride(data, nested, configuredBackendUrl);
  return {
    ...nested,
    backendUrl: selectBackendUrl(
      data.backendUrl || nested.backendUrl || nested.apiEndpoint || null,
      configuredBackendUrl
    ),
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
let authFlowPromise = null;
let lastInteractiveAuthLaunchAt = 0;
const INTERACTIVE_AUTH_COOLDOWN_MS = 60000;

function classifyAuthErrorCode(raw) {
  const message = String(raw || '').trim();
  const normalized = message.toLowerCase();
  if (!normalized) return 'authorization_failed';
  if (normalized.includes('redirect_uri_mismatch')) return 'redirect_uri_mismatch';
  if (normalized.includes('invalid_client')) return 'invalid_client';
  if (normalized.includes('access_denied')) return 'access_denied';
  if (normalized.includes('no oauth response url')) return 'oauth_no_response_url';
  if (normalized.includes('no access token')) return 'oauth_no_access_token';
  if (normalized.includes('network') || normalized.includes('fetch')) return 'network_error';
  return normalized.replace(/\s+/g, '_');
}

async function getAuthToken(interactive = true, options = {}) {
  const forceFresh = options?.forceFresh === true;
  if (!forceFresh && cachedToken && tokenExpiry && Date.now() < tokenExpiry) return cachedToken;
  const stored = forceFresh
    ? {}
    : await chrome.storage.local.get(['gmail_token', 'gmail_token_expiry']);
  if (!forceFresh && stored.gmail_token && stored.gmail_token_expiry && Date.now() < stored.gmail_token_expiry) {
    cachedToken = stored.gmail_token;
    tokenExpiry = stored.gmail_token_expiry;
    return cachedToken;
  }
  // Clear stale token so the next attempt starts fresh
  await clearCachedAuthToken();
  if (!interactive) {
    // Non-interactive: try silent token refresh via chrome.identity
    // (works if user has an active Google session)
    try {
      const silentToken = await new Promise((resolve, reject) => {
        chrome.identity.getAuthToken({ interactive: false }, (token) => {
          if (chrome.runtime.lastError || !token) reject(new Error('silent_refresh_failed'));
          else resolve(token);
        });
      });
      if (silentToken) {
        cachedToken = silentToken;
        tokenExpiry = Date.now() + 3500 * 1000;
        await chrome.storage.local.set({ gmail_token: silentToken, gmail_token_expiry: tokenExpiry });
        return silentToken;
      }
    } catch (_) { /* silent refresh unavailable — need interactive */ }
    throw new Error('No valid token');
  }
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
    authUrl.searchParams.set('response_type', 'code');
    authUrl.searchParams.set('access_type', 'offline');
    authUrl.searchParams.set('scope', OAUTH_CONFIG.scopes.join(' '));
    // Note: deliberately NOT setting `prompt=consent` and
    // `include_granted_scopes=true` — that combination interacted
    // poorly with our Web Application OAuth client and Google
    // returned `invalid_grant: Bad Request` from the token endpoint
    // even though the auth phase succeeded. Letting Google manage
    // re-prompting (it does so automatically when scopes change or
    // the grant is stale) keeps the simpler, more-tolerated flow.

    chrome.identity.launchWebAuthFlow({ url: authUrl.toString(), interactive: true }, (responseUrl) => {
      if (chrome.runtime.lastError) {
        reject(new Error(chrome.runtime.lastError.message));
        return;
      }
      if (!responseUrl) {
        reject(new Error('No OAuth response URL'));
        return;
      }
      // Authorization code flow: code is in query params, not hash fragment
      const url = new URL(responseUrl);
      const code = url.searchParams.get('code');
      if (code) {
        // Exchange code for tokens via backend (backend has client_secret)
        exchangeCodeForTokens(code, redirectUrl).then(resolve).catch(reject);
        return;
      }
      // Fallback: implicit flow (access_token in hash)
      const params = new URLSearchParams(url.hash.slice(1));
      const token = params.get('access_token');
      const expiresIn = parseInt(params.get('expires_in') || '3600', 10);
      if (!token) {
        reject(new Error('No access token or authorization code'));
        return;
      }
      cachedToken = token;
      tokenExpiry = Date.now() + (expiresIn * 1000) - 60000;
      chrome.storage.local.set({ gmail_token: token, gmail_token_expiry: tokenExpiry });
      resolve(token);
    });
  });
}

async function exchangeCodeForTokens(code, redirectUri) {
  const backendUrl = await getBackendUrl();
  const response = await fetchWithRetry(`${backendUrl}/extension/gmail/exchange-code`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code, redirect_uri: redirectUri })
  }, 1);
  if (!response.ok) {
    let detail = `code_exchange_failed_${response.status}`;
    try {
      const payload = await response.json();
      if (payload?.detail) detail = String(payload.detail);
    } catch (_) { /* ignore */ }
    throw new Error(detail);
  }
  const result = await response.json();
  // Backend exchanged code and stored tokens (including refresh token for 24/7 scanning).
  // Cache the access token locally for extension API calls.
  const accessToken = result.access_token || '';
  const expiresIn = Number(result.expires_in || 3600);
  if (accessToken) {
    cachedToken = accessToken;
    tokenExpiry = Date.now() + (expiresIn * 1000) - 60000;
    chrome.storage.local.set({ gmail_token: accessToken, gmail_token_expiry: tokenExpiry });
  }
  // Mark that backend registration is already done (code exchange did it)
  codeExchangeResult = result;
  return accessToken;
}

// Set by exchangeCodeForTokens so ensureGmailAuthWithBackend can skip double-registration
let codeExchangeResult = null;

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
      email: profile?.email || null,
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
  const wantsInteractive = Boolean(interactive);
  const now = Date.now();
  if (wantsInteractive) {
    const retryAfterMs = (lastInteractiveAuthLaunchAt + INTERACTIVE_AUTH_COOLDOWN_MS) - now;
    if (retryAfterMs > 0) {
      return {
        success: false,
        error: 'interactive_auth_cooldown',
        retry_after_seconds: Math.ceil(retryAfterMs / 1000),
      };
    }
    lastInteractiveAuthLaunchAt = now;
  }
  if (authFlowPromise) return authFlowPromise;

  const run = async () => {
    const token = await getAuthToken(wantsInteractive, { forceFresh: wantsInteractive });
    if (!token) {
      return { success: false, error: 'no_google_token' };
    }

    let backendRegistration = null;
    if (codeExchangeResult && typeof codeExchangeResult === 'object') {
      backendRegistration = codeExchangeResult;
      codeExchangeResult = null;
    } else {
      try {
        backendRegistration = await registerGmailTokenWithBackend(token);
      } catch (_) {
        backendRegistration = null;
      }
    }

    const profile = await getProfileUserInfo();
    const backendAccessToken = String(
      backendRegistration?.backend_access_token
      || backendRegistration?.backendAccessToken
      || token
      || ''
    ).trim() || null;
    const backendExpiresIn = Number(
      backendRegistration?.backend_expires_in
      || backendRegistration?.backendExpiresIn
      || getTokenTtlSeconds()
    ) || getTokenTtlSeconds();
    const organizationId = String(
      backendRegistration?.organization_id
      || backendRegistration?.organizationId
      || 'default'
    ).trim() || 'default';
    const userId = String(
      backendRegistration?.user_id
      || backendRegistration?.userId
      || profile?.id
      || ''
    ).trim() || null;
    // Cache backend auth in service-worker-accessible storage so the
    // alarm-driven pipeline-notification poller can hit the backend
    // without re-running the full OAuth → register dance every minute.
    try {
      const cacheTtlMs = Math.max(60, Number(backendExpiresIn) || 0) * 1000;
      const cachedBackendUrl = await getBackendUrl();
      await chrome.storage.local.set({
        cl_backend_token: backendAccessToken,
        cl_backend_token_expiry: Date.now() + cacheTtlMs,
        cl_organization_id: organizationId,
        cl_user_email: profile?.email || null,
        cl_backend_url: cachedBackendUrl || null,
      });
    } catch (_) {}

    return {
      success: true,
      backendAccessToken,
      backendExpiresIn,
      organizationId,
      email: profile?.email || null,
      userId,
    };
  };

  authFlowPromise = run().finally(() => {
    authFlowPromise = null;
  });
  return authFlowPromise;
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

  const retryableTools = new Set(['find_element', 'query_selector_all', 'click', 'type', 'select', 'upload_file', 'drag_drop']);
  const retryableErrors = new Set([
    'not_found',
    'invalid_selector',
    'source_not_found',
    'target_not_found',
    'element_not_input',
    'element_not_select',
    'element_not_file_input',
    'drag_drop_failed'
  ]);
  const requestedAttempts = Math.floor(Number(command?.params?.max_attempts ?? 1));
  const maxAttempts = retryableTools.has(tool) ? Math.max(1, Math.min(5, Number.isFinite(requestedAttempts) ? requestedAttempts : 1)) : 1;
  const requestedDelay = Math.floor(Number(command?.params?.retry_delay_ms ?? 250));
  const retryDelayMs = Math.max(100, Math.min(2000, Number.isFinite(requestedDelay) ? requestedDelay : 250));

  let lastPayload = { ok: false, error: 'no_result' };
  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    try {
      const result = await chrome.scripting.executeScript({
        target: { tabId },
        world: 'ISOLATED',
        func: runBrowserCommand,
        args: [command]
      });
      lastPayload = result?.[0]?.result || { ok: false, error: 'no_result' };
    } catch (error) {
      lastPayload = { ok: false, error: 'execute_script_failed', detail: String(error?.message || error) };
    }

    if (lastPayload?.ok) {
      return {
        ...lastPayload,
        tab_id: tabId,
        tool_name: tool,
        attempts: attempt
      };
    }

    const shouldRetry = (
      attempt < maxAttempts
      && retryableTools.has(tool)
      && retryableErrors.has(String(lastPayload?.error || ''))
    );
    if (!shouldRetry) {
      return {
        ...lastPayload,
        tab_id: tabId,
        tool_name: tool,
        attempts: attempt
      };
    }

    await new Promise((resolve) => setTimeout(resolve, retryDelayMs * attempt));
  }

  return {
    ...lastPayload,
    tab_id: tabId,
    tool_name: tool,
    attempts: maxAttempts
  };
}

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === 'ensureGmailAuth') {
    ensureGmailAuthWithBackend(!!request.interactive)
      .then((payload) => {
        if (payload?.success === false) {
          sendResponse({
            success: false,
            error: String(payload?.error || 'authorization_failed'),
            retryAfterSeconds: Number(payload?.retry_after_seconds || 0) || 0,
          });
          return;
        }
        sendResponse({
          success: true,
          email: payload?.email || null,
          userId: payload?.user_id || payload?.userId || null,
          organizationId: payload?.organization_id || payload?.organizationId || 'default',
          backendAccessToken: payload?.backend_access_token || payload?.backendAccessToken || null,
          backendTokenType: payload?.backend_token_type || payload?.backendTokenType || 'bearer',
          backendExpiresIn: Number(payload?.backend_expires_in || payload?.backendExpiresIn || 0) || 0,
          retryAfterSeconds: Number(payload?.retry_after_seconds || 0) || 0,
        });
      })
      .catch((error) => sendResponse({ success: false, error: classifyAuthErrorCode(error?.message) }));
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

  if (request.action === 'getPendingDirectRouteForTab') {
    const tabId = Number(sender?.tab?.id || request?.tabId);
    readPendingDirectRouteForTab(tabId)
      .then((pending) => sendResponse({ success: true, pending }))
      .catch((error) => sendResponse({ success: false, error: error.message }));
    return true;
  }

  if (request.action === 'clearPendingDirectRouteForTab') {
    const tabId = Number(sender?.tab?.id || request?.tabId);
    clearPendingDirectRouteForTab(tabId)
      .then(() => sendResponse({ success: true }))
      .catch((error) => sendResponse({ success: false, error: error.message }));
    return true;
  }

  if (request.action === 'executeBrowserToolCommand') {
    executeBrowserToolCommand(request.command || {})
      .then((result) => sendResponse({ success: true, result }))
      .catch((error) => sendResponse({ success: false, error: error.message }));
    return true;
  }

  if (request.action === 'showNotification') {
    const { title, message, iconUrl, notificationId } = request;
    chrome.notifications.create(notificationId || `cl-${Date.now()}`, {
      type: 'basic',
      iconUrl: iconUrl || 'icons/icon128.png',
      title: title || 'Clearledgr',
      message: message || '',
      priority: 1,
    });
    sendResponse({ success: true });
    return false;
  }
});

// ===========================================================================
// Pipeline desktop notifications — DESIGN_THESIS.md §3 Gmail Power Features
//
// Streak-equivalent: "Gmail desktop notifications when Boxes are updated,
// moved to new stages, or assigned." We poll the backend audit feed on a
// 1-minute alarm (MV3 service workers cannot use setInterval — the worker
// unloads after ~30s of inactivity). The poller reads the operator-facing
// audit normalisation which already carries `operator_importance` and
// human-readable title/message — only `high` importance events trigger
// desktop notifications to preserve the signal-to-noise ratio that §4.04
// of the thesis mandates ("exceptions are the only interruptions").
//
// State lives in chrome.storage.local:
//   cl_backend_token / cl_backend_token_expiry / cl_organization_id
//     — cached at signin, read by the poller on each tick
//   cl_notif_cursor_ts
//     — ISO timestamp of the most recent event already notified on; the
//       poller only fires notifications for events strictly newer than
//       this, preventing duplicate notifications on worker restart
// ===========================================================================

const PIPELINE_NOTIFICATION_ALARM = 'cl-pipeline-notifications';
const PIPELINE_NOTIFICATION_PERIOD_MINUTES = 1;

async function _getCachedBackendAuth() {
  try {
    const { cl_backend_token, cl_backend_token_expiry, cl_organization_id } =
      await chrome.storage.local.get([
        'cl_backend_token', 'cl_backend_token_expiry', 'cl_organization_id',
      ]);
    if (!cl_backend_token) return null;
    if (cl_backend_token_expiry && Date.now() > Number(cl_backend_token_expiry)) {
      return null;  // expired — skip until next register
    }
    return {
      token: cl_backend_token,
      organizationId: cl_organization_id || 'default',
    };
  } catch (_) {
    return null;
  }
}

function _titleForImportance(importance) {
  if (importance === 'high') return 'Clearledgr — attention needed';
  return 'Clearledgr';
}

async function _pollPipelineNotifications() {
  const auth = await _getCachedBackendAuth();
  if (!auth) return;  // not signed in / token expired — silent skip

  const backendUrl = await getBackendUrl();
  if (!backendUrl) return;

  let events = [];
  try {
    const resp = await fetch(
      `${backendUrl}/api/ap/audit/recent?organization_id=${encodeURIComponent(auth.organizationId)}&limit=30`,
      { headers: { Authorization: `Bearer ${auth.token}` } },
    );
    if (!resp.ok) return;
    const data = await resp.json();
    events = Array.isArray(data?.events) ? data.events : [];
  } catch (_) {
    return;  // network blip — try again next tick
  }

  if (!events.length) return;

  const { cl_notif_cursor_ts } = await chrome.storage.local.get(['cl_notif_cursor_ts']);
  const cursorTs = cl_notif_cursor_ts || '';

  // events arrive newest-first. Walk oldest-to-newest so chronological
  // order is preserved in the notification tray.
  const ordered = [...events].reverse();
  let newestTs = cursorTs;
  let fired = 0;

  for (const event of ordered) {
    const ts = String(event?.ts || '');
    if (!ts || (cursorTs && ts <= cursorTs)) continue;

    const importance = String(event?.operator_importance || '').toLowerCase();
    if (importance !== 'high') {
      if (!newestTs || ts > newestTs) newestTs = ts;
      continue;
    }

    const apItemId = String(event?.ap_item_id || '');
    const title = String(event?.operator_title || _titleForImportance(importance));
    const message = String(event?.operator_message || 'A Box moved to a new stage.');
    const notifId = `cl-pipe-${apItemId || 'evt'}-${ts}`;

    try {
      chrome.notifications.create(notifId, {
        type: 'basic',
        iconUrl: 'icons/icon128.png',
        title,
        message,
        priority: 2,
      });
      fired += 1;
    } catch (_) {}

    if (!newestTs || ts > newestTs) newestTs = ts;

    // Cap per-tick so a backlog doesn't spam 30 notifications at once.
    if (fired >= 5) break;
  }

  if (newestTs && newestTs !== cursorTs) {
    try {
      await chrome.storage.local.set({ cl_notif_cursor_ts: newestTs });
    } catch (_) {}
  }
}

function _ensurePipelineNotificationAlarm() {
  try {
    chrome.alarms.get(PIPELINE_NOTIFICATION_ALARM, (existing) => {
      if (!existing) {
        chrome.alarms.create(PIPELINE_NOTIFICATION_ALARM, {
          periodInMinutes: PIPELINE_NOTIFICATION_PERIOD_MINUTES,
        });
      }
    });
  } catch (_) {}
}

chrome.runtime.onInstalled.addListener(_ensurePipelineNotificationAlarm);
chrome.runtime.onStartup.addListener(_ensurePipelineNotificationAlarm);
_ensurePipelineNotificationAlarm();

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm?.name === PIPELINE_NOTIFICATION_ALARM) {
    _pollPipelineNotifications().catch(() => {});
  }
});

// Click a notification → focus Gmail so the AP Manager can act on the Box.
chrome.notifications.onClicked.addListener((notifId) => {
  if (!String(notifId || '').startsWith('cl-pipe-')) return;
  try {
    chrome.tabs.query({ url: 'https://mail.google.com/*' }, (tabs) => {
      if (tabs && tabs.length) {
        chrome.tabs.update(tabs[0].id, { active: true });
        chrome.windows.update(tabs[0].windowId, { focused: true });
      } else {
        chrome.tabs.create({ url: 'https://mail.google.com/' });
      }
      chrome.notifications.clear(notifId);
    });
  } catch (_) {}
});
