import { html } from '../../utils/htm.js';
import RulesPage from './RulesPage.js';
import { usePageProps } from '../../shell/usePageProps.js';

export function RulesRoute() {
  return html`<${RulesPage} ...${usePageProps()} />`;
}
