import { html } from '../../utils/htm.js';
import RecordDetailPage from './RecordDetailPage.js';
import { usePageProps } from '../../shell/usePageProps.js';
import { rememberRecordRouteId } from '../../utils/record-route.js';

/**
 * Route adapter for /items/:id — the workspace exception detail page
 * (Module 2). Pulls the standard page-prop bundle from `usePageProps`
 * and forwards the URL-supplied record id.
 */
export function RecordDetailRoute({ recordId }) {
  // Persist the active record id so a refresh of any page inside the
  // shell can resume to this detail view. Mirrors the Gmail extension
  // behaviour the SPA inherited.
  if (recordId) {
    rememberRecordRouteId(recordId);
  }
  return html`<${RecordDetailPage} ...${usePageProps()} recordId=${recordId} />`;
}
