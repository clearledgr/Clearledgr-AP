import { html } from '../../utils/htm.js';
import PlanPage from './PlanPage.js';
import { usePageProps } from '../../shell/usePageProps.js';

export function PlanRoute() {
  return html`<${PlanPage} ...${usePageProps()} />`;
}
