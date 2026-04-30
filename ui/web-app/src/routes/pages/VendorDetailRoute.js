import { html } from '../../utils/htm.js';
import VendorDetailPage from './VendorDetailPage.js';
import { usePageProps } from '../../shell/usePageProps.js';
import { rememberVendorRouteName } from '../../utils/vendor-route.js';

/**
 * Route adapter for /vendors/:name — Module 4 vendor detail surface.
 * Pulls the standard page-prop bundle and forwards the URL-supplied
 * vendor name.
 */
export function VendorDetailRoute({ vendorName }) {
  if (vendorName) {
    rememberVendorRouteName(vendorName);
  }
  return html`<${VendorDetailPage} ...${usePageProps()} vendorName=${vendorName} />`;
}
