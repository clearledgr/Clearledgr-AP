import { html } from '../utils/htm.js';

/**
 * The Solden S-mark as inline SVG. Three navy parallelogram bars
 * forming a stepped "S", traced to match the navy favicon at
 * /favicon.png. Inline-SVG so it stays crisp at any DPI — the PNG
 * was visibly soft when displayed at 36px and downscaled from 128px.
 *
 * Default colour is the canonical Solden navy (#0A1F44, also exposed
 * as --cl-navy in shell.css). Pass ``color`` to override.
 */
export function SoldenMark({ size = 36, color = '#0A1F44' }) {
  return html`
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width=${size}
      height=${size}
      viewBox="0 0 100 100"
      role="img"
      aria-label="Solden"
      style="display:block;flex:none">
      <g fill=${color}>
        <!-- Top bar: parallelogram, sheared right. -->
        <path d="M 10 12 L 88 6 Q 92 5.6 92 9.6 L 92 22 Q 92 26 88 26.4 L 10 32.4 Q 6 32.8 6 28.8 L 6 16.4 Q 6 12.4 10 12 Z"/>
        <!-- Middle bar: parallelogram, sheared LEFT (the S kink). -->
        <path d="M 88 39 L 10 33 Q 6 32.6 6 36.6 L 6 49 Q 6 53 10 53.4 L 88 59.4 Q 92 59.8 92 55.8 L 92 43.4 Q 92 39.4 88 39 Z"/>
        <!-- Bottom bar: parallelogram, sheared right (mirrors top). -->
        <path d="M 10 66 L 88 60 Q 92 59.6 92 63.6 L 92 76 Q 92 80 88 80.4 L 10 86.4 Q 6 86.8 6 82.8 L 6 70.4 Q 6 66.4 10 66 Z"/>
      </g>
    </svg>
  `;
}
