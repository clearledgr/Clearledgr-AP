import { html } from '../../utils/htm.js';
import PipelinePage from './PipelinePage.js';
import { usePageProps } from '../../shell/usePageProps.js';

export function PipelineRoute() {
  return html`<${PipelinePage} ...${usePageProps()} />`;
}
