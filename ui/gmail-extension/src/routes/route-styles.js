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
    font-size: 14px;
    line-height: 1.5;
    color: var(--ink);
    padding: 28px 32px 40px;
    width: 100%;
    max-width: 1240px;
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
    border: 1px solid transparent; border-radius: var(--radius-sm); padding: 9px 16px;
    font: inherit; font-size: 14px; font-weight: 600; cursor: pointer;
    background: var(--accent); color: var(--navy, #0A1628);
    transition: background var(--transition), transform var(--transition), box-shadow var(--transition);
  }
  .cl-route button:hover { background: var(--accent-hover); transform: translateY(-1px); box-shadow: var(--shadow-sm); }
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
    gap: 16px;
    flex-wrap: wrap;
    margin-bottom: 14px;
  }
  .cl-route .panel-head.compact {
    margin-bottom: 12px;
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
  .cl-route .home-hero {
    display: flex;
    justify-content: center;
    margin: 8px 0 20px;
  }
  .cl-route .home-hero-copy {
    max-width: 720px;
    text-align: center;
  }
  .cl-route .home-eyebrow {
    display: flex;
    align-items: center;
    gap: 8px;
    margin: 0 0 12px;
    color: var(--ink-muted);
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }
  .cl-route .home-eyebrow::before {
    content: '';
    width: 12px;
    height: 12px;
    border-radius: 999px;
    border: 1.5px solid var(--border-hover);
    background: var(--surface);
  }
  .cl-route .home-banner {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    flex-wrap: wrap;
    padding: 16px 18px;
    margin-bottom: 28px;
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    background: var(--surface);
    box-shadow: var(--shadow-sm);
  }
  .cl-route .home-banner.warning {
    border-color: #FCD34D;
    background: #FFFBEB;
  }
  .cl-route .home-quick-row {
    display: grid;
    grid-auto-flow: column;
    grid-auto-columns: minmax(148px, 172px);
    gap: 10px;
    overflow-x: auto;
    padding-bottom: 6px;
    margin-bottom: 22px;
    scrollbar-color: rgba(148, 163, 184, 0.85) transparent;
  }
  .cl-route .home-quick-card {
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    gap: 6px;
    min-height: 72px;
    padding: 11px 12px;
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
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
    box-shadow: var(--shadow-sm);
    transform: translateY(-1px);
  }
  .cl-route .home-quick-card:focus-visible {
    background: var(--surface);
    color: var(--ink);
    outline: 2px solid rgba(15, 23, 42, 0.12);
    outline-offset: 2px;
    border-color: var(--border-hover);
    box-shadow: var(--shadow-sm);
  }
  .cl-route .home-quick-card:active {
    background: var(--surface);
    color: var(--ink);
  }
  .cl-route .home-quick-meta {
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.03em;
    text-transform: uppercase;
    color: var(--ink-muted);
  }
  .cl-route .home-quick-title {
    display: block;
    font-size: 13px;
    line-height: 1.2;
    margin-top: 6px;
  }
  .cl-route .home-quick-detail {
    display: -webkit-box;
    overflow: hidden;
    margin-top: 3px;
    font-size: 11px;
    line-height: 1.35;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
  }
  .cl-route .home-quick-card .muted {
    color: var(--ink-secondary);
  }
  .cl-route .home-panel-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 24px;
    align-items: start;
  }
  .cl-route .home-panel-span {
    grid-column: 1 / -1;
  }
  .cl-route .home-tools-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 18px;
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
    gap: 20px;
    align-items: start;
  }
  .cl-route .templates-sidebar,
  .cl-route .templates-main {
    display: flex;
    flex-direction: column;
    gap: 18px;
  }
  .cl-route .templates-library-card,
  .cl-route .templates-editor-card,
  .cl-route .templates-preview-card,
  .cl-route .templates-fields-card {
    padding: 22px;
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
  .cl-route .secondary-row {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 12px;
    align-items: center;
    padding: 12px 14px;
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
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
    padding: 5px 10px;
    border-radius: 999px;
    border: 1px solid var(--border);
    background: var(--bg);
    font-size: 12px;
    font-weight: 600;
    color: var(--ink-secondary);
  }
  .cl-route .secondary-note {
    padding: 13px 14px;
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
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
  .cl-route .review-block-layout {
    display: grid;
    grid-template-columns: minmax(0, 1.35fr) minmax(240px, 0.85fr);
    gap: 14px;
    align-items: start;
    width: 100%;
  }
  .cl-route .review-block-main,
  .cl-route .review-block-side {
    min-width: 0;
  }
  .cl-route .review-block-side {
    padding: 12px;
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
    .cl-route .home-panel-grid { grid-template-columns: 1fr; gap: 20px; }
    .cl-route .templates-shell { grid-template-columns: 1fr; }
    .cl-route .secondary-shell { grid-template-columns: 1fr; }
    .cl-route .review-block-layout { grid-template-columns: 1fr; }
    .cl-route .settings-section-grid { grid-template-columns: 1fr; }
  }
  @media (max-width: 720px) {
    .cl-route { padding: 20px 16px 28px; }
    .cl-route .kpi-row { grid-template-columns: repeat(2, 1fr); }
    .cl-route .connector-grid, .cl-route .connector-grid-3 { grid-template-columns: 1fr; }
    .cl-route .home-banner { padding: 14px 16px; }
    .cl-route .home-quick-row { grid-auto-columns: minmax(156px, 188px); }
    .cl-route .home-tools-grid { grid-template-columns: 1fr; }
    .cl-route .readiness-list { grid-template-columns: 1fr; }
    .cl-route .templates-form-grid { grid-template-columns: 1fr; }
    .cl-route .secondary-stat-grid,
    .cl-route .secondary-form-grid,
    .cl-route .settings-summary-grid { grid-template-columns: 1fr; }
    .cl-route .templates-library-card,
    .cl-route .templates-editor-card,
    .cl-route .templates-preview-card,
    .cl-route .templates-fields-card { padding: 18px; }
  }
`;
