import { html } from '../../utils/htm.js';
import HealthPage from './HealthPage.js';
import { usePageProps } from '../../shell/usePageProps.js';

export function HealthRoute() {
  return html`<${HealthPage} ...${usePageProps()} />`;
}
