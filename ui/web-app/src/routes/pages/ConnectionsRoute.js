import { html } from '../../utils/htm.js';
import ConnectionsPage from './ConnectionsPage.js';
import { usePageProps } from '../../shell/usePageProps.js';

export function ConnectionsRoute() {
  return html`<${ConnectionsPage} ...${usePageProps()} />`;
}
