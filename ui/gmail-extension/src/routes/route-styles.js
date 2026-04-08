/**
 * Shared CSS for InboxSDK route pages.
 * Extracted from static/workspace/styles.css — component styles only.
 * Layout-specific CSS (shell grid, sidebar nav) is dropped.
 * Everything scoped under .cl-route to avoid Gmail CSS collisions.
 */

export const ROUTE_CSS = `
  .cl-route {
    --bg: #FAFAF8;
    --surface: #FFFFFF;
    --ink: #0F172A;
    --ink-secondary: #475569;
    --ink-muted: #94A3B8;
    --border: #E2E8F0;
    --border-hover: #CBD5E1;
    --accent: #00D67E;
    --accent-hover: #00BC6E;
    --accent-soft: #ECFDF5;
    --brand-muted: #10B981;
    --navy: #0A1628;
    --navy-light: #1E293B;
    --green: #16A34A;
    --green-soft: #F0FDF4;
    --green-text: #16A34A;
    --amber: #CA8A04;
    --amber-soft: #FEFCE8;
    --red: #DC2626;
    --red-soft: #FEF2F2;
    --info: #2563EB;
    --info-soft: #EFF6FF;
    --radius-sm: 6px;
    --radius-md: 8px;
    --radius-lg: 12px;
    --shadow-sm: 0 1px 2px rgba(0,0,0,0.04);
    --shadow-md: 0 2px 8px rgba(0,0,0,0.06);
    --font: 'DM Sans', -apple-system, system-ui, sans-serif;
    --font-display: 'Instrument Sans', -apple-system, system-ui, sans-serif;
    --font-mono: 'Geist Mono', 'SF Mono', monospace;
    --transition: 0.15s ease;

    box-sizing: border-box;
    font-family: var(--font);
    font-size: 13px;
    line-height: 1.5;
    color: var(--ink);
    padding: 14px 20px 28px;
    width: 100%;
    max-width: none;
    min-width: 0;
    min-height: 100%;
    -webkit-font-smoothing: antialiased;
  }

  .cl-route *, .cl-route *::before, .cl-route *::after { box-sizing: border-box; }

  /* Topbar */
  .cl-route .topbar { margin-bottom: 14px; }
  .cl-route .topbar h2 { margin: 0; font-family: var(--font-display); font-size: 23px; font-weight: 700; letter-spacing: -0.02em; }
  .cl-route .topbar p { margin: 3px 0 0; color: var(--ink-secondary); font-size: 13px; max-width: 880px; }

  /* Form elements */
  .cl-route label { font-size: 13px; font-weight: 500; color: var(--ink-secondary); margin-bottom: -8px; }
  .cl-route input, .cl-route textarea, .cl-route select {
    width: 100%; border: 1px solid var(--border); background: var(--surface);
    border-radius: var(--radius-sm); padding: 10px 14px; font: inherit; font-size: 14px;
    color: var(--ink); transition: border-color var(--transition), box-shadow var(--transition);
  }
  .cl-route input:focus, .cl-route textarea:focus, .cl-route select:focus {
    outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft);
  }
  .cl-route input::placeholder { color: var(--ink-muted); }
  .cl-route input[type="checkbox"],
  .cl-route input[type="radio"] {
    width: 16px;
    min-width: 16px;
    height: 16px;
    padding: 0;
    margin: 0;
    border: 1px solid var(--border-hover);
    border-radius: 4px;
    background: var(--surface);
    box-shadow: none;
    accent-color: var(--accent);
    flex: 0 0 auto;
  }
  .cl-route input[type="radio"] {
    border-radius: 999px;
  }
  .cl-route input[type="checkbox"]:focus,
  .cl-route input[type="radio"]:focus {
    box-shadow: 0 0 0 3px var(--accent-soft);
  }
  .cl-route textarea { min-height: 160px; resize: vertical; font-family: var(--font-mono); font-size: 13px; }

  /* Buttons */
  .cl-route button {
    display: inline-flex; align-items: center; justify-content: center; gap: 8px;
    border: 1px solid transparent; border-radius: var(--radius-sm); padding: 9px 16px;
    font: inherit; font-size: 14px; font-weight: 600; cursor: pointer;
    background: var(--accent); color: var(--navy, #0A1628);
    transition: background var(--transition), transform var(--transition), box-shadow var(--transition);
  }
  .cl-route button:hover { background: var(--accent-hover); transform: none; box-shadow: none; }
  .cl-route button:active { transform: translateY(0); }
  .cl-route button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .cl-route button:disabled { opacity: 0.4; cursor: not-allowed; transform: none; box-shadow: none; }
  .cl-route .btn-primary {
    background: var(--accent);
    color: var(--navy);
  }
  .cl-route button.alt {
    background: var(--surface); color: var(--ink); border: 1px solid var(--border);
  }
  .cl-route button.alt:hover { background: #f5f5f5; border-color: var(--border-hover); }
  .cl-route .btn-secondary {
    background: var(--surface);
    color: var(--ink);
    border-color: var(--border);
  }
  .cl-route .btn-secondary:hover {
    background: #F8FAFC;
    border-color: var(--border-hover);
  }
  .cl-route .btn-ghost {
    background: transparent;
    color: var(--ink-secondary);
    border-color: transparent;
    box-shadow: none;
  }
  .cl-route .btn-ghost:hover {
    background: #F8FAFC;
    color: var(--ink);
    box-shadow: none;
  }
  .cl-route .btn-danger {
    background: var(--red-soft);
    color: #B91C1C;
    border-color: #FECACA;
  }
  .cl-route .btn-danger:hover {
    background: #FEE2E2;
    color: #991B1B;
  }
  .cl-route .btn-sm {
    min-height: 34px;
    padding: 7px 12px;
    font-size: 12px;
    font-weight: 600;
  }
  .cl-route .btn-xs {
    min-height: 30px;
    padding: 6px 10px;
    font-size: 11px;
    font-weight: 600;
  }
  .cl-route .page-actions,
  .cl-route .inline-actions,
  .cl-route .row-actions,
  .cl-route .toolbar-actions {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
  .cl-route .row-actions {
    justify-content: flex-end;
  }
  .cl-route .panel-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 14px;
    flex-wrap: wrap;
    margin-bottom: 12px;
  }
  .cl-route .panel-head.compact {
    margin-bottom: 10px;
  }
  .cl-route .segmented-actions {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
  .cl-route .segmented-button {
    background: var(--surface);
    color: var(--ink-secondary);
    border-color: var(--border);
    box-shadow: none;
  }
  .cl-route .segmented-button:hover {
    background: #F8FAFC;
    border-color: var(--border-hover);
  }
  .cl-route .segmented-button.is-active {
    background: #F8FAFC;
    color: var(--navy);
    border-color: var(--border-hover);
    box-shadow: inset 0 0 0 1px rgba(15, 23, 42, 0.08);
  }
  .cl-route .segmented-button.is-active:hover {
    transform: none;
  }
  .cl-route .segmented-button:disabled {
    opacity: 1;
    cursor: default;
    box-shadow: none;
  }

  /* Panels */
  .cl-route .panel {
    background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-lg);
    padding: 16px; margin-bottom: 14px; box-shadow: none;
  }
  .cl-route .panel h3 { margin: 0 0 8px; font-family: var(--font-display); font-size: 17px; font-weight: 600; letter-spacing: -0.01em; }
  .cl-route .muted { font-size: 13px; color: var(--ink-secondary); line-height: 1.5; }

  /* KPI cards */
  .cl-route .kpi-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 10px; margin-bottom: 16px; }
  .cl-route .kpi-card {
    background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-md);
    padding: 14px 16px; box-shadow: none;
  }
  .cl-route .kpi-card strong { display: block; font-family: var(--font-mono); font-size: 22px; font-weight: 600; font-variant-numeric: tabular-nums; letter-spacing: -0.02em; line-height: 1.1; margin-bottom: 3px; }
  .cl-route .kpi-card span { font-size: 12px; color: var(--ink-muted); font-weight: 500; }
  .cl-route .kpi-warning strong { color: var(--amber); }
  .cl-route .kpi-success strong { color: var(--green); }
  .cl-route .kpi-danger strong { color: var(--red); }

  /* Status badges */
  .cl-route .status-badge {
    display: inline-flex; align-items: center; gap: 6px; font-size: 12px; font-weight: 600;
    border-radius: 999px; padding: 4px 12px; background: #f0f0ed; color: var(--ink-muted);
  }
  .cl-route .status-badge::before { content: ''; width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
  .cl-route .status-badge.connected { background: var(--green-soft); color: var(--green-text); }
  .cl-route .check-ok {
    display: inline-flex; align-items: center; gap: 4px; font-size: 12px; font-weight: 600;
    color: var(--green-text); background: var(--green-soft); padding: 3px 10px; border-radius: 999px;
  }
  .cl-route .check-ok::before { content: '\\2713'; font-weight: 700; }
  .cl-route .check-no {
    display: inline-flex; align-items: center; gap: 4px; font-size: 12px; font-weight: 600;
    color: var(--ink-muted); background: #f0f0ed; padding: 3px 10px; border-radius: 999px;
  }
  .cl-route .check-no::before { content: '\\25CB'; }

  /* Activity timeline */
  .cl-route .activity-timeline { display: flex; flex-direction: column; }
  .cl-route .activity-row { display: flex; align-items: center; gap: 14px; padding: 12px 0; border-bottom: 1px solid var(--border); }
  .cl-route .activity-row:last-child { border-bottom: none; }
  .cl-route .activity-time { width: 100px; font-size: 13px; color: var(--ink-muted); text-align: right; flex-shrink: 0; font-variant-numeric: tabular-nums; }
  .cl-route .activity-date { display: block; font-size: 11px; color: var(--ink-muted); opacity: 0.7; }
  .cl-route .activity-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; background: var(--border); }
  .cl-route .activity-dot.ev-posted, .cl-route .activity-dot.ev-approved { background: var(--green); }
  .cl-route .activity-dot.ev-pending { background: var(--amber); }
  .cl-route .activity-dot.ev-rejected, .cl-route .activity-dot.ev-error { background: var(--red); }
  .cl-route .activity-body { display: flex; align-items: center; gap: 12px; flex: 1; min-width: 0; }
  .cl-route .activity-vendor { font-weight: 600; font-size: 14px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 220px; }
  .cl-route .activity-amount { font-size: 14px; font-weight: 600; font-variant-numeric: tabular-nums; white-space: nowrap; }
  .cl-route .activity-badge {
    font-size: 11px; font-weight: 600; padding: 3px 10px; border-radius: 999px;
    white-space: nowrap; text-transform: uppercase; letter-spacing: 0.02em; background: #f0f0ed; color: var(--ink-muted);
  }
  .cl-route .activity-badge.ev-posted, .cl-route .activity-badge.ev-approved { background: var(--green-soft); color: var(--green-text); }
  .cl-route .activity-badge.ev-pending { background: var(--amber-soft); color: #92400e; }
  .cl-route .activity-badge.ev-rejected, .cl-route .activity-badge.ev-error { background: var(--red-soft); color: #991b1b; }

  /* Connector cards */
  .cl-route .connector-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 16px; margin-top: 16px; }
  .cl-route .connector-grid-3 { grid-template-columns: repeat(3, 1fr); }
  .cl-route .connector-card {
    border: 1px solid var(--border); border-radius: var(--radius-md); padding: 20px; background: var(--surface);
    transition: border-color var(--transition), box-shadow var(--transition);
  }
  .cl-route .connector-card:hover { border-color: var(--border-hover); box-shadow: var(--shadow-sm); }
  .cl-route .connector-card.done { border-color: #a7e3d0; background: #fafdfb; }
  .cl-route .connector-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }
  .cl-route .connector-header strong { font-size: 15px; font-weight: 600; }
  .cl-route .connector-detail { font-size: 12px; color: var(--green); margin: 8px 0 0; font-weight: 500; }
  .cl-route .connector-btn { margin-top: 12px; padding: 8px 16px; font-size: 13px; }

  /* Tables */
  .cl-route .table { width: 100%; border-collapse: collapse; }
  .cl-route .table th {
    text-align: left; padding: 10px 12px; font-family: var(--font-display); font-size: 11px; font-weight: 600;
    color: var(--ink-muted); text-transform: uppercase; letter-spacing: 0.04em; border-bottom: 2px solid var(--border);
  }
  .cl-route .table td { text-align: left; padding: 10px 12px; font-size: 13px; border-bottom: 1px solid var(--border); vertical-align: top; }
  .cl-route .table tr:hover td { background: var(--bg); }
  .cl-route .pipeline-table thead th {
    position: sticky;
    top: 0;
    z-index: 1;
    background: #FCFDFE;
  }
  .cl-route .pipeline-table-meta {
    background: #FCFDFE;
  }
  .cl-route .pipeline-filter-grid {
    width: 100%;
  }
  .cl-route .pipeline-shell {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .cl-route .pipeline-hero-panel,
  .cl-route .pipeline-view-panel,
  .cl-route .pipeline-filter-panel,
  .cl-route .pipeline-table-panel {
    margin-bottom: 0;
  }
  .cl-route .pipeline-hero-head,
  .cl-route .pipeline-view-head,
  .cl-route .pipeline-focus-row,
  .cl-route .pipeline-filter-footer,
  .cl-route .pipeline-table-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
  }
  .cl-route .pipeline-hero-copy {
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .cl-route .pipeline-metric-row {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }
  .cl-route .pipeline-focus-row,
  .cl-route .pipeline-saved-input-row,
  .cl-route .pipeline-filter-footer {
    margin-top: 10px;
    padding-top: 10px;
    border-top: 1px solid var(--border);
  }
  .cl-route .pipeline-focus-actions,
  .cl-route .pipeline-chip-strip,
  .cl-route .pipeline-saved-input-row,
  .cl-route .pipeline-filter-aux,
  .cl-route .pipeline-filter-actions,
  .cl-route .pipeline-table-actions {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    align-items: center;
  }
  .cl-route .pipeline-view-band {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    flex-wrap: wrap;
  }
  .cl-route .pipeline-view-label {
    padding-top: 8px;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--ink-muted);
  }
  .cl-route .pipeline-chip-strip {
    flex: 1 1 560px;
  }
  .cl-route .pipeline-record-cell strong {
    line-height: 1.25;
  }
  .cl-route .pipeline-record-cell .muted,
  .cl-route .pipeline-signals-cell .muted {
    line-height: 1.42;
  }

  /* Readiness */
  .cl-route .readiness-list { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; margin-bottom: 16px; }
  .cl-route .readiness-item { font-size: 14px; padding: 12px 14px; border-radius: var(--radius-sm); background: var(--bg); border: 1px solid var(--border); }
  .cl-route .launch-btn { width: 100%; padding: 16px; font-size: 16px; font-weight: 700; border-radius: var(--radius-md); }

  /* Utility */
  .cl-route .row { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; }
  .cl-route .mt-0 { margin-top: 0; }
  .cl-route .mt-10 { margin-top: 12px; }
  .cl-route .home-hero {
    display: block;
    margin: 0 0 14px;
  }
  .cl-route .home-hero-copy {
    max-width: none;
    text-align: left;
  }
  .cl-route .home-header-shell {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 14px;
    flex-wrap: wrap;
    margin-bottom: 14px;
  }
  .cl-route .home-header-copy {
    min-width: 0;
    flex: 1 1 420px;
  }
  .cl-route .home-header-copy h2 {
    margin: 0;
    font-size: 28px;
    line-height: 1.08;
    letter-spacing: -0.03em;
  }
  .cl-route .home-header-copy p {
    margin: 8px 0 0;
    max-width: 760px;
    color: var(--ink-secondary);
  }
  .cl-route .home-eyebrow {
    display: flex;
    align-items: center;
    gap: 8px;
    margin: 0 0 10px;
    color: var(--ink-muted);
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .cl-route .home-eyebrow::before {
    content: '';
    width: 10px;
    height: 10px;
    border-radius: 999px;
    border: 1.5px solid var(--border-hover);
    background: var(--surface);
  }
  .cl-route .home-status-row {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 10px;
    margin-top: 14px;
  }
  .cl-route .home-status-pill {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 2px;
    padding: 10px 12px;
    border-radius: 10px;
    border: 1px solid var(--border);
    background: var(--surface);
    font-size: 12px;
    color: var(--ink-secondary);
  }
  .cl-route .home-status-pill strong {
    font-family: var(--font-display);
    font-size: 18px;
    font-weight: 700;
    letter-spacing: -0.03em;
    color: var(--ink);
  }
  .cl-route .home-status-pill span {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: var(--ink-muted);
  }
  .cl-route .home-status-pill.success {
    border-color: #A7F3D0;
    background: #ECFDF5;
  }
  .cl-route .home-status-pill.success span { color: #166534; }
  .cl-route .home-status-pill.info {
    border-color: #BFDBFE;
    background: #EFF6FF;
  }
  .cl-route .home-status-pill.info span { color: #1D4ED8; }
  .cl-route .home-status-pill.warning {
    border-color: #FCD34D;
    background: #FFFBEB;
  }
  .cl-route .home-status-pill.warning span { color: #92400E; }
  .cl-route .home-utility-rail {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 8px;
    min-width: 0;
    flex: 0 1 auto;
  }
  .cl-route .home-utility-strip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 0;
    border: 0;
    border-radius: 999px;
    background: transparent;
  }
  .cl-route .home-utility-icon-button {
    min-width: 32px;
    width: 32px;
    height: 32px;
    padding: 0;
    border-radius: 999px;
    border-color: transparent;
    background: transparent;
    color: var(--ink-secondary);
    box-shadow: none;
  }
  .cl-route .home-utility-icon-button:hover {
    background: #F1F5F9;
    border-color: transparent;
  }
  .cl-route .home-utility-icon {
    width: 18px;
    height: 18px;
    background-position: center;
    background-repeat: no-repeat;
    background-size: 18px 18px;
    opacity: 0.88;
  }
  .cl-route .home-utility-primary {
    min-height: 36px;
    padding: 0 14px;
    border-radius: 999px;
    border-color: #B7E9D0;
    background: #DFF6EB;
    color: #0F5132;
    box-shadow: none;
  }
  .cl-route .home-utility-primary:hover {
    background: #D6F2E5;
    border-color: #A7DFC6;
  }
  .cl-route .home-banner {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 14px;
    flex-wrap: wrap;
    padding: 10px 12px;
    margin-bottom: 14px;
    border: 1px solid #C7D2FE;
    border-radius: 10px;
    background: #F8FAFF;
    box-shadow: none;
  }
  .cl-route .home-banner.warning {
    border-color: #FCD34D;
    background: #FFFBEB;
  }
  .cl-route .home-mini-chip-row {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    margin-top: 10px;
  }
  .cl-route .home-quick-row {
    display: grid;
    grid-auto-flow: column;
    grid-auto-columns: minmax(164px, 1fr);
    gap: 10px;
    overflow-x: auto;
    padding-bottom: 4px;
    scrollbar-width: thin;
  }
  .cl-route .home-quick-card {
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    gap: 8px;
    min-height: 76px;
    padding: 11px 12px;
    border: 1px solid var(--border);
    border-radius: 10px;
    background: var(--surface);
    color: var(--ink);
    cursor: pointer;
    text-align: left;
    transition: border-color var(--transition), box-shadow var(--transition), transform var(--transition);
  }
  .cl-route .home-quick-card:hover {
    background: var(--surface);
    color: var(--ink);
    border-color: var(--border-hover);
    box-shadow: none;
    transform: translateY(-1px);
  }
  .cl-route .home-quick-card:focus-visible {
    background: var(--surface);
    color: var(--ink);
    outline: 2px solid rgba(15, 23, 42, 0.12);
    outline-offset: 2px;
    border-color: var(--border-hover);
    box-shadow: none;
  }
  .cl-route .home-quick-card:active {
    background: var(--surface);
    color: var(--ink);
  }
  .cl-route .home-quick-meta {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: var(--ink-muted);
  }
  .cl-route .home-quick-title {
    display: block;
    font-size: 13px;
    line-height: 1.2;
    margin-top: 2px;
  }
  .cl-route .home-quick-detail {
    display: -webkit-box;
    overflow: hidden;
    margin-top: 0;
    font-size: 12px;
    line-height: 1.45;
    -webkit-line-clamp: 3;
    -webkit-box-orient: vertical;
  }
  .cl-route .home-quick-card .muted {
    color: var(--ink-secondary);
  }
  .cl-route .home-main-grid {
    display: grid;
    grid-template-columns: minmax(0, 1.42fr) minmax(336px, 0.78fr);
    gap: 16px;
    align-items: start;
    margin-bottom: 16px;
  }
  .cl-route .home-primary-panel {
    min-height: 100%;
  }
  .cl-route .home-secondary-panel {
    min-height: 100%;
  }
  .cl-route .home-list-stack {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .cl-route .home-event-row {
    padding: 12px 14px;
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    background: var(--surface);
  }
  .cl-route .home-panel-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 16px;
    align-items: start;
  }
  .cl-route .home-panel-span {
    grid-column: auto;
  }
  .cl-route .home-surface-panel {
    border-radius: 10px;
  }
  .cl-route .home-surface-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 16px;
    margin-bottom: 14px;
    flex-wrap: wrap;
  }
  .cl-route .home-section-label {
    margin-bottom: 6px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--ink-muted);
  }
  .cl-route .home-empty-state {
    min-height: 136px;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 12px;
    text-align: center;
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    background: linear-gradient(180deg, #FFFFFF 0%, #FBFCFE 100%);
    color: var(--ink-secondary);
    font-size: 13px;
    padding: 20px;
  }
  .cl-route .home-empty-glyph {
    width: 42px;
    height: 42px;
    border-radius: 14px;
    background: linear-gradient(180deg, #F8FAFC 0%, #EEF2FF 100%);
    border: 1px solid var(--border);
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.8);
  }
  .cl-route .home-empty-copy {
    max-width: 320px;
    line-height: 1.55;
  }
  .cl-route .templates-toolbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 18px;
    flex-wrap: wrap;
    margin-bottom: 22px;
  }
  .cl-route .templates-toolbar-actions {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
  .cl-route .templates-shell {
    display: grid;
    grid-template-columns: minmax(280px, 340px) minmax(0, 1fr);
    gap: 14px;
    align-items: start;
  }
  .cl-route .templates-sidebar {
    display: flex;
    flex-direction: column;
    gap: 18px;
  }
  .cl-route .templates-main {
    display: grid;
    grid-template-columns: minmax(0, 1.12fr) minmax(320px, 0.88fr);
    gap: 14px;
    align-items: start;
  }
  .cl-route .templates-library-card,
  .cl-route .templates-editor-card,
  .cl-route .templates-preview-card,
  .cl-route .templates-fields-card {
    padding: 22px;
  }
  .cl-route .templates-preview-card {
    position: sticky;
    top: 12px;
  }
  .cl-route .templates-section-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 14px;
  }
  .cl-route .templates-section-head.compact {
    margin-bottom: 12px;
  }
  .cl-route .templates-library-section {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .cl-route .templates-library-divider {
    height: 1px;
    margin: 16px 0;
    background: linear-gradient(90deg, transparent, var(--border), transparent);
  }
  .cl-route .templates-section-kicker {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: var(--ink-muted);
  }
  .cl-route .templates-list {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .cl-route .templates-row {
    display: block;
    width: 100%;
    padding: 12px 13px;
    border: 1px solid var(--border);
    border-radius: 10px;
    background: var(--surface);
    color: var(--ink);
    text-align: left;
  }
  .cl-route .templates-row:hover,
  .cl-route .templates-row:focus-visible,
  .cl-route .templates-row:active {
    background: var(--surface);
    color: var(--ink);
    border-color: var(--border-hover);
  }
  .cl-route .templates-row.is-selected {
    border-color: rgba(15, 23, 42, 0.18);
    background: linear-gradient(180deg, #FFFFFF 0%, #F8FAFC 100%);
    box-shadow: inset 3px 0 0 var(--accent);
  }
  .cl-route .templates-row-top {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 8px;
    margin-bottom: 4px;
  }
  .cl-route .templates-row-title {
    font-size: 14px;
    line-height: 1.25;
  }
  .cl-route .templates-row-tags {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
    justify-content: flex-end;
  }
  .cl-route .templates-row-detail {
    font-size: 12px;
    line-height: 1.45;
    color: var(--ink-secondary);
  }
  .cl-route .templates-pill {
    display: inline-flex;
    align-items: center;
    padding: 4px 9px;
    border-radius: 999px;
    background: var(--accent-soft);
    color: #047857;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }
  .cl-route .templates-pill.muted {
    background: #F1F5F9;
    color: var(--ink-secondary);
  }
  .cl-route .templates-empty-copy {
    padding: 14px 0 2px;
    font-size: 13px;
    line-height: 1.6;
    color: var(--ink-secondary);
  }
  .cl-route .templates-token-cloud {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }
  .cl-route .templates-token {
    padding: 6px 10px;
    border-radius: 999px;
    border: 1px solid var(--border);
    background: #F8FAFC;
    font-size: 12px;
    color: var(--ink-secondary);
    font-family: var(--font-mono);
  }
  .cl-route .templates-editor-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
    margin-bottom: 12px;
  }
  .cl-route .templates-editor-kicker {
    margin-bottom: 6px;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: var(--ink-muted);
  }
  .cl-route .templates-meta-strip {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    margin-bottom: 14px;
  }
  .cl-route .templates-form-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 12px;
  }
  .cl-route .templates-field {
    display: flex;
    flex-direction: column;
    gap: 6px;
    margin-bottom: 12px;
  }
  .cl-route .templates-field:last-of-type {
    margin-bottom: 0;
  }
  .cl-route .templates-field-label {
    font-size: 12px;
    color: var(--ink-muted);
    font-weight: 600;
  }
  .cl-route .templates-mail-preview {
    border: 1px solid var(--border);
    border-radius: 12px;
    background: linear-gradient(180deg, #FFFFFF 0%, #FBFDFF 100%);
    overflow: hidden;
  }
  .cl-route .templates-mail-row {
    display: grid;
    grid-template-columns: 72px minmax(0, 1fr);
    gap: 10px;
    padding: 12px 14px;
    border-bottom: 1px solid var(--border);
  }
  .cl-route .templates-mail-label {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--ink-muted);
  }
  .cl-route .templates-mail-value {
    font-size: 13px;
    color: var(--ink);
    font-weight: 600;
  }
  .cl-route .templates-mail-body {
    padding: 16px 18px;
    background: #FCFDFE;
  }
  .cl-route .templates-mail-body pre {
    margin: 0;
    white-space: pre-wrap;
    font-family: var(--font);
    font-size: 13px;
    line-height: 1.65;
    color: var(--ink);
  }
  .cl-route .secondary-banner {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 16px;
    flex-wrap: wrap;
    padding: 18px 20px;
    margin-bottom: 20px;
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    background: var(--surface);
    box-shadow: var(--shadow-sm);
  }
  .cl-route .secondary-banner.warning {
    border-color: #FCD34D;
    background: #FFFBEB;
  }
  .cl-route .secondary-banner-copy {
    min-width: 0;
    flex: 1;
  }
  .cl-route .secondary-banner-copy h3 {
    margin: 0 0 6px;
  }
  .cl-route .secondary-banner-copy p {
    margin: 0;
  }
  .cl-route .secondary-banner-actions {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
  .cl-route .secondary-shell {
    display: grid;
    grid-template-columns: minmax(0, 1.18fr) minmax(280px, 0.82fr);
    gap: 20px;
    align-items: start;
  }
  .cl-route .billing-hero {
    padding: 18px 20px;
  }
  .cl-route .billing-eyebrow {
    margin: 0 0 10px;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--ink-muted);
  }
  .cl-route .billing-hero-meta {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    margin-top: 14px;
  }
  .cl-route .billing-shell {
    display: grid;
    grid-template-columns: minmax(0, 1.38fr) minmax(340px, 0.72fr);
    gap: 20px;
    align-items: start;
  }
  .cl-route .billing-main-stack,
  .cl-route .billing-side-stack {
    display: flex;
    flex-direction: column;
    gap: 14px;
    min-width: 0;
  }
  .cl-route .billing-side-stack {
    position: sticky;
    top: 12px;
  }
  .cl-route .billing-summary-grid {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 12px;
  }
  .cl-route .billing-summary-card {
    padding: 14px;
    border: 1px solid var(--border);
    border-radius: 10px;
    background: linear-gradient(180deg, #FFFFFF 0%, #FBFCFE 100%);
  }
  .cl-route .billing-summary-card strong {
    display: block;
    margin-bottom: 6px;
    font-size: 12px;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--ink-muted);
  }
  .cl-route .billing-summary-card span {
    display: block;
    font-family: var(--font-display);
    font-size: 18px;
    font-weight: 600;
    letter-spacing: -0.02em;
    color: var(--ink);
  }
  .cl-route .billing-summary-card small {
    display: block;
    margin-top: 6px;
    font-size: 12px;
    color: var(--ink-secondary);
    line-height: 1.5;
  }
  .cl-route .billing-usage-list {
    display: flex;
    flex-direction: column;
    gap: 14px;
  }
  .cl-route .billing-usage-row {
    padding: 12px 14px;
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    background: var(--surface);
  }
  .cl-route .billing-usage-header {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 8px;
    flex-wrap: wrap;
  }
  .cl-route .billing-usage-header strong {
    font-size: 14px;
  }
  .cl-route .billing-usage-header span {
    font-size: 12px;
    font-weight: 600;
    color: var(--ink-secondary);
  }
  .cl-route .billing-usage-bar {
    width: 100%;
    height: 8px;
    border-radius: 999px;
    background: #EEF2F7;
    overflow: hidden;
  }
  .cl-route .billing-usage-fill {
    height: 100%;
    min-width: 8px;
    border-radius: inherit;
    background: #93C5FD;
  }
  .cl-route .billing-usage-fill.success { background: #34D399; }
  .cl-route .billing-usage-fill.warning { background: #FBBF24; }
  .cl-route .billing-usage-fill.danger { background: #F87171; }
  .cl-route .billing-usage-note {
    margin-top: 8px;
    font-size: 12px;
  }
  .cl-route .billing-feature-grid {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 12px;
  }
  .cl-route .billing-feature-card {
    padding: 13px;
    border: 1px solid var(--border);
    border-radius: 10px;
    background: var(--surface);
  }
  .cl-route .billing-feature-title {
    margin-bottom: 10px;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--ink-muted);
  }
  .cl-route .billing-chip-row {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }
  .cl-route .billing-plan-list {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .cl-route .billing-plan-option {
    padding: 12px;
    border: 1px solid var(--border);
    border-radius: 10px;
    background: var(--surface);
  }
  .cl-route .billing-plan-option.is-current {
    border-color: #A7F3D0;
    background: linear-gradient(180deg, #FFFFFF 0%, #F4FFF8 100%);
  }
  .cl-route .billing-plan-row {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
  }
  .cl-route .billing-plan-copy {
    min-width: 0;
    flex: 1;
  }
  .cl-route .billing-plan-copy p {
    margin: 6px 0 10px;
    font-size: 12px;
    line-height: 1.55;
    color: var(--ink-secondary);
  }
  .cl-route .billing-plan-price {
    font-family: var(--font-display);
    font-size: 22px;
    font-weight: 700;
    letter-spacing: -0.03em;
    color: var(--ink);
  }
  .cl-route .rules-hero {
    padding: 18px 20px;
    margin-bottom: 14px;
  }
  .cl-route .rules-hero-meta {
    margin-top: 12px;
    font-size: 12px;
    line-height: 1.6;
    color: var(--ink-secondary);
  }
  .cl-route .rules-hero-summary {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 10px;
    margin-top: 14px;
  }
  .cl-route .rules-hero-stat {
    padding: 11px 12px;
    border: 1px solid rgba(226, 232, 240, 0.9);
    border-radius: 10px;
    background: rgba(255, 255, 255, 0.78);
  }
  .cl-route .rules-hero-stat strong {
    display: block;
    font-family: var(--font-display);
    font-size: 16px;
    font-weight: 700;
    letter-spacing: -0.02em;
    color: var(--ink);
  }
  .cl-route .rules-hero-stat span {
    display: block;
    margin-top: 3px;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: var(--ink-muted);
  }
  .cl-route .rules-workspace-grid {
    display: grid;
    grid-template-columns: minmax(0, 1.4fr) minmax(340px, 0.7fr);
    gap: 18px;
    align-items: start;
  }
  .cl-route .rules-main-stack,
  .cl-route .rules-side-stack {
    display: flex;
    flex-direction: column;
    gap: 14px;
    min-width: 0;
  }
  .cl-route .rules-side-stack {
    position: sticky;
    top: 12px;
  }
  .cl-route .rules-main-stack .rules-stat-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 12px;
  }
  .cl-route .rules-inline-summary {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    margin-bottom: 14px;
  }
  .cl-route .rules-effective-list .secondary-row {
    align-items: flex-start;
    padding: 14px 15px;
  }
  .cl-route .rules-guardrail-note {
    margin-top: 16px;
  }
  .cl-route .rules-main-stack .secondary-stat-card {
    min-height: 88px;
    padding: 16px;
  }
  .cl-route .rules-main-stack .secondary-stat-card strong {
    margin-bottom: 6px;
  }
  .cl-route .rules-main-stack .secondary-stat-card span {
    color: var(--ink-secondary);
  }
  .cl-route .rules-effective-list .secondary-row-copy strong {
    margin-bottom: 3px;
  }
  .cl-route .rules-effective-list .secondary-row-copy p + p {
    margin-top: 4px;
  }
  .cl-route .secondary-main,
  .cl-route .secondary-side {
    display: flex;
    flex-direction: column;
    gap: 18px;
    min-width: 0;
  }
  .cl-route .secondary-list {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .cl-route .secondary-stack {
    display: flex;
    flex-direction: column;
    gap: 16px;
    min-width: 0;
  }
  .cl-route .secondary-row {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 12px;
    align-items: center;
    padding: 10px 12px;
    border: 1px solid var(--border);
    border-radius: 10px;
    background: var(--surface);
  }
  .cl-route .secondary-row-copy {
    min-width: 0;
  }
  .cl-route .secondary-row-copy strong {
    display: block;
    font-size: 14px;
    margin-bottom: 4px;
  }
  .cl-route .secondary-row-copy p {
    margin: 0;
    font-size: 12px;
    color: var(--ink-secondary);
    line-height: 1.55;
  }
  .cl-route .secondary-chip-row {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }
  .cl-route .secondary-chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 9px;
    border-radius: 999px;
    border: 1px solid var(--border);
    background: var(--bg);
    font-size: 11px;
    font-weight: 600;
    color: var(--ink-secondary);
  }
  .cl-route .secondary-note {
    padding: 11px 12px;
    border: 1px solid var(--border);
    border-radius: 10px;
    background: var(--bg);
    font-size: 13px;
    color: var(--ink-secondary);
    line-height: 1.55;
  }
  .cl-route .secondary-stat-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 10px;
  }
  .cl-route .secondary-stat-card {
    padding: 14px 15px;
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    background: var(--surface);
  }
  .cl-route .secondary-stat-card strong {
    display: block;
    margin-bottom: 4px;
    font-size: 13px;
  }
  .cl-route .secondary-stat-card span {
    display: block;
    font-size: 12px;
    line-height: 1.5;
    color: var(--ink-secondary);
  }
  .cl-route .secondary-form-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 12px;
  }
  .cl-route .secondary-empty {
    font-size: 13px;
    line-height: 1.6;
    color: var(--ink-secondary);
  }
  .cl-route .secondary-form-stack {
    display: flex;
    flex-direction: column;
    gap: 14px;
  }
  .cl-route .secondary-search-row {
    position: relative;
  }
  .cl-route .secondary-search-row svg {
    position: absolute;
    left: 10px;
    top: 50%;
    transform: translateY(-50%);
    pointer-events: none;
  }
  .cl-route .secondary-search-row input {
    padding-left: 34px;
  }
  .cl-route .secondary-card-list {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .cl-route .secondary-card {
    padding: 14px 16px;
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    background: var(--surface);
  }
  .cl-route .secondary-card-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
  }
  .cl-route .secondary-card-copy {
    min-width: 0;
    flex: 1;
  }
  .cl-route .secondary-card-title {
    display: block;
    font-size: 14px;
    font-weight: 700;
    line-height: 1.3;
  }
  .cl-route .secondary-card-meta {
    margin-top: 4px;
    font-size: 12px;
    color: var(--ink-secondary);
    line-height: 1.5;
  }
  .cl-route .secondary-card-tags {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    margin-top: 10px;
  }
  .cl-route .secondary-card-stat {
    min-width: 140px;
    text-align: right;
  }
  .cl-route .secondary-card-stat strong {
    display: block;
    font-family: var(--font-display);
    font-size: 20px;
    font-weight: 700;
    letter-spacing: -0.02em;
    color: var(--ink);
  }
  .cl-route .secondary-card-stat span {
    display: block;
    margin-top: 4px;
    font-size: 12px;
    color: var(--ink-secondary);
    line-height: 1.45;
  }
  .cl-route .secondary-card-actions {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    margin-top: 12px;
  }
  .cl-route .secondary-inline-actions {
    display: flex;
    gap: 8px;
    align-items: center;
    flex-wrap: wrap;
  }
  .cl-route .secondary-callout {
    padding: 12px 14px;
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    background: linear-gradient(180deg, #FFFFFF 0%, #FBFCFE 100%);
    font-size: 13px;
    line-height: 1.55;
    color: var(--ink-secondary);
  }
  .cl-route .secondary-callout.warning {
    border-color: #FCD34D;
    background: #FFFBEB;
    color: #92400E;
  }
  .cl-route .record-detail-toolbar {
    margin-bottom: 12px;
  }
  .cl-route.cl-route-record-detail {
    padding: 10px 14px 24px 10px;
  }
  .cl-route.cl-route-record-detail .topbar {
    margin-bottom: 10px;
  }
  .cl-route .record-detail-shell {
    display: grid;
    grid-template-columns: minmax(0, 1.16fr) minmax(320px, 0.84fr);
    gap: 20px;
    align-items: start;
  }
  .cl-route.cl-route-record-detail .record-detail-shell {
    grid-template-columns: minmax(0, 1.52fr) minmax(300px, 0.68fr);
    gap: 16px;
  }
  .cl-route .record-detail-main,
  .cl-route .record-detail-side {
    display: flex;
    flex-direction: column;
    gap: 16px;
    min-width: 0;
  }
  .cl-route .record-detail-side {
    position: sticky;
    top: 12px;
  }
  .cl-route.cl-route-record-detail .record-detail-side {
    max-width: 420px;
    width: 100%;
    justify-self: end;
  }
  .cl-route .record-detail-hero {
    padding: 18px 18px 16px;
  }
  .cl-route.cl-route-record-detail .record-detail-hero {
    padding: 16px 18px 14px;
  }
  .cl-route .record-detail-hero .panel-head {
    margin-bottom: 0;
  }
  .cl-route .record-detail-eyebrow {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    margin-bottom: 10px;
  }
  .cl-route .record-detail-amount {
    font-family: var(--font-display);
    font-size: 32px;
    font-weight: 700;
    letter-spacing: -0.04em;
    line-height: 1;
    color: var(--ink);
  }
  .cl-route .record-detail-meta {
    margin-top: 8px;
    font-size: 13px;
    line-height: 1.55;
    color: var(--ink-secondary);
  }
  .cl-route .record-detail-hero-note {
    margin-top: 14px;
  }
  .cl-route .record-detail-summary {
    margin-top: 14px;
  }
  .cl-route .record-detail-hero-actions {
    margin-top: 14px;
  }
  .cl-route .route-operator-overrides {
    margin-top: 12px;
    border-top: 1px solid var(--border);
    padding-top: 12px;
  }
  .cl-route .route-operator-overrides-summary {
    list-style: none;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    font-size: 13px;
    font-weight: 700;
    color: var(--ink-secondary);
  }
  .cl-route .route-operator-overrides-summary::-webkit-details-marker {
    display: none;
  }
  .cl-route .route-operator-overrides-count {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: 22px;
    height: 22px;
    padding: 0 7px;
    border-radius: 999px;
    border: 1px solid var(--border);
    background: var(--bg);
    font-size: 11px;
    font-weight: 700;
    color: var(--ink-secondary);
  }
  .cl-route .route-operator-overrides-copy {
    margin-top: 10px;
    max-width: 720px;
    font-size: 13px;
    line-height: 1.55;
    color: var(--ink-muted);
  }
  .cl-route .route-operator-overrides-actions {
    margin-top: 12px;
  }
  .cl-route .detail-row-list {
    display: flex;
    flex-direction: column;
    gap: 0;
  }
  .cl-route .detail-row {
    display: grid;
    grid-template-columns: minmax(96px, 0.8fr) minmax(0, 1.2fr);
    gap: 14px;
    align-items: start;
    padding: 10px 0;
    border-bottom: 1px solid var(--border);
  }
  .cl-route .detail-row:first-child {
    padding-top: 0;
  }
  .cl-route .detail-row:last-child {
    padding-bottom: 0;
    border-bottom: none;
  }
  .cl-route .detail-row-label {
    font-size: 12px;
    font-weight: 600;
    color: var(--ink-muted);
  }
  .cl-route .detail-row-value {
    min-width: 0;
    font-size: 13px;
    font-weight: 600;
    color: var(--ink);
    text-align: right;
    line-height: 1.5;
    word-break: break-word;
  }
  .cl-route .detail-row-value.subtle {
    color: var(--ink-secondary);
    font-weight: 500;
  }
  .cl-route .detail-detail-stack {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .cl-route .reports-shell {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(340px, 1fr);
    gap: 20px;
    align-items: start;
  }
  .cl-route .reports-main-stack,
  .cl-route .reports-side-stack {
    display: flex;
    flex-direction: column;
    gap: 18px;
    min-width: 0;
  }
  .cl-route .reports-metric-card {
    padding: 18px;
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    background: var(--surface);
  }
  .cl-route .reports-metric-value {
    font-family: var(--font-display);
    font-size: 28px;
    font-weight: 700;
    letter-spacing: -0.03em;
    color: var(--ink);
    line-height: 1;
  }
  .cl-route .reports-metric-label {
    margin-top: 6px;
    font-size: 13px;
    font-weight: 600;
    color: var(--ink);
  }
  .cl-route .reports-metric-detail {
    margin-top: 6px;
    font-size: 12px;
    color: var(--ink-secondary);
    line-height: 1.5;
  }
  .cl-route .reports-row {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 16px;
    align-items: start;
    padding: 10px 0;
    border-bottom: 1px solid var(--border);
  }
  .cl-route .reports-row:last-child {
    border-bottom: none;
    padding-bottom: 0;
  }
  .cl-route .reports-row-copy strong {
    display: block;
    font-size: 13px;
  }
  .cl-route .reports-row-copy span {
    display: block;
    margin-top: 3px;
    font-size: 12px;
    color: var(--ink-secondary);
    line-height: 1.5;
  }
  .cl-route .reports-row-value {
    font-weight: 700;
    text-align: right;
    font-size: 13px;
  }
  .cl-route .reports-chip-wrap {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }
  .cl-route .reports-export-row {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 12px;
    align-items: center;
    padding: 10px 14px;
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    background: var(--surface);
  }
  .cl-route .reports-inline-result {
    margin-top: 4px;
    font-size: 11px;
    color: var(--ink-secondary);
  }
  .cl-route .recon-steps {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .cl-route .recon-step {
    display: flex;
    align-items: flex-start;
    gap: 10px;
  }
  .cl-route .recon-step-index {
    width: 24px;
    height: 24px;
    border-radius: 999px;
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--accent);
    color: var(--navy);
    font-size: 12px;
    font-weight: 700;
    font-family: var(--font-display);
  }
  .cl-route .recon-step-copy {
    font-size: 13px;
    color: var(--ink-secondary);
    line-height: 1.55;
    padding-top: 2px;
  }
  .cl-route .review-shell {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .cl-route .review-overview-panel,
  .cl-route .review-command-panel,
  .cl-route .review-section-panel,
  .cl-route .review-empty-panel,
  .cl-route .review-disputes-panel {
    padding: 12px 14px;
    margin-bottom: 0;
  }
  .cl-route .review-overview-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 16px;
    flex-wrap: wrap;
  }
  .cl-route .review-overview-copy {
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .cl-route .review-metric-row {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }
  .cl-route .review-metric-pill {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 2px;
    min-width: 104px;
    padding: 9px 11px;
    border: 1px solid var(--border);
    border-radius: 10px;
    background: var(--bg);
    color: var(--ink);
    font-size: 12px;
    font-weight: 700;
  }
  .cl-route .review-metric-pill[data-tone="warning"] {
    border-color: #FCD34D;
    background: #FFFBEB;
    color: #92400E;
  }
  .cl-route .review-metric-pill[data-tone="success"] {
    border-color: #A7F3D0;
    background: #ECFDF5;
    color: #166534;
  }
  .cl-route .review-metric-pill[data-tone="danger"] {
    border-color: #FECACA;
    background: #FEF2F2;
    color: #B91C1C;
  }
  .cl-route .review-metric-value {
    font-family: var(--font-display);
    font-size: 17px;
    font-variant-numeric: tabular-nums;
    line-height: 1;
  }
  .cl-route .review-metric-label {
    font-size: 10px;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    opacity: 0.72;
  }
  .cl-route .review-search-row {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 10px;
    align-items: center;
  }
  .cl-route .review-search-box {
    position: relative;
  }
  .cl-route .review-search-box input {
    width: 100%;
    padding: 8px 8px 8px 34px;
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    font-size: 13px;
    background: var(--bg);
  }
  .cl-route .review-search-helper {
    font-size: 12px;
    text-align: right;
  }
  .cl-route .review-bulk-bar {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
    margin-top: 12px;
    padding-top: 12px;
    border-top: 1px solid var(--border);
  }
  .cl-route .review-bulk-actions {
    justify-content: flex-end;
  }
  .cl-route .review-section-stack {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .cl-route .review-card {
    padding: 10px 12px;
    border: 1px solid var(--border);
    border-radius: 10px;
    background: var(--surface);
  }
  .cl-route .review-card-top {
    margin-bottom: 2px;
  }
  .cl-route .review-badge-row {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
    margin-bottom: 4px;
  }
  .cl-route .review-badge {
    display: inline-flex;
    align-items: center;
    padding: 4px 8px;
    border-radius: 999px;
    border: 1px solid var(--border);
    background: var(--bg);
    color: var(--ink-secondary);
    font-size: 11px;
    font-weight: 700;
  }
  .cl-route .review-badge.info {
    border-color: #BFDBFE;
    background: #EFF6FF;
    color: #1D4ED8;
  }
  .cl-route .review-card-meta {
    color: var(--ink-secondary);
  }
  .cl-route .review-card-summary {
    border-radius: 8px;
  }
  .cl-route .review-card-actions {
    justify-content: flex-end;
  }
  .cl-route .review-count-pill {
    display: inline-flex;
    align-items: center;
    padding: 4px 8px;
    border-radius: 999px;
    border: 1px solid var(--border);
    background: var(--bg);
    color: var(--ink-secondary);
    font-size: 11px;
    font-weight: 700;
  }
  .cl-route .review-section-head h3 {
    font-size: 16px;
  }
  .cl-route .review-block-layout {
    display: grid;
    grid-template-columns: minmax(0, 1.5fr) minmax(268px, 0.7fr);
    gap: 14px;
    align-items: start;
    width: 100%;
  }
  .cl-route .review-block-main,
  .cl-route .review-block-side {
    min-width: 0;
  }
  .cl-route .review-block-side {
    padding: 10px;
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    background: #FCFDFE;
  }
  .cl-route .review-block-facts {
    display: grid;
    grid-template-columns: minmax(120px, 152px) minmax(0, 1fr);
    gap: 8px 12px;
    align-items: start;
  }
  .cl-route .review-block-fact-label {
    font-size: 12px;
    color: var(--ink-muted);
  }
  .cl-route .review-block-fact-value {
    font-size: 13px;
    font-weight: 600;
    color: var(--ink);
    text-align: left;
  }
  .cl-route .review-block-heading {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--ink-muted);
    margin-bottom: 6px;
  }
  .cl-route .review-block-copy {
    font-size: 13px;
    line-height: 1.55;
    color: var(--ink-secondary);
  }
  .cl-route .review-block-note {
    margin-top: 10px;
    padding-top: 10px;
    border-top: 1px solid var(--border);
    font-size: 12px;
    line-height: 1.5;
    color: var(--ink-muted);
  }
  .cl-route .review-block-actions {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    margin-top: 12px;
  }
  .cl-route .settings-section-grid {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    gap: 16px;
  }
  .cl-route .settings-summary-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 12px;
  }
  .cl-route .settings-summary-card {
    padding: 14px 15px;
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    background: #FCFDFE;
  }
  .cl-route .settings-summary-card strong {
    display: block;
    margin-bottom: 4px;
    font-size: 13px;
  }
  .cl-route .settings-summary-card span,
  .cl-route .settings-summary-card p {
    margin: 0;
    font-size: 12px;
    line-height: 1.55;
    color: var(--ink-secondary);
  }

  /* Responsive */
  @media (max-width: 1080px) {
    .cl-route { padding: 24px 24px 32px; }
    .cl-route.cl-route-record-detail { padding: 18px 18px 28px; }
    .cl-route .home-header-shell { flex-direction: column; }
    .cl-route .home-utility-rail { width: 100%; min-width: 0; justify-content: space-between; }
    .cl-route .home-main-grid { grid-template-columns: 1fr; }
    .cl-route .home-panel-grid { grid-template-columns: 1fr; gap: 20px; }
    .cl-route .templates-shell,
    .cl-route .templates-main { grid-template-columns: 1fr; }
    .cl-route .secondary-shell,
    .cl-route .billing-shell,
    .cl-route .reports-shell,
    .cl-route .record-detail-shell { grid-template-columns: 1fr; }
    .cl-route .rules-workspace-grid { grid-template-columns: 1fr; }
    .cl-route .rules-side-stack { position: static; }
    .cl-route .billing-side-stack { position: static; }
    .cl-route .record-detail-side { position: static; }
    .cl-route .templates-preview-card { position: static; }
    .cl-route .billing-summary-grid,
    .cl-route .billing-feature-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .cl-route .home-status-row,
    .cl-route .rules-hero-summary { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .cl-route .rules-main-stack .rules-stat-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .cl-route .review-search-row { grid-template-columns: 1fr; }
    .cl-route .review-search-helper { text-align: left; }
    .cl-route .review-block-layout { grid-template-columns: 1fr; }
    .cl-route .pipeline-filter-grid { grid-template-columns: repeat(2, minmax(0, 1fr)) !important; }
    .cl-route .settings-section-grid { grid-template-columns: 1fr; }
  }
  @media (max-width: 720px) {
    .cl-route { padding: 20px 16px 28px; }
    .cl-route.cl-route-record-detail { padding: 16px 12px 24px; }
    .cl-route .kpi-row { grid-template-columns: repeat(2, 1fr); }
    .cl-route .connector-grid, .cl-route .connector-grid-3 { grid-template-columns: 1fr; }
    .cl-route .home-banner { padding: 14px 16px; }
    .cl-route .home-header-copy h2 { font-size: 26px; }
    .cl-route .home-utility-rail { align-items: stretch; flex-direction: column; }
    .cl-route .home-utility-strip { justify-content: space-between; width: 100%; }
    .cl-route .home-utility-primary { width: 100%; }
    .cl-route .home-status-row,
    .cl-route .rules-hero-summary { grid-template-columns: 1fr; }
    .cl-route .home-quick-row { grid-auto-columns: minmax(220px, 1fr); }
    .cl-route .pipeline-filter-grid { grid-template-columns: 1fr !important; }
    .cl-route .readiness-list { grid-template-columns: 1fr; }
    .cl-route .rules-main-stack .rules-stat-grid { grid-template-columns: 1fr; }
    .cl-route .templates-form-grid { grid-template-columns: 1fr; }
    .cl-route .secondary-stat-grid,
    .cl-route .billing-summary-grid,
    .cl-route .billing-feature-grid,
    .cl-route .secondary-form-grid,
    .cl-route .settings-summary-grid { grid-template-columns: 1fr; }
    .cl-route .secondary-row,
    .cl-route .reports-export-row { grid-template-columns: 1fr; }
    .cl-route .secondary-card-head { flex-direction: column; }
    .cl-route .secondary-card-stat { min-width: 0; text-align: left; }
    .cl-route .detail-row {
      grid-template-columns: 1fr;
      gap: 4px;
    }
    .cl-route .detail-row-value { text-align: left; }
    .cl-route .templates-library-card,
    .cl-route .templates-editor-card,
    .cl-route .templates-preview-card,
    .cl-route .templates-fields-card { padding: 18px; }
  }

  @media (max-height: 860px) and (min-width: 721px) {
    .cl-route {
      padding-top: 10px;
      padding-bottom: 20px;
    }
    .cl-route .pipeline-shell {
      gap: 8px;
    }
    .cl-route .pipeline-hero-panel,
    .cl-route .pipeline-view-panel,
    .cl-route .pipeline-filter-panel {
      padding: 10px 12px !important;
    }
    .cl-route .pipeline-hero-copy {
      gap: 8px;
    }
    .cl-route .pipeline-hero-copy p {
      font-size: 12px;
      line-height: 1.4;
    }
    .cl-route .pipeline-metric-row {
      gap: 6px;
    }
    .cl-route .pipeline-view-head,
    .cl-route .pipeline-filter-footer,
    .cl-route .pipeline-table-head {
      gap: 10px;
    }
    .cl-route .pipeline-view-band + .pipeline-view-band {
      margin-top: 6px !important;
    }
    .cl-route .pipeline-focus-row,
    .cl-route .pipeline-saved-input-row,
    .cl-route .pipeline-filter-footer {
      margin-top: 8px;
      padding-top: 8px;
    }
    .cl-route .pipeline-saved-input-row input {
      max-width: 220px;
      min-height: 34px;
      padding-top: 8px;
      padding-bottom: 8px;
    }
    .cl-route .pipeline-filter-grid {
      gap: 8px !important;
    }
    .cl-route .pipeline-filter-grid label,
    .cl-route .pipeline-filter-footer label {
      gap: 4px !important;
    }
    .cl-route .pipeline-filter-grid .muted,
    .cl-route .pipeline-filter-footer .muted,
    .cl-route .pipeline-table-meta .muted {
      font-size: 11px !important;
    }
    .cl-route .pipeline-table-head {
      padding-top: 8px !important;
      padding-bottom: 8px !important;
    }
    .cl-route .pipeline-table th {
      padding-top: 8px;
      padding-bottom: 8px;
    }
    .cl-route .pipeline-table td {
      padding-top: 8px;
      padding-bottom: 8px;
    }
  }

  /* ==================== SPINNER ==================== */

  .cl-route .loading-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 10px;
    padding: 40px 0;
    color: var(--ink-muted);
    font-size: 13px;
  }
  .cl-route .spinner {
    width: 24px;
    height: 24px;
    border: 2.5px solid var(--border);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: route-spin 0.6s linear infinite;
  }
  @keyframes route-spin { to { transform: rotate(360deg); } }
`;
