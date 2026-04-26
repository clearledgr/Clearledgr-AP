import { html } from '../../utils/htm.js';
import ReviewPage from './ReviewPage.js';
import { usePageProps } from '../../shell/usePageProps.js';

export function ReviewRoute() {
  return html`<${ReviewPage} ...${usePageProps()} />`;
}
