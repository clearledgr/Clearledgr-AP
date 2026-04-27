import { useEffect, useRef, useState } from 'preact/hooks';
import { useLocation } from 'wouter-preact';
import { html } from '../utils/htm.js';
import { useSession, logout } from '../auth/useSession.js';
import { useBootstrap } from './BootstrapContext.js';
import { EntitySwitcher } from './EntitySwitcher.js';
import { useMobileShell } from './MobileShellContext.js';

/**
 * Topbar — org context (left) + user menu (right).
 *
 * Org context (workstream D scope, single-tenant-per-user model):
 *   - Renders the active org name and the current user's role pill.
 *   - The chevron affordance is intentional: it signals where a future
 *     multi-org switcher will live, but does nothing today because
 *     users belong to exactly one org. When `user_organizations`
 *     ships, swap the chevron for a click-target dropdown without
 *     touching the Topbar's layout.
 *
 * User menu:
 *   - Email row (read-only)
 *   - "Onboarding" link when bootstrap.onboarding.completed === false
 *   - Sign out
 */
const ROLE_LABELS = {
  owner: 'Owner',
  admin: 'Admin',
  ap_manager: 'AP manager',
  ap_clerk: 'AP clerk',
  approver: 'Approver',
  viewer: 'Viewer',
  user: 'Member',
};

export function Topbar() {
  const { session } = useSession();
  const bootstrap = useBootstrap();
  const [, navigate] = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef(null);
  const { toggle: toggleSidebar } = useMobileShell();

  const email = bootstrap?.current_user?.email || session?.email || '';
  const name = bootstrap?.current_user?.name || session?.name || email;
  const orgName =
    bootstrap?.organization?.name ||
    bootstrap?.organization?.id ||
    session?.organization_id ||
    'default';
  const rawRole = String(
    bootstrap?.current_user?.role || session?.role || ''
  ).trim().toLowerCase();
  const roleLabel = ROLE_LABELS[rawRole] || (rawRole ? rawRole.replace(/_/g, ' ') : '');
  const onboardingPending =
    bootstrap?.onboarding && bootstrap.onboarding.completed === false;

  // Click-outside to close the menu — keeps the dropdown from sticking
  // open when the user navigates somewhere else via the sidebar.
  useEffect(() => {
    if (!menuOpen) return;
    function onDocClick(event) {
      if (menuRef.current && !menuRef.current.contains(event.target)) {
        setMenuOpen(false);
      }
    }
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, [menuOpen]);

  return html`
    <header class="cl-topbar">
      <button
        class="cl-topbar-hamburger"
        type="button"
        aria-label="Open navigation"
        onClick=${toggleSidebar}>
        ☰
      </button>
      <div class="cl-topbar-left">
        <div
          class="cl-topbar-org"
          title="Active workspace — multi-org switcher coming soon"
          aria-label=${`Active workspace: ${orgName}`}>
          <div class="cl-topbar-org-stack">
            <span class="cl-topbar-org-label">Workspace</span>
            <span class="cl-topbar-org-name">${orgName}</span>
          </div>
          ${roleLabel
            ? html`<span class=${`cl-topbar-role cl-topbar-role-${rawRole}`}>${roleLabel}</span>`
            : null}
          <span class="cl-topbar-org-chevron" aria-hidden="true">▾</span>
        </div>
        <${EntitySwitcher} />
      </div>

      <button
        class="cl-topbar-cmdk-hint"
        title="Open command palette"
        onClick=${() => {
          const isMac = typeof navigator !== 'undefined' && navigator.platform?.toLowerCase().includes('mac');
          const event = new KeyboardEvent('keydown', {
            key: 'k', code: 'KeyK', bubbles: true,
            metaKey: isMac, ctrlKey: !isMac,
          });
          window.dispatchEvent(event);
        }}>
        <span class="cl-topbar-cmdk-icon">⌘</span>
        <span>Search…</span>
        <span class="cl-topbar-cmdk-kbd">K</span>
      </button>
      <div class="cl-topbar-actions" ref=${menuRef}>
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
                ${onboardingPending
                  ? html`
                      <button
                        class="cl-topbar-menu-item cl-topbar-menu-onboarding"
                        onClick=${() => {
                          setMenuOpen(false);
                          navigate('/onboarding');
                        }}>
                        Resume onboarding
                      </button>
                    `
                  : null}
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
