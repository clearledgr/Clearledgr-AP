import { html } from '../../utils/htm.js';
import ExceptionsPage from './ExceptionsPage.js';
import { usePageProps } from '../../shell/usePageProps.js';

export function ExceptionsRoute() {
  return html`<${ExceptionsPage} ...${usePageProps()} />`;
}
