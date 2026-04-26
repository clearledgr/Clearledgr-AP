export function normalizeUserRole(role) {
  return String(role || '').trim().toLowerCase();
}

export function hasOpsAccessRole(role) {
  return ['owner', 'admin', 'operator'].includes(normalizeUserRole(role));
}

export function hasAdminAccessRole(role) {
  return ['owner', 'admin'].includes(normalizeUserRole(role));
}
