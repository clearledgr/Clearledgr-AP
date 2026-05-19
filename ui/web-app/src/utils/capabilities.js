/**
 * Capability resolution for the workspace SPA.
 *
 * Migration v89: the backend bootstrap response carries both the flat
 * legacy ``capabilities`` dict (every old key preserved) and a
 * tree-shaped ``capabilities_tree`` ({ workspace: {...}, ap_item:
 * {...} }) so the frontend can read either. Per-Box capabilities live
 * under their box_type key in the tree; workspace-wide capabilities
 * live under ``workspace``. New code should use ``hasBoxCapability``
 * for box-specific gates and ``hasCapability`` (flat) for the rest.
 */

function normalizeRole(role) {
  return String(role || '').trim().toLowerCase();
}

function isCapabilityMap(value) {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

// Map legacy single-axis values that older JWTs / cached bootstraps
// may still carry onto the v89 workspace-role axis.
const LEGACY_TO_WORKSPACE = {
  owner: 'owner',
  api: 'api',
  admin: 'admin',
  cfo: 'admin',
  financial_controller: 'admin',
  member: 'member',
  user: 'member',
  ap_clerk: 'member',
  ap_manager: 'member',
  operator: 'member',
  read_only: 'read_only',
  viewer: 'read_only',
};

function resolveWorkspaceRole(bootstrap) {
  const user = bootstrap?.current_user;
  const explicit = normalizeRole(user?.workspace_role);
  if (explicit) return explicit;
  const legacy = normalizeRole(user?.role);
  return LEGACY_TO_WORKSPACE[legacy] || legacy || '';
}

export function getFallbackCapabilities(role) {
  // ``role`` may be a workspace_role (v89) or a legacy single-axis
  // value (pre-v89). Either way, fold to the workspace axis first.
  const normalized = normalizeRole(role);
  const workspaceRole = LEGACY_TO_WORKSPACE[normalized] || normalized;
  const isAdmin = ['owner', 'admin', 'api'].includes(workspaceRole);
  const isMember = isAdmin || ['member'].includes(workspaceRole);

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
    view_ops_workspace: isMember || !workspaceRole,
    operate_records: isMember || !workspaceRole,
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
  // Prefer the canonical workspace_role; fall back to the legacy
  // role claim for stale bootstraps.
  const workspaceRole = resolveWorkspaceRole(bootstrap);
  const fallback = getFallbackCapabilities(workspaceRole);
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

export function getCapabilitiesTree(bootstrap) {
  // v89 tree shape — { workspace: {...}, ap_item: {...} }. When a
  // 2nd Box ships it adds a sibling key. Returns an empty object on
  // bootstraps that pre-date v89; callers should fall back to the
  // flat ``getCapabilities`` in that case.
  return (
    isCapabilityMap(bootstrap?.capabilities_tree)
    || isCapabilityMap(bootstrap?.current_user?.capabilities_tree)
  )
    ? (bootstrap.capabilities_tree || bootstrap.current_user.capabilities_tree)
    : {};
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

export function hasBoxCapability(bootstrap, boxType, capability) {
  // Look up a per-Box-type capability from the tree shape, e.g.
  // ``hasBoxCapability(bootstrap, 'ap_item', 'approve_invoice')``.
  // Falls back to the flat capability dict so pre-v89 cached
  // bootstraps still resolve common gates.
  if (!boxType || !capability) return false;
  const tree = getCapabilitiesTree(bootstrap);
  const box = isCapabilityMap(tree?.[boxType]) ? tree[boxType] : null;
  if (box) return Boolean(box[capability]);
  return hasCapability(bootstrap, capability);
}

export function getWorkspaceRole(bootstrap) {
  return resolveWorkspaceRole(bootstrap);
}

export function getBoxRole(bootstrap, boxType) {
  const roles = bootstrap?.current_user?.box_roles;
  return isCapabilityMap(roles) ? String(roles[boxType] || '').toLowerCase() : '';
}

export function hasOpsCapability(bootstrap) {
  const capabilities = getCapabilities(bootstrap);
  return Boolean(capabilities.operate_records || capabilities.view_ops_workspace);
}

export function hasAdminCapability(bootstrap) {
  const capabilities = getCapabilities(bootstrap);
  return Boolean(capabilities.manage_admin_pages);
}
