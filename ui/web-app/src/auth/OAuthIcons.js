import { html } from '../utils/htm.js';

/**
 * Official Google + Microsoft brand marks for OAuth sign-in buttons.
 *
 * Both are inline SVG so they scale crisp at any DPI without a network
 * round-trip. Sized to match the surrounding button text (~18px tall).
 *
 * Google brand guidelines: use the multi-colour "G" alongside
 * "Continue with Google" / "Sign in with Google". Reference:
 * https://developers.google.com/identity/branding-guidelines
 *
 * Microsoft brand guidelines: use the 4-square logo (Red / Green / Blue /
 * Yellow) alongside "Sign in with Microsoft". Reference:
 * https://learn.microsoft.com/en-us/azure/active-directory/develop/howto-add-branding-in-azure-ad-apps
 */

export function GoogleMark({ size = 18 }) {
  return html`
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width=${size}
      height=${size}
      viewBox="0 0 18 18"
      aria-hidden="true"
      focusable="false"
      style="display:block;flex:none">
      <path fill="#4285F4" d="M17.64 9.2045c0-.6381-.0573-1.2518-.1636-1.8409H9v3.4814h4.8436c-.2086 1.125-.8427 2.0782-1.7959 2.7164v2.2581h2.9089c1.7018-1.5668 2.6843-3.874 2.6843-6.615z"/>
      <path fill="#34A853" d="M9 18c2.43 0 4.4673-.806 5.9564-2.18l-2.9089-2.2581c-.806.54-1.8368.86-3.0475.86-2.344 0-4.3282-1.5832-5.0364-3.7104H.9573v2.3318C2.4382 15.9831 5.4818 18 9 18z"/>
      <path fill="#FBBC05" d="M3.9636 10.71A5.41 5.41 0 0 1 3.682 9c0-.5932.1023-1.1700.2823-1.71V4.9582H.9573A8.997 8.997 0 0 0 0 9c0 1.4523.3477 2.8268.9573 4.0418L3.9636 10.71z"/>
      <path fill="#EA4335" d="M9 3.5795c1.3214 0 2.5077.4541 3.4405 1.3459l2.5814-2.5814C13.4632.8918 11.4259 0 9 0 5.4818 0 2.4382 2.0168.9573 4.9582L3.9636 7.29C4.6718 5.1627 6.656 3.5795 9 3.5795z"/>
    </svg>
  `;
}

export function MicrosoftMark({ size = 18 }) {
  return html`
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width=${size}
      height=${size}
      viewBox="0 0 21 21"
      aria-hidden="true"
      focusable="false"
      style="display:block;flex:none">
      <rect x="1" y="1" width="9" height="9" fill="#F25022"/>
      <rect x="11" y="1" width="9" height="9" fill="#7FBA00"/>
      <rect x="1" y="11" width="9" height="9" fill="#00A4EF"/>
      <rect x="11" y="11" width="9" height="9" fill="#FFB900"/>
    </svg>
  `;
}
