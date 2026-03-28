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

export function navigateToRecordDetail(navigate, recordId) {
  const normalized = rememberRecordRouteId(recordId);
  if (!normalized || typeof navigate !== 'function') return false;
  navigate('clearledgr/invoice/:id', { id: normalized });
  return true;
}

export function resolveRecordRouteId(params = {}, hash = '') {
  const paramId = normalizeRecordRouteId(params?.id);
  if (paramId) return paramId;

  const hashText = String(hash || '');
  const hashMatch = hashText.match(/clearledgr\/invoice\/([^?]+)/);
  const hashId = normalizeRecordRouteId(hashMatch?.[1]);
  if (hashId) return hashId;

  return normalizeRecordRouteId(readLocalStorage(ACTIVE_RECORD_ID_STORAGE_KEY));
}
