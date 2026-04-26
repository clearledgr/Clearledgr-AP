import { html } from '../utils/htm.js';

/**
 * Stub used for routes that haven't been lifted from the Gmail
 * extension yet. Each placeholder is replaced as workstream A.3 / A.4
 * progresses; Vendors / Reconciliation / Connections / Settings /
 * Activity all land here first so URLs are stable while the lifts
 * happen one page at a time.
 */
export function PlaceholderPage({ title, lift = '' }) {
  return html`
    <div class="cl-page">
      <header class="cl-page-header">
        <h1>${title}</h1>
        <p class="cl-page-sub">Migrating from the Gmail extension. Live at GA.</p>
      </header>
      ${lift
        ? html`<div class="cl-page-meta">Source: <code>${lift}</code></div>`
        : null}
    </div>
  `;
}
