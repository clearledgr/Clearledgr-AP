import { Link, useLocation } from 'wouter-preact';
import { html } from '../utils/htm.js';
import { BrandMark } from './BrandMark.js';

/**
 * Sidebar nav for the coordination-layer control center.
 *
 * Four groups, ordered by what the operator does here:
 *   primary   — live state of the coordination layer (Home, Activity, Exceptions)
 *   workflows — the box types the operator works in (Records, Procurement, Builder)
 *   data      — reference + read-only surfaces (Vendors, Reports, Audit log)
 *   admin     — policy + identity + render-target config (Rules, Connections, API keys, Settings)
 *
 * Records / Procurement / Builder are the workflow surfaces (box types the
 * runtime runs), so they group under WORKFLOWS, not DATA. DATA is for the
 * reference surfaces: the vendor directory, reporting, and the audit log.
 * (The /workflows page is the no-code builder, labeled "Builder" here so the
 * group heading and the item don't read as "Workflows > Workflows".)
 */
export const NAV_ITEMS = [
  { path: '/', label: 'Home', group: 'primary' },
  { path: '/activity', label: 'Activity', group: 'primary' },
  { path: '/exceptions', label: 'Exceptions', group: 'primary' },
  { path: '/records', label: 'Records', group: 'workflows' },
  { path: '/procurement', label: 'Procurement', group: 'workflows' },
  { path: '/workflows', label: 'Builder', group: 'workflows' },
  { path: '/vendors', label: 'Vendors', group: 'data' },
  { path: '/reports', label: 'Reports', group: 'data' },
  { path: '/audit', label: 'Audit log', group: 'data' },
  { path: '/rules', label: 'Approval rules', group: 'admin' },
  { path: '/connections', label: 'Connections', group: 'admin' },
  { path: '/api-keys', label: 'API keys', group: 'admin' },
  { path: '/settings', label: 'Settings', group: 'admin' },
];

const GROUP_LABELS = {
  primary: '',
  workflows: 'WORKFLOWS',
  data: 'DATA',
  admin: 'ADMIN',
};

export function SidebarNav() {
  const [pathname] = useLocation();

  const groups = ['primary', 'workflows', 'data', 'admin'];

  return html`
    <nav class="cl-sidebar-nav" aria-label="Primary">
      <div class="cl-sidebar-brand">
        <${BrandMark} height=${32} tone="primary" />
      </div>
      ${groups.map(
        (group) => html`
          <div class="cl-sidebar-group" key=${group}>
            ${GROUP_LABELS[group]
              ? html`<div class="cl-sidebar-group-label">${GROUP_LABELS[group]}</div>`
              : null}
            <ul class="cl-sidebar-list">
              ${NAV_ITEMS.filter((i) => i.group === group).map((item) => {
                const active =
                  item.path === '/'
                    ? pathname === '/'
                    : pathname === item.path || pathname.startsWith(`${item.path}/`);
                return html`
                  <li key=${item.path}>
                    <${Link} href=${item.path}
                      class=${`cl-sidebar-link ${active ? 'is-active' : ''}`}>
                      ${item.label}
                    <//>
                  </li>
                `;
              })}
            </ul>
          </div>
        `
      )}
    </nav>
  `;
}
