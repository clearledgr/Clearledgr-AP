import { useEffect, useState, useCallback } from 'preact/hooks';
import { api, ApiError } from '../api/client.js';

const sessionListeners = new Set();
let cachedSession = undefined; // undefined = unloaded, null = unauthenticated, object = authenticated

function notify() {
  for (const fn of sessionListeners) fn(cachedSession);
}

// Routes that exist for unauthenticated users. Probing /auth/me on
// these is wasted work and generates a misleading "401" line in the
// browser console (Chrome logs every non-2xx fetch as a "Failed to
// load resource" warning, which is unsuppressible from JS). Skip the
// probe and short-circuit to "logged out" — anything that needs an
// authenticated session calls refreshSession() explicitly after the
// auth flow lands.
const UNAUTHENTICATED_ROUTES = new Set([
  '/login',
  '/privacy',
  '/terms',
  '/request-demo',
  '/status',
]);

async function _consumePostOAuthAuthCode() {
  // After /auth/google/callback redirects back to the SPA, the URL
  // carries ?post_oauth=1&auth_code=XXX&org=YYY. The api expects the
  // SPA to POST that one-time auth_code to /auth/google/exchange,
  // which sets the HttpOnly session cookie + CSRF cookie on
  // .clearledgr.com. Without this step /auth/me returns 401 and the
  // user bounces back to /login even though the OAuth handshake
  // succeeded — which is exactly the loop Mo reported.
  if (typeof window === 'undefined') return false;
  const params = new URLSearchParams(window.location.search);
  const authCode = (params.get('auth_code') || '').trim();
  if (!authCode) return false;
  try {
    await api('/auth/google/exchange', {
      method: 'POST',
      body: { auth_code: authCode },
      retry: false,
    });
  } catch {
    // If exchange fails (expired code, rotated key, etc.) fall
    // through and let /auth/me return 401 cleanly. The user lands on
    // /login and can retry.
    return false;
  }
  // Clean the auth_code out of the URL so a refresh doesn't try to
  // re-exchange (the code is one-time on the api side and the second
  // attempt would 400). Preserves any other query params.
  try {
    params.delete('auth_code');
    params.delete('org');
    params.delete('post_oauth');
    const next = `${window.location.pathname}${params.toString() ? `?${params.toString()}` : ''}${window.location.hash || ''}`;
    window.history.replaceState({}, '', next);
  } catch {
    /* ignore — best-effort URL cleanup */
  }
  return true;
}

async function loadSession() {
  if (typeof window !== 'undefined') {
    const path = window.location.pathname || '';
    if (UNAUTHENTICATED_ROUTES.has(path)) {
      cachedSession = null;
      notify();
      return cachedSession;
    }
  }
  // Step 1: if the URL carries a fresh OAuth auth_code, exchange it
  // for session cookies BEFORE we probe /auth/me. The exchange call
  // sets cookies via the api response; subsequent /auth/me reads them.
  await _consumePostOAuthAuthCode();
  try {
    const me = await api('/auth/me', { retry: false });
    cachedSession = me;
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) {
      cachedSession = null;
    } else {
      cachedSession = null;
    }
  }
  notify();
  return cachedSession;
}

export async function refreshSession() {
  return loadSession();
}

export async function logout() {
  try {
    await api('/auth/logout', { method: 'POST', retry: false });
  } catch {
    /* swallow — local state still resets below */
  }
  cachedSession = null;
  notify();
}

export function useSession() {
  const [session, setSession] = useState(cachedSession);

  useEffect(() => {
    const listener = (next) => setSession(next);
    sessionListeners.add(listener);
    if (cachedSession === undefined) loadSession();
    return () => sessionListeners.delete(listener);
  }, []);

  const refresh = useCallback(() => loadSession(), []);

  return {
    session,
    isLoading: session === undefined,
    isAuthenticated: !!session,
    refresh,
  };
}
