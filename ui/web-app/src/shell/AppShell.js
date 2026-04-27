import { useEffect } from 'preact/hooks';
import { useLocation } from 'wouter-preact';
import { html } from '../utils/htm.js';
import { SidebarNav } from './SidebarNav.js';
import { Topbar } from './Topbar.js';
import { ErrorBoundary } from './ErrorBoundary.js';
import { AppFooter } from './AppFooter.js';
import { CommandK } from './CommandK.js';
import { MobileShellProvider, useMobileShell } from './MobileShellContext.js';

function AppShellInner({ children }) {
  const [location] = useLocation();
  const { open, close } = useMobileShell();

  // Close the drawer on route change so users navigating via the
  // sidebar nav don't end up with a leftover open drawer covering
  // the page they just landed on.
  useEffect(() => {
    if (open) close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location]);

  return html`
    <div class="cl-app">
      <aside class=${`cl-app-sidebar${open ? ' cl-app-sidebar-open' : ''}`}>
        <${SidebarNav} />
      </aside>
      ${open
        ? html`<div class="cl-sidebar-backdrop" onClick=${close} aria-hidden="true"></div>`
        : null}
      <div class="cl-app-main">
        <${Topbar} />
        <main class="cl-app-content">
          <${ErrorBoundary}>${children}<//>
        </main>
        <${AppFooter} />
      </div>
      <${CommandK} />
    </div>
  `;
}

export function AppShell({ children }) {
  return html`
    <${MobileShellProvider}>
      <${AppShellInner}>${children}<//>
    <//>
  `;
}
