import { readLocalStorage, writeLocalStorage } from './formatters.js';

export const ACTIVE_RECORD_ID_STORAGE_KEY = 'clearledgr_active_ap_item_id';

function safeDecode(value) {
  const text = String(value || '').trim();
  if (!text) return '';
  try {
    return decodeURIComponent(text);
  } catch {
    return text;
  }
}

export function normalizeRecordRouteId(value) {
  return safeDecode(value).trim();
}

export function rememberRecordRouteId(recordId) {
  const normalized = normalizeRecordRouteId(recordId);
  if (!normalized) return '';
  writeLocalStorage(ACTIVE_RECORD_ID_STORAGE_KEY, normalized);
  return normalized;
}

/**
 * SPA navigation: `navigate` here is the wouter-preact location setter
 * (the second tuple element from `useLocation()`), which accepts a
 * path string. The SPA route is `/records/:id` — the listing lives at
 * `/records` and the detail surface at `/records/<id>`.
 */
export function navigateToRecordDetail(navigate, recordId) {
  const normalized = rememberRecordRouteId(recordId);
  if (!normalized || typeof navigate !== 'function') return false;
  navigate(`/records/${encodeURIComponent(normalized)}`);
  return true;
}

export function resolveRecordRouteId(params = {}, hash = '') {
  const paramId = normalizeRecordRouteId(params?.id);
  if (paramId) return paramId;

  const hashText = String(hash || '');
  const hashMatch = hashText.match(/records\/([^/?#]+)/);
  const hashId = normalizeRecordRouteId(hashMatch?.[1]);
  if (hashId) return hashId;

  return normalizeRecordRouteId(readLocalStorage(ACTIVE_RECORD_ID_STORAGE_KEY));
}
