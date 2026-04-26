import { useEffect, useState } from 'preact/hooks';
import { html } from '../utils/htm.js';
import { api } from '../api/client.js';

/**
 * Placeholder Pipeline page wired to the same `/api/ap/items/upcoming`
 * endpoint the Gmail extension uses, so we have one round-trip proven
 * before we lift the full PipelinePage from
 * `ui/gmail-extension/src/routes/pages/PipelinePage.js`.
 *
 * The full lift happens in workstream A.3. This stub is the
 * skeleton's smoke test — it confirms auth + fetch + render path
 * before any real pageage is moved over.
 */
export function PipelinePage() {
  const [state, setState] = useState({ status: 'loading', data: null, error: null });

  useEffect(() => {
    let cancelled = false;
    api('/api/ap/items/upcoming?organization_id=default&limit=25')
      .then((data) => {
        if (cancelled) return;
        setState({ status: 'ready', data, error: null });
      })
      .catch((err) => {
        if (cancelled) return;
        setState({ status: 'error', data: null, error: err.message || String(err) });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return html`
    <div class="cl-page">
      <header class="cl-page-header">
        <h1>Pipeline</h1>
        <p class="cl-page-sub">Upcoming AP work, all surfaces feeding the same Box.</p>
      </header>
      ${state.status === 'loading' ? html`<div class="cl-page-loading">Loading…</div>` : null}
      ${state.status === 'error'
        ? html`<div class="cl-page-error">Could not load pipeline: ${state.error}</div>`
        : null}
      ${state.status === 'ready'
        ? html`
            <pre class="cl-page-preview">${JSON.stringify(state.data, null, 2)}</pre>
          `
        : null}
    </div>
  `;
}
