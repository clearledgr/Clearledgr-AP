function normalizeRole(role) {
  return String(role || '').trim().toLowerCase();
}

function isCapabilityMap(value) {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

export function getFallbackCapabilities(role) {
  const normalizedRole = normalizeRole(role);
  const isAdmin = ['owner', 'admin', 'api'].includes(normalizedRole);
  const isOps = ['owner', 'admin', 'operator', 'api'].includes(normalizedRole);

  // All view capabilities default to true — never lock users out of
  // navigation because bootstrap failed or role is unknown. Manage
  // capabilities (destructive actions) require confirmed role.
  return {
    view_home: true,
    view_pipeline: true,
    view_review: true,
    view_upcoming: true,
    view_activity: true,
    view_vendors: true,
    view_templates: true,
    view_connections: true,
    view_rules: true,
    view_settings: true,
    view_team: true,
    view_company: true,
    view_plan: true,
    view_reconciliation: true,
    view_system_status: true,
    view_reports: true,
    view_ops_workspace: isOps || !normalizedRole,
    operate_records: isOps || !normalizedRole,
    manage_connections: isAdmin,
    manage_rules: isAdmin,
    manage_settings: isAdmin,
    manage_team: isAdmin,
    manage_company: isAdmin,
    manage_plan: isAdmin,
    manage_admin_pages: isAdmin,
  };
}

export function getCapabilities(bootstrap) {
  const role = bootstrap?.current_user?.role;
  const fallback = getFallbackCapabilities(role);
  const explicit = isCapabilityMap(bootstrap?.capabilities)
    ? bootstrap.capabilities
    : isCapabilityMap(bootstrap?.current_user?.capabilities)
      ? bootstrap.current_user.capabilities
      : {};
  return {
    ...fallback,
    ...explicit,
  };
}

export function hasCapability(source, capability) {
  if (!capability) return false;
  const capabilities = isCapabilityMap(source?.capabilities) || isCapabilityMap(source?.current_user)
    ? getCapabilities(source)
    : isCapabilityMap(source)
      ? source
      : {};
  return Boolean(capabilities?.[capability]);
}

export function hasOpsCapability(bootstrap) {
  const capabilities = getCapabilities(bootstrap);
  return Boolean(capabilities.operate_records || capabilities.view_ops_workspace);
}

export function hasAdminCapability(bootstrap) {
  const capabilities = getCapabilities(bootstrap);
  return Boolean(capabilities.manage_admin_pages);
}
