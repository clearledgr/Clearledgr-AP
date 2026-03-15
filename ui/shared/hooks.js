/** Shared Preact hooks for Clearledgr UI */
import { h, Component } from 'preact';
import { useState, useCallback, useRef, useEffect } from 'preact/hooks';

/**
 * ErrorBoundary — catches render errors in child components.
 * Usage: html`<${ErrorBoundary} fallback="Could not load section">...children...<//>`
 */
export class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error('[Clearledgr ErrorBoundary]', error, info?.componentStack || '');
  }

  render() {
    if (this.state.error) {
      const fallback = this.props.fallback || 'Something went wrong.';
      const onRetry = () => this.setState({ error: null });
      return h('div', { class: 'cl-error-boundary', role: 'alert' },
        h('p', { class: 'cl-error-message' }, typeof fallback === 'string' ? fallback : 'Something went wrong.'),
        h('button', { class: 'cl-btn cl-btn-secondary', onClick: onRetry, type: 'button' }, 'Retry')
      );
    }
    return this.props.children;
  }
}

/**
 * useAction — guards async actions with pending state, debounce, and dedup.
 * Returns [execute, { pending, error, result }].
 *
 * Example:
 *   const [approve, approveState] = useAction(async (itemId) => {
 *     return await api(`/ap/${itemId}/approve`, { method: 'POST' });
 *   });
 *   html`<button disabled=${approveState.pending} onClick=${() => approve(item.id)}>
 *     ${approveState.pending ? 'Approving...' : 'Approve'}
 *   </button>`
 */
export function useAction(asyncFn, options = {}) {
  const { onSuccess, onError, resetOnExecute = true } = options;
  const [state, setState] = useState({ pending: false, error: null, result: null });
  const inflightRef = useRef(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  const execute = useCallback(async (...args) => {
    // Dedup: ignore if already in flight
    if (inflightRef.current) return inflightRef.current;

    if (resetOnExecute) {
      setState({ pending: true, error: null, result: null });
    } else {
      setState((prev) => ({ ...prev, pending: true, error: null }));
    }

    const promise = asyncFn(...args);
    inflightRef.current = promise;

    try {
      const result = await promise;
      if (mountedRef.current) {
        setState({ pending: false, error: null, result });
        onSuccess?.(result);
      }
      return result;
    } catch (error) {
      if (mountedRef.current) {
        setState({ pending: false, error, result: null });
        onError?.(error);
      }
      throw error;
    } finally {
      inflightRef.current = null;
    }
  }, [asyncFn, onSuccess, onError, resetOnExecute]);

  return [execute, state];
}

/**
 * useFetch — fetches data from an API endpoint with loading/error/data states.
 * Automatically aborts on unmount or when deps change.
 *
 * Example:
 *   const { data, loading, error, refetch } = useFetch(`/api/context/${itemId}`, [itemId]);
 */
export function useFetch(url, deps = []) {
  const [state, setState] = useState({ data: null, loading: true, error: null });
  const abortRef = useRef(null);

  const fetchData = useCallback(async () => {
    if (!url) {
      setState({ data: null, loading: false, error: null });
      return;
    }

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setState((prev) => ({ ...prev, loading: true, error: null }));

    try {
      const response = await fetch(url, { signal: controller.signal });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      if (!controller.signal.aborted) {
        setState({ data, loading: false, error: null });
      }
    } catch (error) {
      if (error.name === 'AbortError') return;
      setState({ data: null, loading: false, error });
    }
  }, [url, ...deps]);

  useEffect(() => {
    fetchData();
    return () => abortRef.current?.abort();
  }, [fetchData]);

  return { ...state, refetch: fetchData };
}

/**
 * useHashRoute — syncs state with window.location.hash.
 * Returns [currentRoute, setRoute].
 */
export function useHashRoute(defaultRoute = '') {
  const getHash = () => window.location.hash.slice(1) || defaultRoute;
  const [route, setRouteState] = useState(getHash);

  useEffect(() => {
    const onHashChange = () => setRouteState(getHash());
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, [defaultRoute]);

  const setRoute = useCallback((newRoute) => {
    window.location.hash = newRoute;
  }, []);

  return [route, setRoute];
}

/**
 * useUndoToast — shows a toast with undo capability.
 * Returns { show, ToastComponent }.
 */
export function useUndoToast(timeout = 5000) {
  const [toast, setToast] = useState(null);
  const timerRef = useRef(null);

  const show = useCallback((message, onUndo) => {
    clearTimeout(timerRef.current);
    setToast({ message, onUndo });
    timerRef.current = setTimeout(() => setToast(null), timeout);
  }, [timeout]);

  const dismiss = useCallback(() => {
    clearTimeout(timerRef.current);
    setToast(null);
  }, []);

  const handleUndo = useCallback(() => {
    if (toast?.onUndo) toast.onUndo();
    dismiss();
  }, [toast, dismiss]);

  useEffect(() => () => clearTimeout(timerRef.current), []);

  return { toast, show, dismiss, handleUndo };
}
