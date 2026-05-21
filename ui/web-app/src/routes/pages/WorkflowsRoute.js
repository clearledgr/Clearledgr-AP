import { html } from '../../utils/htm.js';
import WorkflowsPage from './WorkflowsPage.js';
import { usePageProps } from '../../shell/usePageProps.js';

export function WorkflowsRoute() {
  return html`<${WorkflowsPage} ...${usePageProps()} />`;
}
