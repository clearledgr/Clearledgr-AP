import { html } from '../../utils/htm.js';
import ReconciliationPage from './ReconciliationPage.js';
import { usePageProps } from '../../shell/usePageProps.js';

export function ReconciliationRoute() {
  return html`<${ReconciliationPage} ...${usePageProps()} />`;
}
