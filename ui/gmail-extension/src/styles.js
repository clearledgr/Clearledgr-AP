/** Sidebar CSS — injected into Gmail DOM via <style> block */

export const SIDEBAR_CSS = `
      .cl-sidebar {
        --cl-bg: #ffffff;
        --cl-surface: #f8fafc;
        --cl-card: #ffffff;
        --cl-border: #e5e7eb;
        --cl-text: #111827;
        --cl-muted: #6b7280;
        --cl-accent: #0f766e;
        --cl-accent-soft: #ecfdf5;
        font-family: 'Google Sans', Roboto, sans-serif;
        color: var(--cl-text);
        padding: 12px;
        display: flex;
        flex-direction: column;
        gap: 10px;
        height: 100%;
        background: var(--cl-bg);
        position: relative;
      }
      .cl-header {
        display: flex;
        flex-direction: column;
        gap: 2px;
        margin-bottom: 2px;
      }
      .cl-title {
        font-size: 14px;
        font-weight: 600;
        display: flex;
        align-items: center;
        gap: 6px;
      }
      .cl-logo {
        width: 16px;
        height: 16px;
        display: inline-block;
      }
      .cl-subtitle {
        font-size: 11px;
        color: var(--cl-muted);
      }
      .cl-toast {
        font-size: 11px;
        color: var(--cl-text);
        background: #f3f4f6;
        border: 1px solid var(--cl-border);
        border-radius: 6px;
        padding: 5px 8px;
        display: none;
      }
      .cl-toast[data-tone="error"] {
        color: #991b1b;
        border-color: #fecaca;
        background: #fef2f2;
      }
      .cl-action-dialog {
        position: absolute;
        inset: 0;
        display: none;
        align-items: center;
        justify-content: center;
        z-index: 24;
        background: rgba(15, 23, 42, 0.5);
        padding: 12px;
      }
      .cl-action-dialog-card {
        width: 100%;
        max-width: 320px;
        border-radius: 10px;
        border: 1px solid var(--cl-border);
        background: #ffffff;
        box-shadow: 0 12px 28px rgba(15, 23, 42, 0.2);
        padding: 12px;
        display: flex;
        flex-direction: column;
        gap: 8px;
      }
      .cl-action-dialog-title {
        font-size: 12px;
        font-weight: 700;
        color: var(--cl-text);
      }
      .cl-action-dialog-label {
        font-size: 11px;
        color: var(--cl-muted);
      }
      .cl-action-dialog-chips {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
      }
      .cl-action-chip {
        border: 1px solid var(--cl-border);
        background: #f8fafc;
        color: #334155;
        border-radius: 999px;
        font-size: 10px;
        font-weight: 600;
        padding: 4px 8px;
        cursor: pointer;
      }
      .cl-action-chip:hover {
        border-color: var(--cl-accent);
        color: var(--cl-accent);
      }
      .cl-action-chip:focus-visible {
        outline: 2px solid #0f766e;
        outline-offset: 2px;
      }
      .cl-action-dialog-input {
        width: 100%;
        min-height: 34px;
        border-radius: 8px;
        border: 1px solid var(--cl-border);
        padding: 8px 10px;
        font-size: 12px;
        color: var(--cl-text);
        background: #ffffff;
      }
      .cl-action-dialog-input:focus {
        border-color: var(--cl-accent);
        outline: none;
        box-shadow: 0 0 0 2px rgba(15, 118, 110, 0.16);
      }
      .cl-action-dialog-hint {
        font-size: 10px;
        color: var(--cl-muted);
        line-height: 1.3;
      }
      .cl-action-dialog-actions {
        display: flex;
        justify-content: flex-end;
        gap: 8px;
      }
      .cl-section {
        background: var(--cl-surface);
        border: 1px solid var(--cl-border);
        border-radius: 10px;
        padding: 10px;
        display: flex;
        flex-direction: column;
        gap: 8px;
      }
      .cl-section-title {
        font-size: 11px;
        font-weight: 600;
        color: var(--cl-muted);
        text-transform: uppercase;
        letter-spacing: 0.04em;
      }
      .cl-thread-card {
        background: var(--cl-card);
        border: 1px solid var(--cl-border);
        border-radius: 8px;
        padding: 10px;
        display: flex;
        flex-direction: column;
        gap: 5px;
      }
      .cl-navigator {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
        margin-bottom: 2px;
      }
      .cl-nav-buttons {
        display: flex;
        gap: 6px;
      }
      .cl-nav-btn {
        min-width: 56px;
      }
      .cl-thread-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
      }
      .cl-thread-title {
        font-weight: 600;
        font-size: 13px;
      }
      .cl-thread-main {
        font-size: 12px;
        color: var(--cl-text);
      }
      .cl-thread-sub {
        font-size: 11px;
        color: #4b5563;
      }
      .cl-operator-brief {
        border: 1px solid #d1d5db;
        border-radius: 8px;
        background: #f8fafc;
        display: flex;
        flex-direction: column;
      }
      .cl-operator-brief[data-tone="warning"] {
        border-color: #fcd34d;
        background: #fffbeb;
      }
      .cl-operator-brief[data-tone="good"] {
        border-color: #86efac;
        background: #f0fdf4;
      }
      .cl-operator-brief-row {
        display: flex;
        flex-direction: column;
        gap: 2px;
        padding: 7px 8px;
      }
      .cl-operator-brief-row + .cl-operator-brief-row {
        border-top: 1px dashed #d1d5db;
      }
      .cl-operator-brief-label {
        font-size: 10px;
        font-weight: 700;
        color: #334155;
        text-transform: uppercase;
        letter-spacing: 0.03em;
      }
      .cl-operator-brief-text {
        font-size: 11px;
        color: #1f2937;
        line-height: 1.35;
      }
      .cl-operator-brief-outcome {
        margin-top: 1px;
        font-size: 10px;
        color: #475569;
      }
      .cl-decision-banner {
        border: 1px solid var(--cl-border);
        border-radius: 8px;
        padding: 8px;
        background: #ffffff;
      }
      .cl-decision-title {
        font-size: 11px;
        font-weight: 700;
        color: #111827;
      }
      .cl-decision-detail {
        margin-top: 2px;
        font-size: 10px;
        color: #4b5563;
      }
      .cl-decision-good {
        border-color: #86efac;
        background: #f0fdf4;
      }
      .cl-decision-warning {
        border-color: #fcd34d;
        background: #fffbeb;
      }
      .cl-decision-neutral {
        border-color: #d1d5db;
        background: #f9fafb;
      }
      .cl-risk-row {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
      }
      .cl-risk-chip {
        font-size: 10px;
        border: 1px solid #d1d5db;
        border-radius: 999px;
        padding: 2px 8px;
        color: #374151;
        background: #f9fafb;
      }
      .cl-risk-chip-warning {
        border-color: #f59e0b;
        color: #92400e;
        background: #fffbeb;
      }
      .cl-agent-reasoning-banner {
        margin: 6px 0 2px;
        padding: 7px 10px;
        background: #f0f9ff;
        border-left: 3px solid #3b82f6;
        border-radius: 4px;
        font-size: 11px;
        color: #1e3a5f;
        line-height: 1.45;
      }
      .cl-agent-label {
        font-weight: 600;
        color: #1d4ed8;
        margin-right: 3px;
      }
      .cl-agent-risks {
        margin-top: 4px;
        display: flex;
        flex-wrap: wrap;
        gap: 4px;
      }
      .cl-discount-banner {
        margin: 6px 0 2px;
        padding: 7px 10px;
        background: #f0fdf4;
        border-left: 3px solid #16a34a;
        border-radius: 4px;
        font-size: 11px;
        color: #14532d;
        line-height: 1.45;
      }
      .cl-discount-label {
        font-weight: 600;
        color: #15803d;
        margin-right: 3px;
      }
      .cl-needs-info-banner {
        margin: 6px 0 2px;
        padding: 7px 10px;
        background: #fffbeb;
        border-left: 3px solid #f59e0b;
        border-radius: 4px;
        font-size: 11px;
        color: #78350f;
        line-height: 1.45;
      }
      .cl-needs-info-label {
        font-weight: 600;
        color: #b45309;
        margin-right: 3px;
      }
      .cl-needs-info-meta {
        margin-top: 4px;
        color: #92400e;
      }
      .cl-draft-link {
        display: inline-block;
        margin-left: 8px;
        padding: 2px 7px;
        background: #fef3c7;
        border: 1px solid #f59e0b;
        border-radius: 3px;
        color: #92400e;
        font-size: 10px;
        font-weight: 600;
        text-decoration: none;
      }
      .cl-draft-link:hover {
        background: #fde68a;
      }
      .cl-thread-meta {
        font-size: 11px;
        color: var(--cl-muted);
      }
      .cl-source-subject {
        line-height: 1.35;
      }
      .cl-confidence-section {
        margin: 6px 0;
        padding: 8px;
        background: #f9fafb;
        border: 1px solid var(--cl-border);
        border-radius: 8px;
      }
      .cl-confidence-bar {
        display: flex;
        align-items: center;
        gap: 8px;
        font-size: 11px;
      }
      .cl-confidence-label {
        color: var(--cl-muted);
        font-weight: 500;
      }
      .cl-confidence-value {
        font-weight: 600;
        font-size: 13px;
      }
      .cl-conf-high { color: #16a34a; }
      .cl-conf-med { color: #ca8a04; }
      .cl-conf-low { color: #dc2626; }
      .cl-confidence-threshold {
        margin-left: auto;
        color: var(--cl-muted);
        font-size: 10px;
      }
      .cl-mismatch {
        display: flex;
        align-items: center;
        gap: 6px;
        margin-top: 4px;
        padding: 4px 6px;
        border-radius: 4px;
        font-size: 10px;
      }
      .cl-mismatch-high {
        background: #fef2f2;
        border: 1px solid #fecaca;
        color: #991b1b;
      }
      .cl-mismatch-medium {
        background: #fffbeb;
        border: 1px solid #fed7aa;
        color: #92400e;
      }
      .cl-mismatch-low {
        background: #f0fdf4;
        border: 1px solid #bbf7d0;
        color: #166534;
      }
      .cl-mismatch-field {
        font-weight: 600;
        text-transform: capitalize;
      }
      /* Receipt notice banner */
      .cl-receipt-notice {
        font-size: 11px;
        color: #15803d;
        background: #f0fdf4;
        border: 1px solid #bbf7d0;
        border-radius: 6px;
        padding: 6px 8px;
        margin: 2px 0 4px;
        display: flex;
        align-items: flex-start;
        gap: 6px;
        line-height: 1.4;
      }
      .cl-receipt-icon {
        font-size: 13px;
        flex-shrink: 0;
      }
      /* Exception root-cause one-liner */
      .cl-exception-reason {
        font-size: 11px;
        color: #b45309;
        background: #fffbeb;
        border: 1px solid #fde68a;
        border-radius: 6px;
        padding: 4px 8px;
        margin: 2px 0 4px;
      }
      /* Per-field confidence collapsible */
      .cl-field-conf-details {
        margin-top: 6px;
      }
      .cl-field-conf-summary {
        font-size: 10px;
        color: var(--cl-muted);
        cursor: pointer;
        user-select: none;
      }
      .cl-field-conf-grid {
        display: grid;
        grid-template-columns: 1fr auto;
        gap: 2px 8px;
        margin-top: 4px;
        font-size: 11px;
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
      .cl-btn-approve {
        background: #16a34a !important;
        color: white !important;
        border-color: #16a34a !important;
      }
      .cl-btn-review {
        background: #ca8a04 !important;
        color: white !important;
        border-color: #ca8a04 !important;
      }
      .cl-btn-small {
        font-size: 10px !important;
        padding: 3px 6px !important;
      }
      .cl-thread-actions {
        display: flex;
        gap: 6px;
        margin-top: 4px;
        flex-wrap: wrap;
      }
      .cl-primary-cta {
        margin-top: 6px;
        width: 100%;
      }
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
      .cl-work-surface .cl-details {
        margin-top: 2px;
      }
      .cl-source-list {
        display: flex;
        flex-direction: column;
        gap: 6px;
        margin-top: 6px;
      }
      .cl-source-row {
        border: 1px solid var(--cl-border);
        border-radius: 8px;
        background: #f9fafb;
        padding: 7px;
        display: flex;
        flex-direction: column;
        gap: 4px;
      }
      .cl-source-main {
        display: flex;
        align-items: center;
        gap: 6px;
        font-size: 11px;
        color: var(--cl-text);
      }
      .cl-source-sub {
        font-size: 10px;
        color: var(--cl-muted);
      }
      .cl-context-tabs {
        margin-top: 8px;
        display: flex;
        gap: 6px;
        flex-wrap: wrap;
      }
      .cl-context-tab {
        border: 1px solid var(--cl-border);
        border-radius: 999px;
        background: #ffffff;
        color: #374151;
        padding: 4px 8px;
        font-size: 10px;
        cursor: pointer;
      }
      .cl-context-tab.active {
        border-color: #0f766e;
        color: #0f766e;
        background: #ecfdf5;
      }
      .cl-context-refresh {
        margin-left: auto;
        flex: 0;
        font-size: 10px;
        padding: 4px 8px;
      }
      .cl-context-body {
        margin-top: 8px;
        border: 1px solid var(--cl-border);
        border-radius: 8px;
        background: #f9fafb;
        padding: 8px;
        display: flex;
        flex-direction: column;
        gap: 6px;
      }
      .cl-context-row {
        font-size: 10px;
        color: #374151;
        line-height: 1.35;
      }
      .cl-context-row-browser {
        border: 1px solid #e5e7eb;
        border-radius: 6px;
        background: #ffffff;
        padding: 6px;
      }
      .cl-context-row-browser-main {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 6px;
      }
      .cl-context-row-browser-status {
        font-size: 9px;
        text-transform: uppercase;
        color: #475569;
      }
      .cl-context-row-browser-status[data-tone="success"] {
        color: #166534;
      }
      .cl-context-row-browser-status[data-tone="error"] {
        color: #b91c1c;
      }
      .cl-context-row-browser-tag {
        width: fit-content;
        font-size: 9px;
        color: #1d4ed8;
        background: #dbeafe;
        border: 1px solid #bfdbfe;
        border-radius: 999px;
        padding: 1px 5px;
        text-transform: uppercase;
        letter-spacing: 0.02em;
        font-weight: 600;
      }
      .cl-context-meta {
        font-size: 10px;
        color: #4b5563;
        font-weight: 600;
      }
      .cl-context-warning {
        color: #b45309;
        font-weight: 600;
      }
      .cl-fallback-banner {
        margin-top: 8px;
        border: 1px solid #cbd5e1;
        border-radius: 8px;
        background: #f8fafc;
        padding: 8px;
        display: flex;
        flex-direction: column;
        gap: 4px;
      }
      .cl-fallback-banner[data-tone="info"] {
        border-color: #93c5fd;
        background: #eff6ff;
      }
      .cl-fallback-banner[data-tone="warning"] {
        border-color: #fbbf24;
        background: #fffbeb;
      }
      .cl-fallback-banner[data-tone="error"] {
        border-color: #fca5a5;
        background: #fef2f2;
      }
      .cl-fallback-banner[data-tone="success"] {
        border-color: #86efac;
        background: #f0fdf4;
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
        letter-spacing: 0.02em;
        font-weight: 700;
        color: #334155;
        border: 1px solid #cbd5e1;
        border-radius: 999px;
        padding: 1px 6px;
        background: #ffffff;
      }
      .cl-fallback-stage {
        font-size: 9px;
        text-transform: uppercase;
        font-weight: 700;
        color: #475569;
      }
      .cl-fallback-title {
        font-size: 11px;
        font-weight: 600;
        color: #1f2937;
        line-height: 1.3;
      }
      .cl-fallback-progress {
        font-size: 10px;
        color: #334155;
        font-weight: 600;
      }
      .cl-fallback-detail {
        font-size: 10px;
        color: #374151;
        line-height: 1.35;
      }
      .cl-fallback-trust-note {
        font-size: 10px;
        color: #334155;
        border-left: 2px solid #cbd5e1;
        padding-left: 6px;
        line-height: 1.35;
      }
      .cl-fallback-stage-list {
        display: flex;
        flex-wrap: wrap;
        gap: 4px;
      }
      .cl-fallback-stage-chip {
        font-size: 9px;
        color: #334155;
        background: rgba(255, 255, 255, 0.8);
        border: 1px solid #cbd5e1;
        border-radius: 999px;
        padding: 1px 5px;
      }
      .cl-fallback-meta {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        font-size: 9px;
        color: #475569;
      }
      .cl-fallback-meta span {
        background: rgba(255, 255, 255, 0.7);
        border: 1px solid #e2e8f0;
        border-radius: 999px;
        padding: 1px 5px;
      }
      .cl-conflict-panel {
        border: 1px solid #fbbf24;
        border-radius: 8px;
        background: #fffbeb;
        padding: 8px;
        display: flex;
        flex-direction: column;
        gap: 6px;
      }
      .cl-select {
        width: 100%;
        border: 1px solid var(--cl-border);
        border-radius: 6px;
        padding: 6px;
        font-size: 11px;
        background: #ffffff;
        color: var(--cl-text);
      }
      .cl-btn {
        flex: 1;
        border-radius: 6px;
        border: 1px solid #059669;
        background: #059669;
        color: #ffffff;
        font-size: 11px;
        padding: 6px 8px;
        cursor: pointer;
      }
      .cl-btn:disabled {
        background: #e5e7eb;
        border-color: #e5e7eb;
        color: #9ca3af;
        cursor: not-allowed;
      }
      .cl-btn:focus-visible {
        outline: 2px solid #0f766e;
        outline-offset: 2px;
      }
      .cl-btn-secondary {
        background: #ffffff;
        color: var(--cl-text);
        border-color: #d1d5db;
      }
      .cl-pill {
        font-size: 10px;
        text-transform: uppercase;
        border: 1px solid currentColor;
        border-radius: 999px;
        padding: 2px 7px;
        font-weight: 600;
        white-space: nowrap;
      }
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
        font-size: 11px;
        color: #6b7280;
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
        border-radius: 8px;
        padding: 8px;
        background: var(--cl-card);
        display: flex;
        flex-direction: column;
        gap: 3px;
        cursor: pointer;
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
      .cl-pill-queue {
        font-size: 9px;
        padding: 1px 6px;
      }
      .cl-queue-row-meta {
        display: flex;
        align-items: baseline;
        gap: 8px;
        flex-wrap: wrap;
      }
      .cl-queue-vendor {
        font-size: 12px;
        font-weight: 600;
        color: var(--cl-text);
      }
      .cl-queue-amount {
        font-size: 11px;
        color: #374151;
      }
      .cl-queue-subject {
        font-size: 11px;
        color: var(--cl-text);
        line-height: 1.35;
      }
      .cl-queue-meta {
        font-size: 10px;
        color: var(--cl-muted);
      }
      .cl-empty {
        font-size: 11px;
        color: var(--cl-muted);
      }
      .cl-audit-list {
        display: flex;
        flex-direction: column;
        gap: 8px;
      }
      .cl-audit-row {
        border: 1px solid var(--cl-border);
        border-radius: 8px;
        padding: 10px;
        background: var(--cl-card);
        display: flex;
        flex-direction: column;
        gap: 6px;
        overflow: hidden;
      }
      .cl-audit-main {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 8px;
        flex-wrap: wrap;
      }
      .cl-audit-type {
        font-size: 12px;
        font-weight: 600;
        color: var(--cl-text);
        flex: 1;
        min-width: 0;
      }
      .cl-audit-time {
        font-size: 11px;
        color: var(--cl-muted);
        white-space: nowrap;
      }
      .cl-audit-detail {
        font-size: 12px;
        color: #4b5563;
        line-height: 1.4;
        white-space: normal;
        overflow-wrap: anywhere;
        word-break: break-word;
      }
      .cl-audit-detail-wrap {
        display: flex;
        flex-direction: column;
        gap: 6px;
      }
      .cl-audit-detail-summary {
        list-style: none;
        cursor: pointer;
        color: #4b5563;
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
        outline: 2px solid #0f766e;
        outline-offset: 2px;
      }
      .cl-scan-status {
        font-size: 11px;
        color: var(--cl-muted);
      }
      .cl-scan-status[data-tone="error"] {
        color: #b91c1c;
      }
      .cl-inline-actions {
        display: none;
        margin-top: 8px;
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
        border: 1px solid #0f766e;
        border-radius: 999px;
        padding: 2px 7px;
        font-weight: 600;
      }
      .cl-agent-count {
        font-size: 10px;
        color: var(--cl-muted);
      }
      .cl-agent-list {
        display: flex;
        flex-direction: column;
        gap: 5px;
        margin-top: 8px;
      }
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
        color: #334155;
        text-transform: uppercase;
        letter-spacing: 0.03em;
      }
      .cl-agent-timeline-empty {
        font-size: 10px;
        color: var(--cl-muted);
        border: 1px dashed var(--cl-border);
        border-radius: 8px;
        padding: 8px;
        background: #f8fafc;
      }
      .cl-agent-row {
        border: 1px solid var(--cl-border);
        border-radius: 8px;
        padding: 8px;
        background: var(--cl-card);
        display: flex;
        flex-direction: column;
        gap: 5px;
      }
      .cl-agent-row-timeline {
        padding: 7px;
        gap: 4px;
      }
      .cl-agent-row-timeline[data-source="audit"] {
        background: #f8fafc;
      }
      .cl-agent-row-timeline[data-kind="browser_fallback"] {
        border-color: #bfdbfe;
        background: #f8fbff;
      }
      .cl-agent-row-main {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
      }
      .cl-agent-tool {
        font-size: 11px;
        font-weight: 600;
        color: var(--cl-text);
      }
      .cl-agent-status {
        font-size: 9px;
        color: var(--cl-muted);
        text-transform: uppercase;
      }
      .cl-agent-stage-chip {
        font-size: 9px;
        color: #1d4ed8;
        background: #dbeafe;
        border: 1px solid #bfdbfe;
        border-radius: 999px;
        padding: 1px 5px;
        font-weight: 700;
        text-transform: uppercase;
      }
      .cl-agent-detail {
        font-size: 10px;
        color: var(--cl-muted);
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
        color: #334155;
        background: #e2e8f0;
        border-radius: 999px;
        padding: 1px 6px;
        font-weight: 600;
      }
      .cl-agent-time {
        font-size: 10px;
        color: var(--cl-muted);
      }
      .cl-agent-preview {
        margin-top: 6px;
        border: 1px dashed #d1d5db;
        border-radius: 8px;
        padding: 7px;
        background: #f8fafc;
      }
      .cl-agent-preview-title {
        font-size: 10px;
        font-weight: 600;
        color: #1f2937;
        margin-bottom: 4px;
      }
      .cl-agent-preview-meta {
        margin-top: 4px;
        font-size: 10px;
        color: #4b5563;
      }
      .cl-agent-warning-list {
        margin: 6px 0 0;
        padding-left: 16px;
        font-size: 10px;
        color: #b45309;
      }
      .cl-agent-detail-error {
        color: #b91c1c;
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
        padding: 6px 8px;
        font-size: 11px;
        background: #ffffff;
        color: var(--cl-text);
      }
      .cl-agent-command-input:focus {
        outline: 2px solid rgba(15, 118, 110, 0.18);
        border-color: #0f766e;
      }
      .cl-agent-command-submit {
        flex: 0 0 auto;
        min-width: 56px;
      }
      .cl-agent-command-hint {
        margin-top: 6px;
        font-size: 10px;
        color: var(--cl-muted);
        line-height: 1.35;
      }
      .cl-agent-share-target-row {
        margin-top: 8px;
        display: flex;
        flex-direction: column;
        gap: 4px;
      }
      .cl-agent-share-target-label {
        font-size: 10px;
        color: var(--cl-muted);
        font-weight: 600;
      }
      .cl-agent-share-target {
        font-size: 11px;
      }
      .cl-agent-intent {
        display: inline-flex;
        align-items: center;
        justify-content: space-between;
        gap: 6px;
        text-align: left;
      }
      .cl-agent-intent-recommended {
        border-color: #0f766e;
        box-shadow: inset 0 0 0 1px rgba(15, 118, 110, 0.15);
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
      .cl-details {
        border-top: 1px dashed var(--cl-border);
        margin-top: 4px;
        padding-top: 4px;
      }
      .cl-details summary {
        list-style: none;
        cursor: pointer;
        font-size: 12px;
        font-weight: 500;
        color: var(--cl-muted);
      }
      .cl-details summary::-webkit-details-marker {
        display: none;
      }
      .cl-details summary:focus-visible {
        outline: 2px solid #0f766e;
        outline-offset: 2px;
      }
      .cl-detail-grid {
        display: flex;
        flex-direction: column;
        gap: 4px;
        margin-top: 6px;
      }
      .cl-detail-row {
        display: flex;
        justify-content: space-between;
        gap: 8px;
        font-size: 10px;
        color: var(--cl-muted);
      }
      .cl-detail-row span:last-child {
        color: var(--cl-text);
      }
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
`;
/* Add state pill CSS classes (replaces inline style) */

export const STATE_PILL_CSS = `
  .cl-pill-received { color: #2563eb; border-color: #2563eb; }
  .cl-pill-validated { color: #0f766e; border-color: #0f766e; }
  .cl-pill-needs-info { color: #b45309; border-color: #b45309; }
  .cl-pill-needs-approval { color: #c2410c; border-color: #c2410c; }
  .cl-pill-approved { color: #15803d; border-color: #15803d; }
  .cl-pill-ready-to-post { color: #0f766e; border-color: #0f766e; }
  .cl-pill-posted-to-erp { color: #7c3aed; border-color: #7c3aed; }
  .cl-pill-closed { color: #0f766e; border-color: #0f766e; }
  .cl-pill-rejected { color: #b91c1c; border-color: #b91c1c; }
  .cl-pill-failed-post { color: #b91c1c; border-color: #b91c1c; }
`;
