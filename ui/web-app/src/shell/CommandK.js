import { useCallback, useEffect, useMemo, useRef, useState } from 'preact/hooks';
import { useLocation } from 'wouter-preact';
import { html } from '../utils/htm.js';
import { api } from '../api/client.js';
import { useOrgId } from './BootstrapContext.js';

/**
 * Command palette (⌘K / Ctrl+K) — table-stakes for any modern admin
 * SPA. Combines navigation with live AP/vendor search:
 *
 *   - Static nav entries (Pipeline, Vendors, Onboarding, etc.) that
 *     fuzzy-match against the user's query
 *   - Live results from the existing /api/ap/items/search and
 *     /api/vendors/summary endpoints (debounced 200ms)
 *   - ↑/↓ to traverse, Enter to activate, Esc to close
 *
 * Mounted from AppShell so it's available on every authenticated
 * page. Keyboard listener registers globally; opening/closing is
 * idempotent.
 */

const NAV_ENTRIES = [
  { kind: 'nav', label: 'Home', sub: 'Workspace overview', path: '/', tokens: ['home', 'overview', 'dashboard'] },
  { kind: 'nav', label: 'Pipeline', sub: 'AP items by stage', path: '/pipeline', tokens: ['pipeline', 'invoices', 'ap'] },
  { kind: 'nav', label: 'Review queue', sub: 'Items needing attention', path: '/review', tokens: ['review', 'queue'] },
  { kind: 'nav', label: 'Exceptions', sub: 'Items with blockers', path: '/exceptions', tokens: ['exceptions', 'errors', 'blockers'] },
  { kind: 'nav', label: 'Vendors', sub: 'Vendor directory', path: '/vendors', tokens: ['vendors', 'suppliers'] },
  { kind: 'nav', label: 'Reconciliation', sub: 'Match bank ↔ ERP', path: '/reconciliation', tokens: ['reconciliation', 'recon', 'match'] },
  { kind: 'nav', label: 'Activity', sub: 'Audit trail', path: '/activity', tokens: ['activity', 'audit', 'history'] },
  { kind: 'nav', label: 'Connections', sub: 'ERP, Slack, Teams, Gmail', path: '/connections', tokens: ['connections', 'integrations', 'erp', 'slack', 'teams', 'gmail'] },
  { kind: 'nav', label: 'Settings', sub: 'Org, users, policies, billing', path: '/settings', tokens: ['settings', 'config', 'preferences', 'billing'] },
  { kind: 'nav', label: 'Templates', sub: 'Reply + email templates', path: '/templates', tokens: ['templates'] },
  { kind: 'nav', label: 'Status', sub: 'Operational health', path: '/status', tokens: ['status', 'health', 'uptime', 'incidents'] },
  { kind: 'nav', label: 'Onboarding', sub: 'Setup wizard', path: '/onboarding', tokens: ['onboarding', 'setup', 'wizard'] },
];

function fuzzyScore(query, item) {
  if (!query) return 1;
  const q = query.toLowerCase();
  const haystacks = [item.label, item.sub, ...(item.tokens || [])].filter(Boolean).map((s) => s.toLowerCase());
  for (const h of haystacks) {
    if (h.startsWith(q)) return 3;
    if (h.includes(q)) return 2;
  }
  // Subsequence match: characters appear in order
  for (const h of haystacks) {
    let i = 0;
    for (const ch of h) {
      if (q[i] === ch) i++;
      if (i >= q.length) return 1;
    }
  }
  return 0;
}

