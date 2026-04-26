import { html } from '../../utils/htm.js';
import SettingsPage from './SettingsPage.js';
import { usePageProps } from '../../shell/usePageProps.js';

export function SettingsRoute() {
  return html`<${SettingsPage} ...${usePageProps()} />`;
}
