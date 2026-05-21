import { html } from '../../utils/htm.js';
import ProcurementPage from './ProcurementPage.js';
import { usePageProps } from '../../shell/usePageProps.js';

export function ProcurementRoute() {
  return html`<${ProcurementPage} ...${usePageProps()} />`;
}
