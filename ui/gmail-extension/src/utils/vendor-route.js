import { readLocalStorage, writeLocalStorage } from './formatters.js';

export const ACTIVE_VENDOR_NAME_STORAGE_KEY = 'clearledgr_active_vendor_name';

function safeDecode(value) {
  const text = String(value || '').trim();
  if (!text) return '';
  try {
    return decodeURIComponent(text);
  } catch {
    return text;
  }
}

export function normalizeVendorRouteName(value) {
  return safeDecode(value).trim();
}

export function rememberVendorRouteName(vendorName) {
  const normalized = normalizeVendorRouteName(vendorName);
  if (!normalized) return '';
  writeLocalStorage(ACTIVE_VENDOR_NAME_STORAGE_KEY, normalized);
  return normalized;
}

export function navigateToVendorRecord(navigate, vendorName) {
  const normalized = rememberVendorRouteName(vendorName);
  if (!normalized || typeof navigate !== 'function') return false;
  navigate('clearledgr/vendor/:name', { name: normalized });
  return true;
}

export function resolveVendorRouteName(params = {}, hash = '') {
  const paramName = normalizeVendorRouteName(params?.name);
  if (paramName) return paramName;

  const hashText = String(hash || '');
  const hashMatch = hashText.match(/clearledgr\/vendor\/([^?]+)/);
  const hashName = normalizeVendorRouteName(hashMatch?.[1]);
  if (hashName) return hashName;

  return normalizeVendorRouteName(readLocalStorage(ACTIVE_VENDOR_NAME_STORAGE_KEY));
}
