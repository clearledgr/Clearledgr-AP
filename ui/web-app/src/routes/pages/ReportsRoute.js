import { html } from '../../utils/htm.js';
import ReportsPage from './ReportsPage.js';
import { usePageProps } from '../../shell/usePageProps.js';

export function ReportsRoute() {
  return html`<${ReportsPage} ...${usePageProps()} />`;
}
