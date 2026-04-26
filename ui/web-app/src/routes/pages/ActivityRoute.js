import { html } from '../../utils/htm.js';
import ActivityPage from './ActivityPage.js';
import { usePageProps } from '../../shell/usePageProps.js';

export function ActivityRoute() {
  return html`<${ActivityPage} ...${usePageProps()} />`;
}
