import { useState } from 'preact/hooks';
import { html } from '../utils/htm.js';
import { useSession, logout } from '../auth/useSession.js';

export function Topbar() {
  const { session } = useSession();
  const [menuOpen, setMenuOpen] = useState(false);

  const email = session?.email || '';
  const name = session?.name || email;
  const orgName = session?.organization_name || session?.organization_id || 'default';

  return html`
    <header class="cl-topbar">
      <div class="cl-topbar-org">
        <span class="cl-topbar-org-label">Org</span>
        <span class="cl-topbar-org-name">${orgName}</span>
      </div>
      <div class="cl-topbar-actions">
        <button
          class="cl-topbar-user"
          onClick=${() => setMenuOpen((v) => !v)}
          aria-haspopup="menu"
          aria-expanded=${menuOpen}>
          <span class="cl-topbar-avatar">${(name[0] || '?').toUpperCase()}</span>
          <span class="cl-topbar-name">${name}</span>
        </button>
        ${menuOpen
          ? html`
              <div class="cl-topbar-menu" role="menu">
                <div class="cl-topbar-menu-row">${email}</div>
                <button
                  class="cl-topbar-menu-item"
                  onClick=${() => {
                    setMenuOpen(false);
                    logout().then(() => {
                      window.location.href = '/login';
                    });
                  }}>
                  Sign out
                </button>
              </div>
            `
          : null}
      </div>
    </header>
  `;
}
