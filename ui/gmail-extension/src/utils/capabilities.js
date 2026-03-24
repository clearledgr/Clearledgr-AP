function normalizeRole(role) {
  return String(role || '').trim().toLowerCase();
}

function isCapabilityMap(value) {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

export function getFallbackCapabilities(role) {
  const normalizedRole = normalizeRole(role);
  const hasWorkspaceRole = Boolean(normalizedRole);
  const isAdmin = ['owner', 'admin', 'api'].includes(normalizedRole);
  const isOps = ['owner', 'admin', 'operator', 'api'].includes(normalizedRole);

  return {
    view_home: true,
    view_pipeline: true,
    view_review: hasWorkspaceRole,
    view_upcoming: hasWorkspaceRole,
    view_activity: hasWorkspaceRole,
    view_vendors: hasWorkspaceRole,
    view_templates: hasWorkspaceRole,
    view_connections: hasWorkspaceRole,
    view_rules: hasWorkspaceRole,
    view_settings: hasWorkspaceRole,
    view_team: hasWorkspaceRole,
    view_company: hasWorkspaceRole,
    view_plan: hasWorkspaceRole,
    view_reconciliation: hasWorkspaceRole,
    view_system_status: hasWorkspaceRole,
    view_reports: hasWorkspaceRole,
    view_ops_workspace: isOps,
    operate_records: isOps,
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
