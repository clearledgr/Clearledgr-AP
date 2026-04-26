import { createContext, h } from 'preact';
import { useCallback, useContext, useEffect, useRef, useState } from 'preact/hooks';
import { html } from '../utils/htm.js';

const ToastContext = createContext(() => {});

let nextId = 1;

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const timersRef = useRef(new Map());

  const dismiss = useCallback((id) => {
    setToasts((list) => list.filter((t) => t.id !== id));
    const handle = timersRef.current.get(id);
    if (handle) {
      clearTimeout(handle);
      timersRef.current.delete(id);
    }
  }, []);

  const toast = useCallback((message, opts = {}) => {
    const id = nextId++;
    const variant = opts.variant || 'info';
    const ttl = opts.ttl ?? 4500;
    setToasts((list) => [...list, { id, message: String(message || ''), variant }]);
    if (ttl > 0) {
      const handle = setTimeout(() => dismiss(id), ttl);
      timersRef.current.set(id, handle);
    }
    return id;
  }, [dismiss]);

  useEffect(() => () => {
    for (const handle of timersRef.current.values()) clearTimeout(handle);
    timersRef.current.clear();
  }, []);

  return html`
    <${ToastContext.Provider} value=${toast}>
      ${children}
      <div class="cl-toast-rail" aria-live="polite">
        ${toasts.map((t) => html`
          <div class=${`cl-toast cl-toast-${t.variant}`} key=${t.id}>
            <span class="cl-toast-msg">${t.message}</span>
            <button class="cl-toast-close" onClick=${() => dismiss(t.id)} aria-label="Dismiss">×</button>
          </div>
        `)}
      </div>
    <//>
  `;
}

export function useToast() {
  return useContext(ToastContext);
}
