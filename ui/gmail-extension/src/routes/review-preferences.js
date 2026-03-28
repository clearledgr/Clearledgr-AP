const STORAGE_PREFIX = 'clearledgr_review_preferences_v1';

function normalizeText(value, fallback = '') {
  return String(value || '').trim() || fallback;
}

function normalizeUserEmail(value) {
  return normalizeText(value).toLowerCase();
}

function resolveReviewScope(scopeOrOrgId, maybeUserEmail = '') {
  if (scopeOrOrgId && typeof scopeOrOrgId === 'object') {
    return {
      orgId: normalizeText(scopeOrOrgId.orgId || scopeOrOrgId.organizationId, 'default'),
      userEmail: normalizeUserEmail(scopeOrOrgId.userEmail || scopeOrOrgId.email || maybeUserEmail),
    };
  }
  return {
    orgId: normalizeText(scopeOrOrgId, 'default'),
    userEmail: normalizeUserEmail(maybeUserEmail),
  };
}

function readStorageValue(key) {
  if (typeof window === 'undefined' || !window?.localStorage) return null;
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStorageValue(key, value) {
  if (typeof window === 'undefined' || !window?.localStorage) return;
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* best effort */
  }
}

function removeStorageValue(key) {
  if (typeof window === 'undefined' || !window?.localStorage) return;
  try {
    window.localStorage.removeItem(key);
  } catch {
    /* best effort */
  }
}

export function getReviewPreferenceKey(scopeOrOrgId, maybeUserEmail = '') {
  const scope = resolveReviewScope(scopeOrOrgId, maybeUserEmail);
  return `${STORAGE_PREFIX}:${scope.orgId}:${scope.userEmail || 'anonymous'}`;
}

export function defaultReviewPreferences() {
  return {
    searchQuery: '',
  };
}

export function normalizeReviewPreferences(value = {}) {
  return {
    searchQuery: normalizeText(value?.searchQuery).slice(0, 120),
  };
}

export function readReviewPreferences(scopeOrOrgId, maybeUserEmail = '') {
  const raw = readStorageValue(getReviewPreferenceKey(scopeOrOrgId, maybeUserEmail));
  if (!raw) return defaultReviewPreferences();
  try {
    return normalizeReviewPreferences(JSON.parse(raw));
  } catch {
    return defaultReviewPreferences();
  }
}

export function writeReviewPreferences(scopeOrOrgId, maybeUserEmailOrValue = '', maybeValue = null) {
  const hasExplicitUserEmail = typeof maybeUserEmailOrValue === 'string' || maybeUserEmailOrValue == null;
  const userEmail = hasExplicitUserEmail ? maybeUserEmailOrValue : '';
  const value = hasExplicitUserEmail ? maybeValue : maybeUserEmailOrValue;
  writeStorageValue(
    getReviewPreferenceKey(scopeOrOrgId, userEmail),
    JSON.stringify(normalizeReviewPreferences(value)),
  );
}

export function clearReviewPreferences(scopeOrOrgId, maybeUserEmail = '') {
  removeStorageValue(getReviewPreferenceKey(scopeOrOrgId, maybeUserEmail));
}
