import { Link, useLocation } from 'wouter-preact';
import { html } from '../utils/htm.js';
import { BrandMark } from './BrandMark.js';

/**
 * Sidebar nav for the coordination-layer control center.
 *
 * Three groups, ordered by what the operator does here:
 *   primary  — live state of the coordination layer (Home, Activity, Exceptions)
 *   data     — directories and read-only surfaces (Records, Vendors, Reports, Audit log)
 *   admin    — policy + identity + render-target config (Rules, Connections, API keys, Settings)
 *
 * The earlier "WORK" group (Records / Review queue / Exceptions / Vendors)
 * mirrored Streak/BILL grammar — a workflow desktop. The workspace
 * is not a workflow desktop. Approvals + vendor follow-up happen in
 * Slack/Teams/Gmail; the workspace shows live state, lets the
 * operator intervene when the agent escalates, and configures policy.
 */
export const NAV_ITEMS = [
  { path: '/', label: 'Home', group: 'primary' },
  { path: '/activity', label: 'Activity', group: 'primary' },
  { path: '/exceptions', label: 'Exceptions', group: 'primary' },
  { path: '/records', label: 'Records', group: 'data' },
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
  data: 'DATA',
  admin: 'ADMIN',
};

export function SidebarNav() {
  const [pathname] = useLocation();

  const groups = ['primary', 'data', 'admin'];

  return html`
    <nav class="cl-sidebar-nav" aria-label="Primary">
      <div class="cl-sidebar-brand">
        <${BrandMark} height=${32} tone="on-dark" />
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
