/** Sidebar CSS — injected into Gmail DOM via <style> block
 *  Design system: DESIGN.md — Industrial/Utilitarian, mint green + navy
 *  Typography: Instrument Sans (headings) + DM Sans (body) + Geist Mono (data) */

export const SIDEBAR_CSS = `
      /* Fonts loaded via <link> tags in injectFonts() — no @import needed */

      .cl-sidebar {
        --cl-bg: #FAFAF8;
        --cl-surface: #FFFFFF;
        --cl-card: #FFFFFF;
        --cl-border: #E2E8F0;
        --cl-border-hover: #CBD5E1;
        --cl-primary: #0F172A;
        --cl-secondary: #475569;
        --cl-muted: #94A3B8;
        --cl-accent: #00D67E;
        --cl-accent-hover: #00BC6E;
        --cl-accent-soft: #ECFDF5;
        --cl-brand-muted: #10B981;
        --cl-navy: #0A1628;
        --cl-navy-light: #1E293B;
        --cl-green: #16A34A;
        --cl-green-soft: #F0FDF4;
        --cl-green-text: #16A34A;
        --cl-amber: #CA8A04;
        --cl-amber-soft: #FEFCE8;
        --cl-red: #DC2626;
        --cl-red-soft: #FEF2F2;
        --cl-info: #2563EB;
        --cl-info-soft: #EFF6FF;
        --cl-radius-sm: 6px;
        --cl-radius-md: 8px;
        --cl-radius-lg: 12px;
        --cl-shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.04);
        --cl-shadow-md: 0 2px 8px rgba(0, 0, 0, 0.06);
        --cl-font-display: 'Instrument Sans', -apple-system, system-ui, sans-serif;
        --cl-font-body: 'DM Sans', -apple-system, system-ui, sans-serif;
        --cl-font-mono: 'Geist Mono', 'SF Mono', monospace;
        --cl-transition: 0.15s ease;
        --cl-shell-pad-y: 10px;
        --cl-shell-pad-x: 8px;
        --cl-surface-pad: 10px;
        --cl-panel-pad: 10px;
        font-family: var(--cl-font-body);
        -webkit-font-smoothing: antialiased;
        -moz-osx-font-smoothing: grayscale;
        color: var(--cl-primary);
        padding: var(--cl-shell-pad-y) var(--cl-shell-pad-x);
        display: flex;
        flex-direction: column;
        gap: 8px;
        height: 100%;
        width: 100%;
        min-width: 0;
        box-sizing: border-box;
        background: var(--cl-bg);
        position: relative;
        font-size: 13px;
        line-height: 1.5;
      }

      /* ==================== HEADER ==================== */

      .cl-header {
        display: flex;
        align-items: center;
        gap: 8px;
        padding-bottom: 8px;
        border-bottom: 1px solid var(--cl-border);
      }
      .cl-title {
        font-family: var(--cl-font-display);
        font-size: 15px;
        font-weight: 700;
        letter-spacing: -0.02em;
        color: var(--cl-primary);
        display: flex;
        align-items: center;
        gap: 8px;
      }
      .cl-logo {
        width: 20px;
        height: 20px;
        display: inline-block;
        border-radius: 5px;
      }
      .cl-header-right {
        margin-left: auto;
        display: flex;
        align-items: center;
        gap: 6px;
      }
      .cl-header-badge {
        font-size: 11px;
        font-weight: 600;
        color: var(--cl-brand-muted, #10B981);
        background: var(--cl-accent-soft, #ECFDF5);
        padding: 2px 8px;
        border-radius: 999px;
      }

      /* ==================== TOAST ==================== */

      .cl-toast {
        font-size: 13px;
        font-weight: 500;
        color: #fff;
        background: var(--cl-primary);
        border-radius: var(--cl-radius-sm);
        padding: 10px 14px;
        display: none;
        box-shadow: var(--cl-shadow-md);
      }
      .cl-toast[data-tone="error"] {
        background: var(--cl-red);
      }
      .cl-toast[data-tone="success"] {
        background: var(--cl-green);
      }

      /* ==================== ACTION DIALOG ==================== */

      .cl-action-dialog {
        position: absolute;
        inset: 0;
        display: none;
        align-items: center;
        justify-content: center;
        z-index: 24;
        background: rgba(26, 26, 26, 0.5);
        padding: 16px;
      }
      .cl-action-dialog-card {
        width: 100%;
        max-width: 320px;
        border-radius: var(--cl-radius-md);
        border: 1px solid var(--cl-border);
        background: var(--cl-surface);
        box-shadow: 0 12px 40px rgba(0, 0, 0, 0.1);
        padding: 20px;
        display: flex;
        flex-direction: column;
        gap: 12px;
      }
      .cl-action-dialog-title {
        font-size: 15px;
        font-weight: 700;
        color: var(--cl-primary);
        letter-spacing: -0.01em;
      }
      .cl-action-dialog-label {
        font-size: 12px;
        font-weight: 500;
        color: var(--cl-secondary);
      }
      .cl-action-dialog-message {
        font-size: 12px;
        color: var(--cl-secondary);
        line-height: 1.5;
      }
      .cl-action-dialog-preview {
        display: flex;
        flex-direction: column;
        gap: 6px;
        padding: 10px 12px;
        border: 1px solid var(--cl-border);
        border-radius: var(--cl-radius-sm);
        background: var(--cl-bg);
      }
      .cl-action-dialog-preview-line {
        font-size: 12px;
        color: var(--cl-primary);
        line-height: 1.45;
      }
      .cl-action-dialog-chips {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
      }
      .cl-action-chip {
        border: 1px solid var(--cl-border);
        background: var(--cl-bg);
        color: var(--cl-secondary);
        border-radius: 999px;
        font-size: 11px;
        font-weight: 600;
        padding: 4px 10px;
        cursor: pointer;
        transition: border-color var(--cl-transition), color var(--cl-transition);
      }
      .cl-action-chip:hover {
        border-color: var(--cl-accent);
        color: var(--cl-accent);
      }
      .cl-action-chip:focus-visible {
        outline: 2px solid var(--cl-accent);
        outline-offset: 2px;
      }
      .cl-action-dialog-input {
        width: 100%;
        min-height: 38px;
        border-radius: var(--cl-radius-sm);
        border: 1px solid var(--cl-border);
        padding: 8px 12px;
        font: inherit;
        font-size: 13px;
        color: var(--cl-primary);
        background: var(--cl-surface);
        transition: border-color var(--cl-transition), box-shadow var(--cl-transition);
      }
      .cl-action-dialog-input:focus {
        border-color: var(--cl-accent);
        outline: none;
        box-shadow: 0 0 0 3px var(--cl-accent-soft);
      }
      .cl-action-dialog-hint {
        font-size: 11px;
        color: var(--cl-muted);
        line-height: 1.4;
      }
      .cl-action-dialog-actions {
        display: flex;
        justify-content: flex-end;
        gap: 8px;
        margin-top: 4px;
      }

      /* ==================== SECTIONS ==================== */

      .cl-section {
        background: var(--cl-surface);
        border: 1px solid var(--cl-border);
        border-radius: var(--cl-radius-md);
        padding: var(--cl-surface-pad);
        display: flex;
        flex-direction: column;
        gap: 10px;
        box-shadow: var(--cl-shadow-sm);
        min-width: 0;
      }
      .cl-section-title {
        font-size: 11px;
        font-weight: 600;
        color: var(--cl-muted);
        text-transform: uppercase;
        letter-spacing: 0.05em;
      }

      /* ==================== INVOICE CARD ==================== */

      .cl-thread-card {
        background: var(--cl-card);
        border: 1px solid var(--cl-border);
        border-radius: var(--cl-radius-md);
        padding: var(--cl-surface-pad);
        display: flex;
        flex-direction: column;
        gap: 8px;
        box-shadow: var(--cl-shadow-sm);
        min-width: 0;
      }

      /* Navigator */
      .cl-navigator {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
        padding-bottom: 10px;
        border-bottom: 1px solid var(--cl-border);
        margin-bottom: 4px;
      }
      .cl-nav-label {
        font-size: 12px;
        font-weight: 500;
        color: var(--cl-muted);
        font-variant-numeric: tabular-nums;
      }
      .cl-nav-buttons {
        display: flex;
        gap: 4px;
      }
      .cl-nav-btn {
        width: 28px;
        height: 28px;
        display: flex;
        align-items: center;
        justify-content: center;
        border-radius: 6px;
        border: 1px solid var(--cl-border);
        background: var(--cl-surface);
        color: var(--cl-secondary);
        font-size: 14px;
        cursor: pointer;
        transition: background var(--cl-transition), border-color var(--cl-transition);
        padding: 0;
      }
      .cl-nav-btn:hover:not(:disabled) {
        background: var(--cl-bg);
        border-color: var(--cl-border-hover);
      }
      .cl-nav-btn:disabled {
        opacity: 0.3;
        cursor: not-allowed;
      }

      /* Vendor + State header */
      .cl-thread-header {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 6px;
        flex-wrap: wrap;
      }
      .cl-thread-header-copy {
        min-width: 0;
        display: flex;
        flex-direction: column;
        gap: 3px;
        flex: 1 1 180px;
      }
      .cl-thread-header-with-thumb {
        display: flex;
        align-items: center;
        gap: 10px;
      }
      .cl-thread-title {
        font-weight: 700;
        font-size: 14px;
        letter-spacing: -0.01em;
        color: var(--cl-primary);
      }

      /* Amount display */
      .cl-amount-row {
        display: flex;
        align-items: baseline;
        gap: 12px;
        margin: 2px 0;
      }
      .cl-amount {
        font-size: 22px;
        font-weight: 700;
        letter-spacing: -0.02em;
        color: var(--cl-primary);
        font-variant-numeric: tabular-nums;
      }
      .cl-amount-currency {
        font-size: 13px;
        font-weight: 500;
        color: var(--cl-muted);
      }

      /* Invoice meta */
      .cl-meta-row {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        font-size: 12px;
        color: var(--cl-secondary);
      }
      .cl-meta-tag {
        display: inline-flex;
        align-items: center;
        gap: 4px;
        background: var(--cl-bg);
        border: 1px solid var(--cl-border);
        border-radius: 6px;
        padding: 3px 8px;
        font-size: 11px;
        font-weight: 500;
        color: var(--cl-secondary);
      }
      .cl-thread-meta-inline {
        font-size: 11.5px;
        color: var(--cl-secondary);
        line-height: 1.38;
        overflow-wrap: anywhere;
      }
      .cl-thread-header > .cl-pill {
        margin-left: auto;
        flex-shrink: 0;
      }

      .cl-blocker-list {
        display: flex;
        flex-direction: column;
        gap: 8px;
      }
      .cl-blocker-row {
        border: 1px solid var(--cl-border);
        border-radius: var(--cl-radius-sm);
        background: var(--cl-bg);
        padding: 9px 10px;
        display: flex;
        flex-direction: column;
        gap: 3px;
      }
      .cl-blocker-label {
        font-size: 12px;
        font-weight: 700;
        color: var(--cl-primary);
      }
      .cl-blocker-detail {
        font-size: 12px;
        color: var(--cl-secondary);
        line-height: 1.45;
      }
      .cl-state-note {
        font-size: 12px;
        color: var(--cl-secondary);
        line-height: 1.45;
        border: 1px solid var(--cl-border);
        border-radius: var(--cl-radius-sm);
        background: var(--cl-surface);
        padding: 9px 10px;
      }
      .cl-review-panel {
        border: 1px solid #fcd34d;
        border-radius: var(--cl-radius-sm);
        background: var(--cl-amber-soft);
        padding: 9px 10px;
        display: flex;
        flex-direction: column;
        gap: 7px;
      }
      .cl-review-copy {
        font-size: 12px;
        color: #78350f;
        line-height: 1.45;
      }
      .cl-review-card {
        border: 1px solid rgba(180, 83, 9, 0.16);
        border-radius: var(--cl-radius-sm);
        background: rgba(255, 255, 255, 0.82);
        padding: 9px 10px;
        display: flex;
        flex-direction: column;
        gap: 5px;
      }
      .cl-review-card-title {
        font-size: 12px;
        font-weight: 700;
        color: var(--cl-primary);
      }
      .cl-review-row {
        display: flex;
        align-items: baseline;
        justify-content: space-between;
        gap: 10px;
      }
      .cl-review-label {
        font-size: 11px;
        color: var(--cl-secondary);
      }
      .cl-review-value {
        font-size: 12px;
        font-weight: 600;
        color: var(--cl-primary);
        text-align: right;
      }
      .cl-review-why {
        font-size: 11px;
        color: #92400e;
        line-height: 1.45;
      }

      .cl-evidence-section {
        border-top: 1px solid var(--cl-border);
        padding-top: 8px;
        display: flex;
        flex-direction: column;
        gap: 8px;
      }
      .cl-evidence-list {
        display: flex;
        flex-direction: column;
        gap: 6px;
      }
      .cl-evidence-row {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
        font-size: 12px;
      }
      .cl-evidence-label {
        color: var(--cl-secondary);
      }
      .cl-evidence-status {
        font-weight: 600;
        color: var(--cl-primary);
      }
      .cl-evidence-status[data-status="ok"] {
        color: var(--cl-green);
      }
      .cl-evidence-status[data-status="missing"] {
        color: var(--cl-muted);
      }

      /* Decision / reasoning banners */
      .cl-agent-reasoning-banner {
        margin: 4px 0;
        padding: 10px 12px;
        background: #f0f4ff;
        border-left: 3px solid #6366f1;
        border-radius: 0 var(--cl-radius-sm) var(--cl-radius-sm) 0;
        font-size: 12px;
        color: #312e81;
        line-height: 1.45;
      }
      .cl-agent-label {
        font-weight: 700;
        color: #4338ca;
        margin-right: 4px;
      }
      .cl-agent-risks {
        margin-top: 4px;
        display: flex;
        flex-wrap: wrap;
        gap: 4px;
      }
      .cl-discount-banner {
        margin: 4px 0;
        padding: 10px 12px;
        background: var(--cl-green-soft);
        border-left: 3px solid var(--cl-green);
        border-radius: 0 var(--cl-radius-sm) var(--cl-radius-sm) 0;
        font-size: 12px;
        color: var(--cl-green-text);
        line-height: 1.45;
      }
      .cl-discount-label {
        font-weight: 700;
        color: var(--cl-green);
        margin-right: 4px;
      }
      .cl-needs-info-banner {
        margin: 4px 0;
        padding: 10px 12px;
        background: var(--cl-amber-soft);
        border-left: 3px solid var(--cl-amber);
        border-radius: 0 var(--cl-radius-sm) var(--cl-radius-sm) 0;
        font-size: 12px;
        color: #78350f;
        line-height: 1.45;
      }
      .cl-needs-info-label {
        font-weight: 700;
        color: #b45309;
        margin-right: 4px;
      }
      .cl-needs-info-meta {
        margin-top: 4px;
        color: #92400e;
      }

      .cl-decision-banner {
        border: 1px solid var(--cl-border);
        border-radius: var(--cl-radius-sm);
        padding: 10px 12px;
        background: var(--cl-surface);
      }
      .cl-decision-title {
        font-size: 12px;
        font-weight: 700;
        color: var(--cl-primary);
      }
      .cl-decision-detail {
        margin-top: 2px;
        font-size: 11px;
        color: var(--cl-secondary);
      }
      .cl-decision-good {
        border-color: #a7e3d0;
        background: #fafdfb;
      }
      .cl-decision-warning {
        border-color: #fcd34d;
        background: var(--cl-amber-soft);
      }
      .cl-decision-neutral {
        border-color: var(--cl-border);
        background: var(--cl-bg);
      }

      .cl-risk-row {
        display: flex;
        flex-wrap: wrap;
        gap: 4px;
        margin-top: 4px;
      }
      .cl-risk-chip {
        font-size: 10px;
        font-weight: 600;
        border: 1px solid var(--cl-border);
        border-radius: 999px;
        padding: 2px 8px;
        color: var(--cl-secondary);
        background: var(--cl-bg);
      }
      .cl-risk-chip-warning {
        border-color: var(--cl-amber);
        color: #92400e;
        background: var(--cl-amber-soft);
      }

      /* Operator brief */
      .cl-operator-brief {
        border: 1px solid var(--cl-border);
        border-radius: var(--cl-radius-sm);
        background: var(--cl-bg);
        display: flex;
        flex-direction: column;
      }
      .cl-operator-brief[data-tone="warning"] {
        border-color: #fcd34d;
        background: var(--cl-amber-soft);
      }
      .cl-operator-brief[data-tone="good"] {
        border-color: #a7e3d0;
        background: var(--cl-green-soft);
      }
      .cl-operator-brief-row {
        display: flex;
        flex-direction: column;
        gap: 2px;
        padding: 8px 10px;
      }
      .cl-operator-brief-row + .cl-operator-brief-row {
        border-top: 1px dashed var(--cl-border);
      }
      .cl-operator-brief-label {
        font-size: 10px;
        font-weight: 700;
        color: var(--cl-secondary);
        text-transform: uppercase;
        letter-spacing: 0.04em;
      }
      .cl-operator-brief-text {
        font-size: 12px;
        color: var(--cl-primary);
        line-height: 1.4;
      }
      .cl-operator-brief-outcome {
        margin-top: 1px;
        font-size: 10px;
        color: var(--cl-secondary);
      }

      /* ==================== BUTTONS ==================== */

      .cl-btn {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        gap: 6px;
        border-radius: var(--cl-radius-sm);
        border: none;
        background: var(--cl-accent);
        color: var(--cl-navy, #0A1628);
        font: inherit;
        font-size: 13px;
        font-weight: 600;
        padding: 8px 16px;
        cursor: pointer;
        transition: background var(--cl-transition), transform var(--cl-transition), box-shadow var(--cl-transition);
      }
      .cl-btn:hover:not(:disabled) {
        background: var(--cl-accent-hover);
        transform: translateY(-1px);
        box-shadow: var(--cl-shadow-sm);
      }
      .cl-btn:active:not(:disabled) {
        transform: translateY(0);
      }
      .cl-btn:disabled {
        opacity: 0.4;
        cursor: not-allowed;
        transform: none;
        box-shadow: none;
      }
      .cl-btn:focus-visible {
        outline: 2px solid var(--cl-accent);
        outline-offset: 2px;
      }
      .cl-btn-secondary {
        background: var(--cl-surface);
        color: var(--cl-primary);
        border: 1px solid var(--cl-border);
      }
      .cl-btn-secondary:hover:not(:disabled) {
        background: #f5f5f5;
        border-color: var(--cl-border-hover);
        transform: none;
        box-shadow: none;
      }
      .cl-btn-approve {
        background: var(--cl-green) !important;
        color: white !important;
      }
      .cl-btn-approve:hover:not(:disabled) {
        background: #158a6a !important;
      }
      .cl-btn-review {
        background: var(--cl-amber) !important;
        color: white !important;
      }
      .cl-btn-small {
        font-size: 12px;
        padding: 5px 10px;
      }
      .cl-primary-cta {
        margin-top: 4px;
        width: 100%;
        padding: 9px 14px;
        font-size: 13px;
        font-weight: 700;
        border-radius: var(--cl-radius-sm);
        letter-spacing: -0.01em;
      }

      /* ==================== THREAD ACTIONS ==================== */

      .cl-thread-actions {
        display: flex;
        gap: 6px;
        margin-top: 4px;
        flex-wrap: wrap;
      }
      .cl-auth-copy {
        font-size: 12px;
        color: var(--cl-secondary);
        line-height: 1.45;
      }

      /* ==================== BLOCKED / EMPTY STATES ==================== */

      .cl-blocked-reasons {
        display: flex;
        flex-direction: column;
        gap: 4px;
      }
      .cl-blocker-item {
        border-top: 0;
        margin-top: 0;
        padding-top: 0;
      }

      /* ==================== SCAN STATUS ==================== */

      .cl-scan-status {
        display: flex;
        align-items: center;
        gap: 8px;
        font-size: 12px;
        color: var(--cl-secondary);
      }
      .cl-scan-status::before {
        content: '';
        width: 7px;
        height: 7px;
        border-radius: 50%;
        flex-shrink: 0;
        background: var(--cl-green);
      }
      .cl-scan-status[data-tone="error"] {
        color: var(--cl-red);
      }
      .cl-scan-status[data-tone="error"]::before {
        background: var(--cl-red);
      }
      .cl-scan-status[data-tone="warning"]::before {
        background: var(--cl-amber);
      }
      .cl-inline-actions {
        display: none;
        margin-top: 8px;
      }

      /* ==================== QUEUE ==================== */

      .cl-queue {
        display: flex;
        flex-direction: column;
        gap: 6px;
      }
      .cl-queue-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
      }
      .cl-queue-count {
        font-size: 12px;
        color: var(--cl-muted);
      }
      .cl-queue-list {
        display: flex;
        flex-direction: column;
        gap: 6px;
        max-height: 220px;
        overflow-y: auto;
        padding-right: 2px;
      }
      .cl-queue-row {
        border: 1px solid var(--cl-border);
        border-radius: var(--cl-radius-sm);
        padding: 10px;
        background: var(--cl-card);
        display: flex;
        flex-direction: column;
        gap: 4px;
        cursor: pointer;
        transition: border-color var(--cl-transition), box-shadow var(--cl-transition);
      }
      .cl-queue-row:hover {
        border-color: var(--cl-border-hover);
        box-shadow: var(--cl-shadow-sm);
      }
      .cl-queue-row-active {
        border-color: var(--cl-accent);
        background: var(--cl-accent-soft);
      }
      .cl-queue-row-main {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
      }
      .cl-queue-row-meta {
        display: flex;
        align-items: baseline;
        gap: 8px;
        flex-wrap: wrap;
      }
      .cl-queue-vendor {
        font-size: 13px;
        font-weight: 600;
        color: var(--cl-primary);
      }
      .cl-queue-amount {
        font-size: 12px;
        font-weight: 600;
        color: var(--cl-secondary);
        font-variant-numeric: tabular-nums;
      }
      .cl-queue-subject {
        font-size: 12px;
        color: var(--cl-secondary);
        line-height: 1.35;
      }
      .cl-queue-meta {
        font-size: 11px;
        color: var(--cl-muted);
      }

      /* ==================== CONTEXT TABS ==================== */

      .cl-context-tabs {
        margin-top: 8px;
        display: flex;
        gap: 0;
        border-bottom: 1px solid var(--cl-border);
      }
      .cl-context-tab {
        border: none;
        border-bottom: 2px solid transparent;
        border-radius: 0;
        background: none;
        color: var(--cl-muted);
        padding: 6px 10px;
        font: inherit;
        font-size: 12px;
        font-weight: 500;
        cursor: pointer;
        transition: color var(--cl-transition), border-color var(--cl-transition);
      }
      .cl-context-tab:hover {
        color: var(--cl-secondary);
      }
      .cl-context-tab.active {
        border-bottom-color: var(--cl-accent);
        color: var(--cl-accent);
        font-weight: 600;
      }
      .cl-context-refresh {
        margin-left: auto;
        flex: 0;
        font-size: 11px;
        padding: 4px 8px;
      }
      .cl-context-body {
        margin-top: 10px;
        border: 1px solid var(--cl-border);
        border-radius: var(--cl-radius-sm);
        background: var(--cl-surface);
        padding: 10px;
        display: flex;
        flex-direction: column;
        gap: 6px;
      }
      .cl-context-row {
        font-size: 11px;
        color: var(--cl-secondary);
        line-height: 1.4;
      }
      .cl-context-row-browser {
        border: 1px solid var(--cl-border);
        border-radius: 6px;
        background: var(--cl-bg);
        padding: 6px 8px;
      }
      .cl-context-row-browser-main {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 6px;
      }
      .cl-context-row-browser-status {
        font-size: 10px;
        text-transform: uppercase;
        font-weight: 600;
        color: var(--cl-muted);
      }
      .cl-context-row-browser-status[data-tone="success"] {
        color: var(--cl-green-text);
      }
      .cl-context-row-browser-status[data-tone="error"] {
        color: var(--cl-red);
      }
      .cl-context-row-browser-tag {
        width: fit-content;
        font-size: 9px;
        color: #4338ca;
        background: #ede9fe;
        border: 1px solid #c4b5fd;
        border-radius: 999px;
        padding: 1px 6px;
        text-transform: uppercase;
        letter-spacing: 0.03em;
        font-weight: 700;
      }
      .cl-context-meta {
        font-size: 11px;
        color: var(--cl-secondary);
        font-weight: 600;
      }
      .cl-context-warning {
        color: #b45309;
        font-weight: 600;
      }

      /* ==================== SOURCE LIST ==================== */

      .cl-source-list {
        display: flex;
        flex-direction: column;
        gap: 6px;
        margin-top: 6px;
      }
      .cl-source-row {
        border: 1px solid var(--cl-border);
        border-radius: var(--cl-radius-sm);
        background: var(--cl-bg);
        padding: 8px;
        display: flex;
        flex-direction: column;
        gap: 4px;
      }
      .cl-source-main {
        display: flex;
        align-items: center;
        gap: 6px;
        font-size: 12px;
        color: var(--cl-primary);
      }
      .cl-source-sub {
        font-size: 11px;
        color: var(--cl-muted);
      }
      .cl-source-subject {
        line-height: 1.35;
      }

      /* ==================== CONFIDENCE ==================== */

      .cl-confidence-section {
        margin: 6px 0;
        padding: 10px;
        background: var(--cl-bg);
        border: 1px solid var(--cl-border);
        border-radius: var(--cl-radius-sm);
      }
      .cl-confidence-bar {
        display: flex;
        align-items: center;
        gap: 8px;
        font-size: 12px;
      }
      .cl-confidence-label {
        color: var(--cl-muted);
        font-weight: 500;
      }
      .cl-confidence-value {
        font-weight: 700;
        font-size: 14px;
      }
      .cl-conf-high { color: var(--cl-green); }
      .cl-conf-med { color: var(--cl-amber); }
      .cl-conf-low { color: var(--cl-red); }
      .cl-confidence-threshold {
        margin-left: auto;
        color: var(--cl-muted);
        font-size: 11px;
      }
      .cl-mismatch {
        display: flex;
        align-items: center;
        gap: 6px;
        margin-top: 4px;
        padding: 4px 6px;
        border-radius: 4px;
        font-size: 11px;
      }
      .cl-mismatch-high {
        background: var(--cl-red-soft);
        border: 1px solid #fecaca;
        color: #991b1b;
      }
      .cl-mismatch-medium {
        background: var(--cl-amber-soft);
        border: 1px solid #fed7aa;
        color: #92400e;
      }
      .cl-mismatch-low {
        background: var(--cl-green-soft);
        border: 1px solid #a7e3d0;
        color: var(--cl-green-text);
      }
      .cl-mismatch-field {
        font-weight: 600;
        text-transform: capitalize;
      }

      /* Receipt / exception banners */
      .cl-receipt-notice {
        font-size: 12px;
        color: var(--cl-green-text);
        background: var(--cl-green-soft);
        border: 1px solid #a7e3d0;
        border-radius: var(--cl-radius-sm);
        padding: 8px 10px;
        margin: 2px 0 4px;
        display: flex;
        align-items: flex-start;
        gap: 8px;
        line-height: 1.4;
      }
      .cl-receipt-icon {
        font-size: 14px;
        flex-shrink: 0;
      }
      .cl-exception-reason {
        font-size: 12px;
        color: #92400e;
        background: var(--cl-amber-soft);
        border: 1px solid #fde68a;
        border-radius: var(--cl-radius-sm);
        padding: 6px 10px;
        margin: 2px 0 4px;
      }
      .cl-draft-link {
        display: inline-block;
        margin-left: 8px;
        padding: 2px 8px;
        background: #fef3c7;
        border: 1px solid var(--cl-amber);
        border-radius: 4px;
        color: #92400e;
        font-size: 11px;
        font-weight: 600;
        text-decoration: none;
        transition: background var(--cl-transition);
      }
      .cl-draft-link:hover {
        background: #fde68a;
      }

      /* Per-field confidence collapsible */
      .cl-field-conf-details {
        margin-top: 6px;
      }
      .cl-field-conf-summary {
        font-size: 11px;
        color: var(--cl-muted);
        cursor: pointer;
        user-select: none;
      }
      .cl-field-conf-grid {
        display: grid;
        grid-template-columns: 1fr auto;
        gap: 2px 8px;
        margin-top: 4px;
        font-size: 12px;
      }
      .cl-field-conf-row {
        display: contents;
      }
      .cl-field-conf-label {
        color: var(--cl-muted);
      }
      .cl-field-conf-value {
        font-weight: 600;
        text-align: right;
      }

      /* ==================== DETAILS COLLAPSIBLE ==================== */

      .cl-details {
        border-top: 1px solid var(--cl-border);
        margin-top: 4px;
        padding-top: 8px;
      }
      .cl-details summary {
        list-style: none;
        cursor: pointer;
        font-size: 12px;
        font-weight: 600;
        color: var(--cl-muted);
        transition: color var(--cl-transition);
      }
      .cl-details summary:hover {
        color: var(--cl-secondary);
      }
      .cl-details summary::-webkit-details-marker {
        display: none;
      }
      .cl-details summary:focus-visible {
        outline: 2px solid var(--cl-accent);
        outline-offset: 2px;
      }
      .cl-detail-grid {
        display: flex;
        flex-direction: column;
        gap: 6px;
        margin-top: 8px;
      }
      .cl-detail-row {
        display: flex;
        justify-content: space-between;
        gap: 8px;
        font-size: 11px;
      }
      .cl-detail-row span:first-child {
        color: var(--cl-muted);
        font-weight: 500;
      }
      .cl-detail-row span:last-child {
        color: var(--cl-primary);
        font-weight: 500;
        text-align: right;
      }
      .cl-work-surface .cl-details {
        margin-top: 2px;
      }

      /* ==================== EMPTY / THREAD META ==================== */

      .cl-empty {
        font-size: 12px;
        color: var(--cl-muted);
      }
      .cl-thread-meta {
        font-size: 12px;
        color: var(--cl-muted);
      }
      .cl-thread-main {
        font-size: 12px;
        color: var(--cl-secondary);
      }
      .cl-thread-sub {
        font-size: 12px;
        color: var(--cl-secondary);
      }

      /* ==================== PDF THUMB ==================== */

      .cl-pdf-thumb {
        width: 44px;
        height: 44px;
        border-radius: var(--cl-radius-sm);
        border: 1px solid var(--cl-border);
        object-fit: cover;
        flex-shrink: 0;
        background: var(--cl-bg);
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 10px;
        font-weight: 700;
        color: var(--cl-muted);
      }

      /* ==================== AGENT TIMELINE ==================== */

      .cl-agent-timeline {
        margin-top: 8px;
        display: flex;
        flex-direction: column;
        gap: 8px;
      }
      .cl-agent-group {
        display: flex;
        flex-direction: column;
        gap: 4px;
      }
      .cl-agent-group-title {
        font-size: 10px;
        font-weight: 700;
        color: var(--cl-muted);
        text-transform: uppercase;
        letter-spacing: 0.05em;
      }
      .cl-agent-timeline-empty {
        font-size: 12px;
        color: var(--cl-muted);
        border: 1px dashed var(--cl-border);
        border-radius: var(--cl-radius-sm);
        padding: 10px;
        background: var(--cl-bg);
      }
      .cl-agent-list {
        display: flex;
        flex-direction: column;
        gap: 4px;
      }
      .cl-agent-row {
        border: 1px solid var(--cl-border);
        border-radius: var(--cl-radius-sm);
        padding: 8px 10px;
        background: var(--cl-card);
        display: flex;
        flex-direction: column;
        gap: 4px;
      }
      .cl-agent-row-timeline {
        padding: 8px 10px;
        gap: 4px;
      }
      .cl-agent-row-timeline[data-source="audit"] {
        background: var(--cl-bg);
      }
      .cl-agent-row-timeline[data-kind="browser_fallback"] {
        border-color: #c4b5fd;
        background: #faf5ff;
      }
      .cl-agent-row-main {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
      }
      .cl-agent-tool {
        font-size: 12px;
        font-weight: 600;
        color: var(--cl-primary);
      }
      .cl-agent-status {
        font-size: 10px;
        color: var(--cl-muted);
        text-transform: uppercase;
        font-weight: 600;
        letter-spacing: 0.02em;
      }
      .cl-agent-stage-chip {
        font-size: 9px;
        color: #4338ca;
        background: #ede9fe;
        border: 1px solid #c4b5fd;
        border-radius: 999px;
        padding: 1px 6px;
        font-weight: 700;
        text-transform: uppercase;
      }
      .cl-agent-detail {
        font-size: 11px;
        color: var(--cl-muted);
        line-height: 1.4;
      }
      .cl-agent-detail-error {
        color: var(--cl-red);
      }
      .cl-agent-timeline-meta {
        display: flex;
        align-items: center;
        gap: 6px;
        flex-wrap: wrap;
      }
      .cl-agent-source {
        font-size: 9px;
        text-transform: uppercase;
        font-weight: 700;
        letter-spacing: 0.03em;
        color: var(--cl-secondary);
        background: var(--cl-bg);
        border: 1px solid var(--cl-border);
        border-radius: 999px;
        padding: 1px 7px;
      }
      .cl-agent-time {
        font-size: 11px;
        color: var(--cl-muted);
        font-variant-numeric: tabular-nums;
      }
      .cl-agent-meta {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        align-items: center;
      }
      .cl-agent-chip {
        font-size: 9px;
        text-transform: uppercase;
        border: 1px solid var(--cl-accent);
        color: var(--cl-accent);
        border-radius: 999px;
        padding: 2px 7px;
        font-weight: 700;
      }
      .cl-agent-count {
        font-size: 11px;
        color: var(--cl-muted);
      }
      .cl-agent-preview {
        margin-top: 6px;
        border: 1px dashed var(--cl-border);
        border-radius: var(--cl-radius-sm);
        padding: 8px;
        background: var(--cl-bg);
      }
      .cl-agent-preview-title {
        font-size: 11px;
        font-weight: 600;
        color: var(--cl-primary);
        margin-bottom: 4px;
      }
      .cl-agent-preview-meta {
        margin-top: 4px;
        font-size: 11px;
        color: var(--cl-secondary);
      }
      .cl-agent-warning-list {
        margin: 6px 0 0;
        padding-left: 16px;
        font-size: 11px;
        color: #b45309;
      }
      .cl-agent-actions-bar {
        margin-top: 8px;
        display: flex;
        gap: 8px;
      }
      .cl-agent-command-bar {
        margin-top: 8px;
        display: flex;
        gap: 8px;
        align-items: center;
      }
      .cl-agent-command-input {
        flex: 1;
        min-width: 0;
        border: 1px solid var(--cl-border);
        border-radius: 6px;
        padding: 6px 10px;
        font: inherit;
        font-size: 12px;
        background: var(--cl-surface);
        color: var(--cl-primary);
        transition: border-color var(--cl-transition), box-shadow var(--cl-transition);
      }
      .cl-agent-command-input:focus {
        outline: none;
        border-color: var(--cl-accent);
        box-shadow: 0 0 0 3px var(--cl-accent-soft);
      }
      .cl-agent-command-submit {
        flex: 0 0 auto;
        min-width: 56px;
      }
      .cl-agent-command-hint {
        margin-top: 6px;
        font-size: 11px;
        color: var(--cl-muted);
        line-height: 1.4;
      }
      .cl-agent-share-target-row {
        margin-top: 8px;
        display: flex;
        flex-direction: column;
        gap: 4px;
      }
      .cl-agent-share-target-label {
        font-size: 11px;
        color: var(--cl-muted);
        font-weight: 600;
      }
      .cl-agent-share-target {
        font-size: 12px;
      }
      .cl-agent-intent {
        display: inline-flex;
        align-items: center;
        justify-content: space-between;
        gap: 6px;
        text-align: left;
      }
      .cl-agent-intent-recommended {
        border-color: var(--cl-accent);
        box-shadow: inset 0 0 0 1px rgba(232, 97, 58, 0.15);
      }
      .cl-agent-intent-badge {
        font-size: 9px;
        text-transform: uppercase;
        color: #065f46;
        background: #d1fae5;
        border-radius: 999px;
        padding: 1px 6px;
        font-weight: 700;
        white-space: nowrap;
      }

      /* ==================== FALLBACK BANNER ==================== */

      .cl-fallback-banner {
        margin-top: 8px;
        border: 1px solid var(--cl-border);
        border-radius: var(--cl-radius-sm);
        background: var(--cl-bg);
        padding: 10px;
        display: flex;
        flex-direction: column;
        gap: 4px;
      }
      .cl-fallback-banner[data-tone="info"] {
        border-color: #93c5fd;
        background: #f0f4ff;
      }
      .cl-fallback-banner[data-tone="warning"] {
        border-color: #fbbf24;
        background: var(--cl-amber-soft);
      }
      .cl-fallback-banner[data-tone="error"] {
        border-color: #fca5a5;
        background: var(--cl-red-soft);
      }
      .cl-fallback-banner[data-tone="success"] {
        border-color: #a7e3d0;
        background: var(--cl-green-soft);
      }
      .cl-fallback-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 6px;
        flex-wrap: wrap;
      }
      .cl-fallback-badge {
        font-size: 9px;
        text-transform: uppercase;
        letter-spacing: 0.03em;
        font-weight: 700;
        color: var(--cl-secondary);
        border: 1px solid var(--cl-border);
        border-radius: 999px;
        padding: 1px 7px;
        background: var(--cl-surface);
      }
      .cl-fallback-stage {
        font-size: 9px;
        text-transform: uppercase;
        font-weight: 700;
        color: var(--cl-secondary);
      }
      .cl-fallback-title {
        font-size: 12px;
        font-weight: 600;
        color: var(--cl-primary);
        line-height: 1.35;
      }
      .cl-fallback-progress {
        font-size: 11px;
        color: var(--cl-secondary);
        font-weight: 600;
      }
      .cl-fallback-detail {
        font-size: 11px;
        color: var(--cl-secondary);
        line-height: 1.4;
      }
      .cl-fallback-trust-note {
        font-size: 11px;
        color: var(--cl-secondary);
        border-left: 2px solid var(--cl-border);
        padding-left: 8px;
        line-height: 1.4;
      }
      .cl-fallback-stage-list {
        display: flex;
        flex-wrap: wrap;
        gap: 4px;
      }
      .cl-fallback-stage-chip {
        font-size: 9px;
        color: var(--cl-secondary);
        background: rgba(255, 255, 255, 0.8);
        border: 1px solid var(--cl-border);
        border-radius: 999px;
        padding: 1px 6px;
      }
      .cl-fallback-meta {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        font-size: 10px;
        color: var(--cl-muted);
      }
      .cl-fallback-meta span {
        background: rgba(255, 255, 255, 0.7);
        border: 1px solid var(--cl-border);
        border-radius: 999px;
        padding: 1px 6px;
      }
      .cl-conflict-panel {
        border: 1px solid #fbbf24;
        border-radius: var(--cl-radius-sm);
        background: var(--cl-amber-soft);
        padding: 10px;
        display: flex;
        flex-direction: column;
        gap: 6px;
      }
      .cl-select {
        width: 100%;
        border: 1px solid var(--cl-border);
        border-radius: 6px;
        padding: 6px 10px;
        font: inherit;
        font-size: 12px;
        background: var(--cl-surface);
        color: var(--cl-primary);
      }

      /* ==================== AUDIT LIST ==================== */

      .cl-audit-list {
        display: flex;
        flex-direction: column;
        gap: 6px;
      }
      .cl-audit-group {
        display: flex;
        flex-direction: column;
        gap: 6px;
      }
      .cl-audit-section-title {
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 0.04em;
        text-transform: uppercase;
        color: var(--cl-muted);
      }
      .cl-audit-row {
        border: 1px solid var(--cl-border);
        border-radius: var(--cl-radius-sm);
        padding: 10px;
        background: var(--cl-card);
        display: flex;
        flex-direction: column;
        gap: 4px;
        overflow: hidden;
      }
      .cl-audit-row[data-importance="high"] {
        border-color: rgba(181, 95, 0, 0.28);
        background: linear-gradient(180deg, rgba(255, 251, 235, 0.92), var(--cl-card));
      }
      .cl-audit-row[data-severity="error"],
      .cl-audit-row[data-severity="warning"] {
        box-shadow: inset 3px 0 0 rgba(180, 83, 9, 0.22);
      }
      .cl-audit-main {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 8px;
        flex-wrap: wrap;
      }
      .cl-audit-main-copy {
        display: flex;
        flex-direction: column;
        gap: 4px;
        flex: 1;
        min-width: 0;
      }
      .cl-audit-type {
        font-size: 12px;
        font-weight: 600;
        color: var(--cl-primary);
        flex: 1;
        min-width: 0;
      }
      .cl-audit-badges {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
      }
      .cl-audit-badge {
        display: inline-flex;
        align-items: center;
        border-radius: 999px;
        padding: 2px 7px;
        font-size: 10px;
        font-weight: 700;
        letter-spacing: 0.03em;
        text-transform: uppercase;
        background: rgba(15, 23, 42, 0.05);
        color: var(--cl-secondary);
      }
      .cl-audit-badge[data-importance="high"] {
        background: rgba(234, 179, 8, 0.18);
        color: #854d0e;
      }
      .cl-audit-badge[data-importance="medium"] {
        background: rgba(37, 99, 235, 0.10);
        color: #1d4ed8;
      }
      .cl-audit-badge[data-importance="low"],
      .cl-audit-badge[data-kind="category"] {
        background: rgba(15, 23, 42, 0.05);
        color: var(--cl-muted);
      }
      .cl-audit-time {
        font-size: 11px;
        color: var(--cl-muted);
        white-space: nowrap;
        font-variant-numeric: tabular-nums;
      }
      .cl-audit-detail {
        font-size: 12px;
        color: var(--cl-secondary);
        line-height: 1.4;
        white-space: normal;
        overflow-wrap: anywhere;
        word-break: break-word;
      }
      .cl-audit-evidence,
      .cl-audit-hint,
      .cl-audit-more {
        font-size: 11px;
        line-height: 1.5;
        color: var(--cl-muted);
      }
      .cl-audit-evidence {
        display: flex;
        flex-wrap: wrap;
        gap: 4px;
      }
      .cl-audit-evidence-label {
        font-weight: 700;
        color: var(--cl-secondary);
      }
      .cl-audit-hint {
        color: var(--cl-secondary);
      }
      .cl-audit-secondary {
        border-top: 1px dashed var(--cl-border);
        padding-top: 6px;
      }
      .cl-audit-secondary-summary {
        cursor: pointer;
        color: var(--cl-secondary);
        font-size: 12px;
        font-weight: 600;
        list-style: none;
      }
      .cl-audit-secondary-summary::-webkit-details-marker {
        display: none;
      }
      .cl-audit-detail-wrap {
        display: flex;
        flex-direction: column;
        gap: 6px;
      }
      .cl-audit-detail-summary {
        list-style: none;
        cursor: pointer;
        color: var(--cl-secondary);
        font-size: 12px;
        line-height: 1.4;
        display: -webkit-box;
        -webkit-line-clamp: 2;
        -webkit-box-orient: vertical;
        overflow: hidden;
      }
      .cl-audit-detail-summary::-webkit-details-marker {
        display: none;
      }
      .cl-audit-detail-summary:focus-visible {
        outline: 2px solid var(--cl-accent);
        outline-offset: 2px;
      }

      /* ==================== PILLS / BADGES ==================== */

      .cl-pill {
        font-size: 10px;
        text-transform: uppercase;
        border-radius: 999px;
        padding: 2px 8px;
        font-weight: 600;
        white-space: nowrap;
        letter-spacing: 0.02em;
        display: inline-flex;
        align-items: center;
        gap: 4px;
      }
      .cl-pill::before {
        content: '';
        width: 6px;
        height: 6px;
        border-radius: 50%;
        background: currentColor;
      }
      .cl-pill-queue {
        font-size: 10px;
        padding: 2px 8px;
      }

      /* ==================== MOTION ==================== */

      @media (prefers-reduced-motion: reduce) {
        .cl-sidebar *,
        .cl-thread-card *,
        .cl-action-dialog * {
          animation: none !important;
          transition: none !important;
        }
        html {
          scroll-behavior: auto;
        }
      }

      /* ==================== ACTION FAB ==================== */

      .cl-fab-container {
        position: relative;
        display: flex;
        justify-content: flex-end;
        padding: 8px 16px 4px;
      }
      .cl-fab-btn {
        width: 36px;
        height: 36px;
        border-radius: 50%;
        border: none;
        background: var(--cl-accent, #00D67E);
        color: #fff;
        font-size: 20px;
        font-weight: 300;
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: center;
        box-shadow: 0 2px 8px rgba(0,0,0,0.15);
        transition: transform 0.15s, background 0.15s;
        line-height: 1;
      }
      .cl-fab-btn:hover {
        transform: scale(1.1);
        background: #00BC6E;
      }
      .cl-fab-menu {
        position: absolute;
        bottom: 48px;
        right: 16px;
        background: var(--cl-surface, #fff);
        border: 1px solid var(--cl-border, #E2E8F0);
        border-radius: 8px;
        box-shadow: 0 4px 16px rgba(0,0,0,0.12);
        overflow: hidden;
        z-index: 10;
        min-width: 180px;
      }
      .cl-fab-action {
        display: flex;
        align-items: center;
        gap: 8px;
        width: 100%;
        padding: 10px 14px;
        border: none;
        background: none;
        font-size: 12px;
        font-weight: 500;
        color: var(--cl-primary, #0F172A);
        cursor: pointer;
        text-align: left;
        transition: background 0.1s;
      }
      .cl-fab-action:hover {
        background: var(--cl-border, #f0f0ed);
      }
      .cl-fab-icon {
        font-size: 14px;
        width: 20px;
        text-align: center;
      }

      /* ==================== MINI DASHBOARD (inbox view, no thread open) ==================== */

      .cl-mini-dashboard {
        padding: 4px 0;
      }
      .cl-mini-item {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 6px 0;
        border-bottom: 1px solid var(--cl-border);
        cursor: pointer;
        font-size: 12px;
        transition: background 0.1s;
      }
      .cl-mini-item:hover {
        background: var(--cl-surface);
        border-radius: 4px;
        padding-left: 4px;
        margin-left: -4px;
      }
      .cl-mini-item:last-child { border-bottom: none; }
      .cl-mini-vendor {
        flex: 1;
        font-weight: 500;
        color: var(--cl-primary);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        max-width: 120px;
      }
      .cl-mini-amount {
        font-weight: 600;
        font-variant-numeric: tabular-nums;
        color: var(--cl-primary);
        white-space: nowrap;
      }
      .cl-mini-state {
        font-size: 10px;
        font-weight: 600;
        padding: 1px 6px;
        border-radius: 999px;
        background: var(--cl-border);
        color: var(--cl-secondary);
        white-space: nowrap;
        text-transform: uppercase;
        letter-spacing: 0.02em;
      }

      /* ==================== GEAR BUTTON ==================== */

      .cl-gear-btn {
        background: none;
        border: none;
        cursor: pointer;
        font-size: 16px;
        padding: 4px 6px;
        color: var(--cl-muted);
        border-radius: 4px;
        transition: color 0.15s, background 0.15s;
        line-height: 1;
      }
      .cl-gear-btn:hover {
        color: var(--cl-primary);
        background: var(--cl-border);
      }

      /* ==================== QUICK SETTINGS ==================== */

      .cl-settings-panel {
        padding: 12px 16px;
        border-bottom: 1px solid var(--cl-border);
      }
      .cl-settings-row {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
        margin-bottom: 8px;
        font-size: 12px;
        color: var(--cl-secondary);
      }
      .cl-settings-row label {
        font-weight: 500;
        display: flex;
        align-items: center;
        gap: 6px;
      }
      .cl-settings-row select,
      .cl-settings-row input[type="number"] {
        font-size: 12px;
        padding: 4px 8px;
        border: 1px solid var(--cl-border);
        border-radius: 4px;
        background: var(--cl-bg);
        color: var(--cl-primary);
        max-width: 140px;
      }
      .cl-settings-row input[type="checkbox"] {
        margin: 0;
      }
      .cl-settings-row a {
        color: var(--cl-accent);
        text-decoration: none;
      }
      .cl-settings-row a:hover {
        text-decoration: underline;
      }

      /* ==================== SIDEBAR ONBOARDING ==================== */

      .cl-onboarding-panel {
        padding: 12px 16px;
        border-bottom: 1px solid var(--cl-border);
        background: var(--cl-surface);
      }
      .cl-onboarding-title {
        font-size: 13px;
        font-weight: 600;
        color: var(--cl-primary);
        margin-bottom: 10px;
      }
      .cl-onboarding-step {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 6px 0;
        font-size: 12px;
        color: var(--cl-secondary);
      }
      .cl-onboarding-step.done {
        color: var(--cl-muted);
      }
      .cl-step-icon {
        width: 20px;
        height: 20px;
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 11px;
        font-weight: 600;
        flex-shrink: 0;
        background: var(--cl-border);
        color: var(--cl-secondary);
      }
      .cl-onboarding-step.done .cl-step-icon {
        background: #ECFDF5;
        color: #059669;
      }
      .cl-step-status {
        margin-left: auto;
        font-size: 11px;
        color: #059669;
        font-weight: 500;
      }
      .cl-onboarding-step .cl-btn-small {
        margin-left: auto;
        font-size: 11px;
        padding: 3px 10px;
      }
`;

