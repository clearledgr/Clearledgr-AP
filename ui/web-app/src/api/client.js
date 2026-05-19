/**
 * SPA API client. Mirrors the Gmail extension's `backendFetch` contract
 * (URL, method, JSON body, retries on 502/503/504) but uses the
 * workspace session cookie instead of an extension-storage Bearer token.
 *
 * The backend already issues HttpOnly + CSRF cookie pairs from the
 * `/auth/google/exchange` and `/auth/google/callback` flows
 * (see clearledgr/api/auth.py:60-90). We forward the CSRF token as
 * a header on state-changing requests.
 */

const RETRY_STATUSES = new Set([502, 503, 504]);
const RETRY_BACKOFFS_MS = [400, 1200, 2400];

const CSRF_COOKIE_NAME = 'solden_workspace_csrf';

export class ApiError extends Error {
  constructor(status, payload) {
    super(payload?.detail || payload?.error || `HTTP ${status}`);
    this.status = status;
    this.payload = payload;
  }
}

function readCookie(name) {
  const match = document.cookie
    .split(';')
    .map((c) => c.trim())
    .find((c) => c.startsWith(`${name}=`));
  return match ? decodeURIComponent(match.slice(name.length + 1)) : '';
}

function isStateChanging(method) {
  const m = (method || 'GET').toUpperCase();
  return m === 'POST' || m === 'PUT' || m === 'PATCH' || m === 'DELETE';
}

export async function api(path, options = {}) {
  const {
    method = 'GET',
    body,
    headers = {},
    signal,
    retry = true,
  } = options;

  const finalHeaders = { Accept: 'application/json', ...headers };
  let payloadBody = body;

  if (body !== undefined && !(body instanceof FormData)) {
    // Set Content-Type to application/json by default for any body
    // (object → JSON.stringify here, string → caller already
    // stringified). Without this, FastAPI's default body parser
    // refused pre-stringified bodies with 422 Unprocessable Content.
    // Callers that need a different content-type can still override
    // via the headers option above.
    if (!finalHeaders['Content-Type']) finalHeaders['Content-Type'] = 'application/json';
    if (typeof body !== 'string') payloadBody = JSON.stringify(body);
  }

  if (isStateChanging(method)) {
    const csrf = readCookie(CSRF_COOKIE_NAME);
    if (csrf) finalHeaders['X-CSRF-Token'] = csrf;
  }

  const attempts = retry ? RETRY_BACKOFFS_MS.length + 1 : 1;
  let lastError;

  for (let i = 0; i < attempts; i++) {
    try {
      const response = await fetch(path, {
        method,
        body: payloadBody,
        headers: finalHeaders,
        credentials: 'include',
        signal,
      });

      if (RETRY_STATUSES.has(response.status) && i < attempts - 1) {
        await sleep(RETRY_BACKOFFS_MS[i]);
        continue;
      }

      const text = await response.text();
      const json = text ? safeJson(text) : null;

      if (!response.ok) {
        // Stale-session escape hatch: when the workspace session
        // cookie expires the SPA's cachedSession in useSession.js
        // doesn't auto-invalidate (it's a module-level variable that
        // outlives the cookie's 60-min TTL), so AuthGate keeps
        // rendering the shell while every actual API call 401s. On
        // any 401 we dispatch a custom event so useSession can
        // re-probe /auth/me and AuthGate can redirect to /login.
        // Skip /auth/me itself — its 401 is the normal logged-out
        // signal that loadSession() handles directly.
        if (response.status === 401 && typeof window !== 'undefined' && !path.startsWith('/auth/me')) {
          try { window.dispatchEvent(new CustomEvent('solden:session-stale')); } catch { /* old browsers */ }
        }
        throw new ApiError(response.status, json);
      }
      return json;
    } catch (err) {
      lastError = err;
      if (err.name === 'AbortError') throw err;
      if (i >= attempts - 1) throw err;
      await sleep(RETRY_BACKOFFS_MS[i]);
    }
  }
  throw lastError;
}

function safeJson(text) {
  try {
    return JSON.parse(text);
  } catch {
    return { raw: text };
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
