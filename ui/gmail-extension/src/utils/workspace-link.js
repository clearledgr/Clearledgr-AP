/**
 * Hub deep-links — the Gmail extension is the contextual companion;
 * the workspace SPA at WORKSPACE_URL is the system of record.
 * "Open in Console" buttons across the extension funnel through here
 * so we have one place to swap the host when workspace.clearledgr.com
 * goes live behind its Let's Encrypt cert.
 */

export const WORKSPACE_URL = (() => {
  const fromConfig =
    (typeof self !== 'undefined' && self.CLEARLEDGR_CONFIG?.WORKSPACE_URL) ||
    (typeof globalThis !== 'undefined' && globalThis.CLEARLEDGR_CONFIG?.WORKSPACE_URL);
  return String(fromConfig || 'https://web-app-production-a046.up.railway.app').replace(/\/+$/, '');
})();

export function workspaceItemUrl(itemId) {
  const id = String(itemId || '').trim();
  if (!id) return WORKSPACE_URL;
  return `${WORKSPACE_URL}/items/${encodeURIComponent(id)}`;
}

export function workspaceVendorUrl(vendorName) {
  const name = String(vendorName || '').trim();
  if (!name) return `${WORKSPACE_URL}/vendors`;
  return `${WORKSPACE_URL}/vendors/${encodeURIComponent(name)}`;
}

export function workspacePipelineUrl() {
  return `${WORKSPACE_URL}/pipeline`;
}