export function CommandK() {
  const [, navigate] = useLocation();
  const orgId = useOrgId();

  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [activeIdx, setActiveIdx] = useState(0);
  const [liveResults, setLiveResults] = useState([]);
  const inputRef = useRef(null);

  // Global keybind. ⌘K on macOS, Ctrl+K elsewhere. Esc closes.
  useEffect(() => {
    function onKey(e) {
      const isMac = navigator.platform.toLowerCase().includes('mac');
      const cmdKey = isMac ? e.metaKey : e.ctrlKey;
      if (cmdKey && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault();
        setOpen((v) => !v);
      } else if (e.key === 'Escape' && open) {
        setOpen(false);
      }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);

  useEffect(() => {
    if (open) {
      setQuery('');
      setActiveIdx(0);
      setTimeout(() => inputRef.current?.focus?.(), 0);
    }
  }, [open]);

  // Debounced live search against /api/ap/items/search.
  useEffect(() => {
    if (!open || !query || query.length < 2) {
      setLiveResults([]);
      return undefined;
    }
    const handle = setTimeout(async () => {
      try {
        const data = await api(
          `/api/ap/items/search?organization_id=${encodeURIComponent(orgId)}&q=${encodeURIComponent(query)}&limit=5`,
          { retry: false }
        );
        const items = (data?.items || []).map((item) => ({
          kind: 'item',
          label: item.vendor_name || item.vendor || 'Unknown vendor',
          sub: [item.invoice_number ? `#${item.invoice_number}` : '', item.state ? item.state.replace(/_/g, ' ') : '']
            .filter(Boolean).join(' · '),
          path: `/items/${encodeURIComponent(item.id)}`,
        }));
        setLiveResults(items);
      } catch {
        setLiveResults([]);
      }
    }, 200);
    return () => clearTimeout(handle);
  }, [query, open, orgId]);

  const ranked = useMemo(() => {
    const all = [
      ...NAV_ENTRIES.map((entry) => ({ ...entry, score: fuzzyScore(query, entry) })),
      ...liveResults.map((entry) => ({ ...entry, score: 1.5 })),
    ];
    return all
      .filter((entry) => entry.score > 0)
      .sort((a, b) => b.score - a.score)
      .slice(0, 8);
  }, [query, liveResults]);

  useEffect(() => {
    if (activeIdx >= ranked.length) setActiveIdx(0);
  }, [ranked.length, activeIdx]);

  const activate = useCallback((entry) => {
    if (!entry?.path) return;
    setOpen(false);
    navigate(entry.path);
  }, [navigate]);

  const onInputKey = (e) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActiveIdx((idx) => Math.min(idx + 1, ranked.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActiveIdx((idx) => Math.max(idx - 1, 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      activate(ranked[activeIdx]);
    }
  };

  if (!open) return null;

  return html`
    <div class="cl-cmdk-backdrop" onClick=${() => setOpen(false)}>
      <div class="cl-cmdk" onClick=${(e) => e.stopPropagation()}>
        <input
          ref=${inputRef}
          class="cl-cmdk-input"
          type="text"
          placeholder="Search vendors, invoices, or jump to a page…"
          value=${query}
          onInput=${(e) => setQuery(e.currentTarget.value)}
          onKeyDown=${onInputKey}
        />
        <div class="cl-cmdk-results">
          ${ranked.length === 0
            ? html`<div class="cl-cmdk-empty">No matches.</div>`
            : ranked.map((entry, idx) => html`
                <button
                  class=${`cl-cmdk-row ${idx === activeIdx ? 'is-active' : ''}`}
                  key=${`${entry.kind}-${entry.path}-${idx}`}
                  onMouseEnter=${() => setActiveIdx(idx)}
                  onClick=${() => activate(entry)}>
                  <span class=${`cl-cmdk-kind cl-cmdk-kind-${entry.kind}`}>
                    ${entry.kind === 'nav' ? 'Go to' : 'Open'}
                  </span>
                  <span class="cl-cmdk-row-text">
                    <span class="cl-cmdk-row-label">${entry.label}</span>
                    ${entry.sub ? html`<span class="cl-cmdk-row-sub">${entry.sub}</span>` : null}
                  </span>
                </button>
              `)}
        </div>
        <footer class="cl-cmdk-footer">
          <span><kbd>↑</kbd><kbd>↓</kbd> to navigate</span>
          <span><kbd>↵</kbd> to open</span>
          <span><kbd>Esc</kbd> to close</span>
        </footer>
      </div>
    </div>
  `;
}
