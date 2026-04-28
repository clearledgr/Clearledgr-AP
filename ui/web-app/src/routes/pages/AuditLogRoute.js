import { html } from '../../utils/htm.js';
import AuditLogPage from './AuditLogPage.js';
import { usePageProps } from '../../shell/usePageProps.js';

export function AuditLogRoute() {
  return html`<${AuditLogPage} ...${usePageProps()} />`;
}
