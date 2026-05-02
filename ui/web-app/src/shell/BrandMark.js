import { html } from '../utils/htm.js';

/**
 * The Solden brand mark — the canonical S-mark, inlined directly
 * from the brand-kit SVG (`public/solden-mark.svg`). Two stylized
 * angular slabs forming the silhouette of an "S".
 *
 * Fill is `currentColor`, so the mark inherits its color from the
 * CSS `color` property of its parent. This lets a single component
 * render in any tone without per-variant assets:
 *
 *   tone="primary"   → navy (--cl-navy) on light surfaces
 *   tone="on-dark"   → white on dark / teal surfaces
 *   tone="accent"    → teal (--cl-teal-500), for special hero use
 *
 * Path data is the literal export from the brand kit. Only the
 * fill color and viewBox crop are parameterized.
 */
const TONE_COLORS = {
  primary: 'var(--cl-navy)',
  'on-dark': '#FFFFFF',
  accent: 'var(--cl-teal-500)',
};

export function BrandMark({ size = 24, tone = 'primary', class: className = '' }) {
  const color = TONE_COLORS[tone] || TONE_COLORS.primary;
  return html`
    <svg
      class=${`cl-brand-mark ${className}`.trim()}
      width=${size}
      height=${size}
      viewBox="326 365 165 180"
      fill="currentColor"
      preserveAspectRatio="xMidYMid meet"
      role="img"
      aria-label="Solden"
      style=${`color: ${color}`}
      xmlns="http://www.w3.org/2000/svg">
      <g transform="translate(0, 910) scale(0.1, -0.1)">
        <path d="M3699 5352 c-53 -37 -173 -118 -268 -181 -140 -93 -171 -118 -169 -135 l3 -21 785 -2 c483 -2 791 1 800 7 12 7 16 42 18 184 3 115 0 181 -8 195 -11 21 -14 21 -538 21 l-527 0 -96 -68z" />
        <path d="M3273 4073 c-16 -6 -19 -368 -3 -378 5 -3 248 -7 538 -7 l529 -1 259 175 c186 126 260 181 262 197 l3 21 -788 -1 c-433 0 -794 -3 -800 -6z" />
      </g>
    </svg>
  `;
}
