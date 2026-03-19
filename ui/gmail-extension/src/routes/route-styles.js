/**
 * Shared CSS for InboxSDK route pages.
 * Extracted from static/console/styles.css — component styles only.
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

    font-family: var(--font);
    font-size: 14px;
    line-height: 1.5;
    color: var(--ink);
    padding: 32px 40px;
    max-width: 880px;
    -webkit-font-smoothing: antialiased;
  }

  .cl-route *, .cl-route *::before, .cl-route *::after { box-sizing: border-box; }

  /* Topbar */
  .cl-route .topbar { margin-bottom: 28px; }
  .cl-route .topbar h2 { margin: 0; font-family: var(--font-display); font-size: 28px; font-weight: 700; letter-spacing: -0.02em; }
  .cl-route .topbar p { margin: 6px 0 0; color: var(--ink-muted); font-size: 14px; }

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
  .cl-route textarea { min-height: 160px; resize: vertical; font-family: var(--font-mono); font-size: 13px; }

  /* Buttons */
  .cl-route button {
    display: inline-flex; align-items: center; justify-content: center; gap: 8px;
    border: none; border-radius: var(--radius-sm); padding: 10px 20px;
    font: inherit; font-size: 14px; font-weight: 600; cursor: pointer;
    background: var(--accent); color: var(--navy, #0A1628);
    transition: background var(--transition), transform var(--transition), box-shadow var(--transition);
  }
  .cl-route button:hover { background: var(--accent-hover); transform: translateY(-1px); box-shadow: var(--shadow-sm); }
  .cl-route button:active { transform: translateY(0); }
  .cl-route button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .cl-route button:disabled { opacity: 0.4; cursor: not-allowed; transform: none; box-shadow: none; }
  .cl-route button.alt {
    background: var(--surface); color: var(--ink); border: 1px solid var(--border);
  }
  .cl-route button.alt:hover { background: #f5f5f5; border-color: var(--border-hover); }

  /* Panels */
  .cl-route .panel {
    background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-lg);
    padding: 24px; margin-bottom: 20px; box-shadow: var(--shadow-sm);
  }
  .cl-route .panel h3 { margin: 0 0 12px; font-family: var(--font-display); font-size: 20px; font-weight: 600; letter-spacing: -0.01em; }
  .cl-route .muted { font-size: 13px; color: var(--ink-muted); line-height: 1.5; }

  /* KPI cards */
  .cl-route .kpi-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 20px; }
  .cl-route .kpi-card {
    background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-md);
    padding: 20px; box-shadow: var(--shadow-sm);
  }
  .cl-route .kpi-card strong { display: block; font-family: var(--font-mono); font-size: 28px; font-weight: 600; font-variant-numeric: tabular-nums; letter-spacing: -0.02em; line-height: 1.1; margin-bottom: 4px; }
  .cl-route .kpi-card span { font-size: 13px; color: var(--ink-muted); font-weight: 500; }
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
  .cl-route .table td { text-align: left; padding: 12px; font-size: 14px; border-bottom: 1px solid var(--border); }
  .cl-route .table tr:hover td { background: var(--bg); }

  /* Readiness */
  .cl-route .readiness-list { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; margin-bottom: 16px; }
  .cl-route .readiness-item { font-size: 14px; padding: 12px 14px; border-radius: var(--radius-sm); background: var(--bg); border: 1px solid var(--border); }
  .cl-route .launch-btn { width: 100%; padding: 16px; font-size: 16px; font-weight: 700; border-radius: var(--radius-md); }

  /* Utility */
  .cl-route .row { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; }
  .cl-route .mt-0 { margin-top: 0; }
  .cl-route .mt-10 { margin-top: 12px; }

  /* Responsive */
  @media (max-width: 720px) {
    .cl-route { padding: 20px 16px; }
    .cl-route .kpi-row { grid-template-columns: repeat(2, 1fr); }
    .cl-route .connector-grid, .cl-route .connector-grid-3 { grid-template-columns: 1fr; }
    .cl-route .readiness-list { grid-template-columns: 1fr; }
  }
`;
