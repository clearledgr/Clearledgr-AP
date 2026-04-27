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

async function loadSession() {
  if (typeof window !== 'undefined') {
    const path = window.location.pathname || '';
    if (UNAUTHENTICATED_ROUTES.has(path)) {
      cachedSession = null;
      notify();
      return cachedSession;
    }
  }
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
