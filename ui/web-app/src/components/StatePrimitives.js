/**
 * State primitives — reusable empty / loading / error fallbacks.
 *
 * Every page that fetches data should reach for these instead of
 * rolling its own "Loading…" string. Consistent UX and one place to
 * tune the visual treatment.
 */

import { html } from '../utils/htm.js';

/**
 * EmptyState — shown when a fetch succeeds but returns no data.
 *
 *   <EmptyState
 *     title="No invoices yet"
 *     description="Connect Gmail to start receiving invoices."
 *     ctaLabel="Connect Gmail →"
 *     onCtaClick=${goToConnections}
 *   />
 *
 * For an icon-less single-line version, omit description.
 */
export function EmptyState({ title, description, ctaLabel, onCtaClick, dense = false }) {
  return html`
    <div class=${`cl-state cl-state--empty${dense ? ' cl-state--dense' : ''}`}>
      ${title ? html`<div class="cl-state-title">${title}</div>` : null}
      ${description ? html`<div class="cl-state-body">${description}</div>` : null}
      ${ctaLabel && onCtaClick ? html`
        <button class="btn-primary btn-sm" onClick=${onCtaClick}>${ctaLabel}</button>
      ` : null}
    </div>
  `;
}

/**
 * LoadingSkeleton — placeholder rows shown while a fetch is in flight.
 *
 *   <LoadingSkeleton rows=${3} />
 *
 * Use rows=${1} for a single-line "loading…" replacement; for a list
 * of items, set rows to roughly the expected count.
 */
export function LoadingSkeleton({ rows = 3, label = 'Loading…' }) {
  return html`
    <div class="cl-state cl-state--loading" role="status" aria-label=${label}>
      ${Array.from({ length: rows }).map((_, i) => html`
        <div key=${i} class="cl-skeleton-row" aria-hidden="true"></div>
      `)}
      <span class="cl-state-sr">${label}</span>
    </div>
  `;
}

/**
 * ErrorRetry — shown when a fetch fails. Includes a Retry action so
 * the user can re-attempt without a full page reload.
 *
 *   <ErrorRetry
 *     message="Couldn't load vendor list."
 *     detail=${err.message}
 *     onRetry=${load}
 *   />
 */
export function ErrorRetry({ message, detail, onRetry, retryLabel = 'Try again' }) {
  return html`
    <div class="cl-state cl-state--error">
      <div class="cl-state-title">${message || 'Something went wrong.'}</div>
      ${detail ? html`<div class="cl-state-body">${detail}</div>` : null}
      ${onRetry ? html`
        <button class="btn-secondary btn-sm" onClick=${onRetry}>${retryLabel}</button>
      ` : null}
    </div>
  `;
}
