import { Link, useLocation } from 'wouter-preact';
import { html } from '../utils/htm.js';

/**
 * Hub navigation. The route IDs match the existing Gmail-extension
 * route registry (`ui/gmail-extension/src/routes/route-registry.js`)
 * so deep links from the sidebar/banners can navigate here without
 * a translation table.
 */
export const NAV_ITEMS = [
  { path: '/', label: 'Home', group: 'primary' },
  { path: '/pipeline', label: 'Pipeline', group: 'work' },
  { path: '/review', label: 'Review queue', group: 'work' },
  { path: '/exceptions', label: 'Exceptions', group: 'work' },
  { path: '/vendors', label: 'Vendors', group: 'work' },
  { path: '/reconciliation', label: 'Reconciliation', group: 'work' },
  { path: '/activity', label: 'Activity', group: 'ops' },
  { path: '/audit', label: 'Audit log', group: 'ops' },
  { path: '/connections', label: 'Connections', group: 'ops' },
  { path: '/settings', label: 'Settings', group: 'ops' },
];

const GROUP_LABELS = {
  primary: '',
  work: 'WORK',
  ops: 'OPERATE',
};

export function SidebarNav() {
  const [pathname] = useLocation();

  const groups = ['primary', 'work', 'ops'];

  return html`
    <nav class="cl-sidebar-nav" aria-label="Primary">
      <div class="cl-sidebar-brand">
        <span class="cl-sidebar-mark">●</span>
        <span class="cl-sidebar-wordmark">Clearledgr</span>
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
