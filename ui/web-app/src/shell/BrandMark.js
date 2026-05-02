import { html } from '../utils/htm.js';

/**
 * The Clearledgr brand mark — navy rounded square with two mint
 * vertical bars (the ledger icon, per DESIGN.md §Brand Identity).
 *
 * Inline SVG so it renders without a network round-trip, scales
 * cleanly at any size, and inherits CSS sizing. Used by the sidebar
 * wordmark, the auth-card brand, and the invite-accept brand.
 *
 * Props:
 *   size  — pixel size of the square (defaults to 22).
 *   class — additional CSS class for layout / spacing.
 */
export function BrandMark({ size = 22, class: className = '' }) {
  return html`
    <svg
      class=${`cl-brand-mark ${className}`.trim()}
      width=${size}
      height=${size}
      viewBox="0 0 24 24"
      fill="none"
      role="img"
      aria-label="Clearledgr"
      xmlns="http://www.w3.org/2000/svg">
      <rect width="24" height="24" rx="5.5" fill="#0A1628" />
      <rect x="7.25"  y="6.5" width="3.25" height="11" rx="0.9" fill="#00D67E" />
      <rect x="13.5"  y="6.5" width="3.25" height="11" rx="0.9" fill="#00D67E" />
    </svg>
  `;
}
