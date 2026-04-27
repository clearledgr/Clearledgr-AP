import { html } from '../utils/htm.js';
import { SidebarNav } from './SidebarNav.js';
import { Topbar } from './Topbar.js';
import { ErrorBoundary } from './ErrorBoundary.js';
import { AppFooter } from './AppFooter.js';
import { CommandK } from './CommandK.js';

export function AppShell({ children }) {
  return html`
    <div class="cl-app">
      <aside class="cl-app-sidebar">
        <${SidebarNav} />
      </aside>
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
