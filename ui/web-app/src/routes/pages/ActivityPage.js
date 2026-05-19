/**
 * Activity page.
 *
 * The dedicated /activity route. Renders the same agent activity
 * ribbon shown on workspace Home, but taller (last ~50 events) and
 * with the ribbon as the page itself rather than one panel of many.
 *
 * Replaces the earlier secondary-banner + stat-card + audit-card
 * design. Stat tiles (pending approval, posted today, etc.) already
 * live on Home; this page is purely "what just happened".
 */
import { useEffect, useState } from 'preact/hooks';
import { html } from '../../utils/htm.js';
import { useAction } from '../route-helpers.js';
import { AgentActivityRibbon } from '../../components/AgentActivityRibbon.js';

const ACTIVITY_LIMIT = 50;

export default function ActivityPage({ api, orgId, onRefresh, navigate }) {
  const [state, setState] = useState({ status: 'loading', data: null, error: null });
  const [liveActivity, setLiveActivity] = useState(null);

  // Initial fetch from the same endpoint Home uses, with a wider limit.
  useEffect(() => {
    let cancelled = false;
    setState({ status: 'loading', data: null, error: null });
    api(`/api/workspace/dashboard/recent-activity?limit=${ACTIVITY_LIMIT}`)
      .then((data) => { if (!cancelled) setState({ status: 'ready', data, error: null }); })
      .catch((err) => { if (!cancelled) setState({ status: 'error', data: null, error: err?.message || 'load_failed' }); });
    return () => { cancelled = true; };
  }, [api, orgId, onRefresh]);

  // SSE for live frames. Same stream Home subscribes to; we only
  // care about the 'activity' frame type here.
  useEffect(() => {
    if (typeof EventSource === 'undefined') return undefined;
    const source = new EventSource('/api/workspace/dashboard/stream', { withCredentials: true });
    source.onmessage = (event) => {
      try {
        const frame = JSON.parse(event.data);
        if (frame?.type === 'activity' && frame.data) setLiveActivity(frame.data);
      } catch { /* ignore bad frame */ }
    };
    source.onerror = () => { if (source.readyState === 2) source.close(); };
    return () => source.close();
  }, [orgId]);

  const items = (liveActivity?.items)
    || (Array.isArray(state.data?.items) ? state.data.items : []);

  const [refresh, refreshing] = useAction(onRefresh);

  return html`
    <div class="cl-activity-page">
      <header class="cl-activity-page-head">
        <div>
          <h1 class="cl-activity-page-title">Activity</h1>
          <p class="cl-activity-page-sub">Every agent and operator action across your AP records.</p>
        </div>
        <div class="cl-activity-page-actions">
          <button class="btn-secondary btn-sm" onClick=${refresh} disabled=${refreshing}>
            ${refreshing ? 'Refreshing…' : 'Refresh'}
          </button>
          <button class="btn-primary btn-sm" onClick=${() => navigate?.('/records')}>
            Open records
          </button>
        </div>
      </header>

      <${AgentActivityRibbon}
        state=${state}
        items=${items}
        live=${!!liveActivity}
        navigate=${navigate}
        emptyTitle="Nothing has happened yet."
        emptyDescription="As Solden ingests invoices and your team makes decisions, every action lands here."
      />
    </div>
  `;
}
