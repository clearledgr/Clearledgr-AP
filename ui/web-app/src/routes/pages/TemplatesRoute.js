import { html } from '../../utils/htm.js';
import TemplatesPage from './TemplatesPage.js';
import { usePageProps } from '../../shell/usePageProps.js';

export function TemplatesRoute() {
  return html`<${TemplatesPage} ...${usePageProps()} />`;
}
