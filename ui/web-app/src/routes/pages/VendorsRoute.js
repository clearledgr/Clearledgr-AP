import { html } from '../../utils/htm.js';
import VendorsPage from './VendorsPage.js';
import { usePageProps } from '../../shell/usePageProps.js';

export function VendorsRoute() {
  return html`<${VendorsPage} ...${usePageProps()} />`;
}
