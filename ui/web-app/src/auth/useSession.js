import { useEffect, useState, useCallback } from 'preact/hooks';
import { api, ApiError } from '../api/client.js';

const sessionListeners = new Set();
let cachedSession = undefined; // undefined = unloaded, null = unauthenticated, object = authenticated

function notify() {
  for (const fn of sessionListeners) fn(cachedSession);
}

async function loadSession() {
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
