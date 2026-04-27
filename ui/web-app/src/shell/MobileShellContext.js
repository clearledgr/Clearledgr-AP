import { createContext } from 'preact';
import { useCallback, useContext, useEffect, useState } from 'preact/hooks';
import { html } from '../utils/htm.js';

/**
 * MobileShellContext — drives the sidebar drawer on small screens.
 *
 * Desktop (>760px): sidebar is always rendered statically; `open` is
 * ignored by CSS. Mobile (<=760px): sidebar slides in/out under
 * `cl-app-sidebar-open`, and a backdrop appears.
 *
 * The close-on-route-change behavior is wired in AppShell via
 * window location updates so we don't have to thread router state
 * through here.
 */
const Context = createContext({ open: false, toggle: () => {}, close: () => {} });

export function MobileShellProvider({ children }) {
  const [open, setOpen] = useState(false);

  const toggle = useCallback(() => setOpen((v) => !v), []);
  const close = useCallback(() => setOpen(false), []);

  // Auto-close on resize past the breakpoint so the drawer state
  // doesn't get stuck "open" when rotating to landscape on a tablet.
  useEffect(() => {
    function onResize() {
      if (window.innerWidth > 760 && open) close();
    }
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [open, close]);

  // Esc closes the drawer.
  useEffect(() => {
    if (!open) return;
    function onKey(event) {
      if (event.key === 'Escape') close();
    }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, close]);

  return html`
    <${Context.Provider} value=${{ open, toggle, close }}>${children}<//>
  `;
}

export function useMobileShell() {
  return useContext(Context);
}