/* State pill CSS — filled background badges (matching console activity badges) */

export const STATE_PILL_CSS = `
  .cl-pill-received { color: #4b5563; background: #f3f4f6; }
  .cl-pill-received::before { background: #6b7280; }
  .cl-pill-validated { color: #1e40af; background: #dbeafe; }
  .cl-pill-validated::before { background: #3b82f6; }
  .cl-pill-needs-info { color: #92400e; background: #fef9ee; border: 1px solid #fde68a; }
  .cl-pill-needs-info::before { background: #d97706; }
  .cl-pill-needs-approval { color: #92400e; background: #fff7ed; border: 1px solid #fed7aa; }
  .cl-pill-needs-approval::before { background: #ea580c; }
  .cl-pill-approved { color: #059669; background: #ECFDF5; }
  .cl-pill-approved::before { background: #10B981; }
  .cl-pill-ready-to-post { color: #059669; background: #ECFDF5; }
  .cl-pill-ready-to-post::before { background: #10B981; }
  .cl-pill-posted-to-erp { color: #5b21b6; background: #ede9fe; }
  .cl-pill-posted-to-erp::before { background: #7c3aed; }
  .cl-pill-closed { color: #059669; background: #ECFDF5; }
  .cl-pill-closed::before { background: #10B981; }
  .cl-pill-rejected { color: #991b1b; background: #fef2f2; border: 1px solid #fecaca; }
  .cl-pill-rejected::before { background: #dc2626; }
  .cl-pill-failed-post { color: #991b1b; background: #fef2f2; border: 1px solid #fecaca; }
  .cl-pill-failed-post::before { background: #dc2626; }

  .cl-warning-text { color: #b45309; }

  .cl-approval-progress {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 12px;
    font-weight: 500;
    color: #ea580c;
    padding: 6px 0;
  }
  .cl-approval-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: #ea580c;
    animation: cl-pulse 1.5s ease-in-out infinite;
  }
  @keyframes cl-pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.3; }
  }
  .cl-pdf-thumb {
    width: 44px;
    height: 44px;
    border-radius: var(--cl-radius-sm, 8px);
    border: 1px solid var(--cl-border, #E2E8F0);
    object-fit: cover;
    flex-shrink: 0;
    background: var(--cl-bg, #faf9f7);
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 10px;
    font-weight: 700;
    color: var(--cl-muted, #8c8c8c);
  }
  .cl-thread-header-with-thumb {
    display: flex;
    align-items: center;
    gap: 10px;
  }
`;
