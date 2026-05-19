import { html } from '../../utils/htm.js';
import RecordsPage from './RecordsPage.js';
import { usePageProps } from '../../shell/usePageProps.js';

export function RecordsRoute() {
  return html`<${RecordsPage} ...${usePageProps()} />`;
}
