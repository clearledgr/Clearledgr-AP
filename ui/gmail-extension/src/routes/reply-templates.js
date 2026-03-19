const STORAGE_PREFIX = 'clearledgr_reply_templates_v1';
const MAX_CUSTOM_TEMPLATES = 12;

export const DEFAULT_REPLY_TEMPLATES = [
  {
    id: 'vendor_missing_info',
    name: 'Vendor missing info',
    description: 'Ask the supplier for the missing invoice details.',
    audience: 'vendor',
    subjectTemplate: 'Re: {{subject}}',
    bodyTemplate: [
      'Hi {{vendor_name}},',
      '',
      'We are reviewing invoice {{invoice_number}} for {{amount}} and still need the following before we can complete processing:',
      '',
      '{{issue_summary}}',
      '',
      'Please reply in this thread when you can.',
      '',
      'Best,',
      'Accounts Payable',
    ].join('\n'),
  },
  {
    id: 'approval_nudge',
    name: 'Approval nudge',
    description: 'Send a concise internal follow-up on an outstanding approval.',
    audience: 'internal',
    subjectTemplate: 'Approval follow-up: {{vendor_name}} invoice {{invoice_number}}',
    bodyTemplate: [
      'Hi,',
      '',
      'A decision is still needed on {{vendor_name}} invoice {{invoice_number}} for {{amount}}.',
      '',
      'Current blocker: {{issue_summary}}',
      'Due date: {{due_date}}',
      '',
      'Please take a look when you can.',
      '',
      'Best,',
      'Clearledgr AP',
    ].join('\n'),
  },
  {
    id: 'payment_status',
    name: 'Payment status',
    description: 'Reply to a supplier about invoice status.',
    audience: 'vendor',
    subjectTemplate: 'Re: {{subject}}',
    bodyTemplate: [
      'Hi {{vendor_name}},',
      '',
      'Status update for invoice {{invoice_number}} ({{amount}}): {{state_label}}.',
      '',
      'Current next step: {{next_action}}',
      '',
      'We will update you again if anything changes.',
      '',
      'Best,',
      'Accounts Payable',
    ].join('\n'),
  },
  {
    id: 'rejection_note',
    name: 'Rejection note',
    description: 'Explain why an invoice cannot proceed.',
    audience: 'vendor',
    subjectTemplate: 'Re: {{subject}}',
    bodyTemplate: [
      'Hi {{vendor_name}},',
      '',
      'We cannot continue invoice {{invoice_number}} for {{amount}} yet.',
      '',
      'Reason: {{issue_summary}}',
      '',
      'Please reply with the corrected information or updated invoice.',
      '',
      'Best,',
      'Accounts Payable',
    ].join('\n'),
  },
];

function normalizeText(value, fallback = '') {
  return String(value || '').trim() || fallback;
}

function normalizeUserEmail(value) {
  return normalizeText(value).toLowerCase();
}

function resolveScope(scopeOrOrgId, maybeUserEmail = '') {
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

function normalizeTemplate(value = {}, scope = 'user') {
  return {
    id: normalizeText(value?.id),
    scope,
    name: normalizeText(value?.name).slice(0, 48),
    description: normalizeText(value?.description).slice(0, 140),
    audience: normalizeText(value?.audience, 'vendor').toLowerCase() === 'internal' ? 'internal' : 'vendor',
    subjectTemplate: normalizeText(value?.subjectTemplate || value?.subject_template).slice(0, 180),
    bodyTemplate: normalizeText(value?.bodyTemplate || value?.body_template).slice(0, 2400),
  };
}

function sanitizeCustomTemplates(values = []) {
  return (Array.isArray(values) ? values : [])
    .map((entry) => normalizeTemplate(entry, 'user'))
    .filter((entry) => entry.id && entry.name && entry.bodyTemplate)
    .slice(0, MAX_CUSTOM_TEMPLATES);
}

function sanitizeStarterTemplates() {
  return DEFAULT_REPLY_TEMPLATES
    .map((entry) => normalizeTemplate(entry, 'starter'))
    .filter((entry) => entry.id && entry.name && entry.bodyTemplate);
}

export function getReplyTemplatePreferenceKey(scopeOrOrgId, maybeUserEmail = '') {
  const scope = resolveScope(scopeOrOrgId, maybeUserEmail);
  return `${STORAGE_PREFIX}:${scope.orgId}:${scope.userEmail || 'anonymous'}`;
}

export function defaultReplyTemplatePreferences() {
  return {
    customTemplates: [],
  };
}

export function normalizeReplyTemplatePreferences(value = {}) {
  return {
    customTemplates: sanitizeCustomTemplates(value?.customTemplates),
  };
}

export function getBootstrappedReplyTemplatePreferences(bootstrap = {}) {
  return (
    bootstrap?.current_user?.preferences?.gmail_extension?.reply_templates
    || bootstrap?.current_user?.preferences?.reply_templates
    || null
  );
}

export function buildReplyTemplatePreferencePatch(preferences = {}) {
  return {
    gmail_extension: {
      reply_templates: normalizeReplyTemplatePreferences(preferences),
    },
  };
}

export function readReplyTemplatePreferences(scopeOrOrgId, maybeUserEmail = '') {
  const key = getReplyTemplatePreferenceKey(scopeOrOrgId, maybeUserEmail);
  const raw = readStorageValue(key);
  if (!raw) return defaultReplyTemplatePreferences();
  try {
    return normalizeReplyTemplatePreferences(JSON.parse(raw));
  } catch {
    return defaultReplyTemplatePreferences();
  }
}

export function writeReplyTemplatePreferences(scopeOrOrgId, maybeUserEmailOrValue = '', maybeValue = null) {
  const hasExplicitUserEmail = typeof maybeUserEmailOrValue === 'string' || maybeUserEmailOrValue == null;
  const userEmail = hasExplicitUserEmail ? maybeUserEmailOrValue : '';
  const value = hasExplicitUserEmail ? maybeValue : maybeUserEmailOrValue;
  const key = getReplyTemplatePreferenceKey(scopeOrOrgId, userEmail);
  const normalized = normalizeReplyTemplatePreferences(value || {});
  writeStorageValue(key, JSON.stringify(normalized));
  return normalized;
}

export function getStarterReplyTemplates() {
  return sanitizeStarterTemplates();
}

export function getPersonalReplyTemplates(preferences = {}) {
  return sanitizeCustomTemplates(normalizeReplyTemplatePreferences(preferences).customTemplates);
}

export function getAllReplyTemplates(preferences = {}) {
  return [
    ...getStarterReplyTemplates(),
    ...getPersonalReplyTemplates(preferences),
  ];
}

export function createReplyTemplate(scopeOrOrgId, maybeUserEmailOrTemplate = '', maybeTemplate = null) {
  const hasExplicitUserEmail = typeof maybeUserEmailOrTemplate === 'string' || maybeUserEmailOrTemplate == null;
  const userEmail = hasExplicitUserEmail ? maybeUserEmailOrTemplate : '';
  const template = hasExplicitUserEmail ? maybeTemplate : maybeUserEmailOrTemplate;
  const current = readReplyTemplatePreferences(scopeOrOrgId, userEmail);
  const nextTemplate = normalizeTemplate({
    ...template,
    id: normalizeText(template?.id, `tmpl-${Date.now().toString(36)}`),
  }, 'user');
  return writeReplyTemplatePreferences(scopeOrOrgId, userEmail, {
    ...current,
    customTemplates: [...current.customTemplates, nextTemplate],
  });
}

export function updateReplyTemplate(scopeOrOrgId, maybeUserEmailOrTemplateId = '', maybeTemplateIdOrPatch = null, maybePatch = null) {
  const hasExplicitUserEmail = typeof maybeUserEmailOrTemplateId === 'string' && typeof maybeTemplateIdOrPatch === 'string';
  const userEmail = hasExplicitUserEmail ? maybeUserEmailOrTemplateId : '';
  const templateId = hasExplicitUserEmail ? maybeTemplateIdOrPatch : maybeUserEmailOrTemplateId;
  const patch = hasExplicitUserEmail ? maybePatch : maybeTemplateIdOrPatch;
  const current = readReplyTemplatePreferences(scopeOrOrgId, userEmail);
  const nextTemplates = current.customTemplates.map((template) => (
    template.id === templateId
      ? normalizeTemplate({ ...template, ...(patch || {}), id: template.id }, 'user')
      : template
  ));
  return writeReplyTemplatePreferences(scopeOrOrgId, userEmail, {
    ...current,
    customTemplates: nextTemplates,
  });
}

export function removeReplyTemplate(scopeOrOrgId, maybeUserEmailOrTemplateId = '', maybeTemplateId = null) {
  const hasExplicitUserEmail = typeof maybeTemplateId === 'string';
  const userEmail = hasExplicitUserEmail ? maybeUserEmailOrTemplateId : '';
  const templateId = hasExplicitUserEmail ? maybeTemplateId : maybeUserEmailOrTemplateId;
  const current = readReplyTemplatePreferences(scopeOrOrgId, userEmail);
  return writeReplyTemplatePreferences(scopeOrOrgId, userEmail, {
    ...current,
    customTemplates: current.customTemplates.filter((template) => template.id !== templateId),
  });
}

export function resolveReplyTemplate(preferences = {}, templateId = '') {
  const normalizedId = normalizeText(templateId);
  return getAllReplyTemplates(preferences).find((template) => template.id === normalizedId) || null;
}

export function interpolateReplyTemplate(text = '', values = {}) {
  return String(text || '').replace(/\{\{\s*([a-zA-Z0-9_]+)\s*\}\}/g, (_match, key) => {
    const value = values?.[key];
    return value == null ? '' : String(value);
  });
}

export function buildReplyTemplateContext(item = {}, extra = {}) {
  const amount = Number(item?.amount);
  const amountText = Number.isFinite(amount)
    ? `${String(item?.currency || 'USD')} ${amount.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
    : 'Amount unavailable';
  return {
    vendor_name: item?.vendor_name || item?.vendor || 'there',
    invoice_number: item?.invoice_number || 'N/A',
    amount: amountText,
    due_date: item?.due_date || 'No due date',
    po_number: item?.po_number || 'No PO',
    state_label: String(item?.state || 'received').replace(/_/g, ' '),
    next_action: item?.next_action || 'Review in Clearledgr',
    issue_summary: extra?.issue_summary || item?.exception_code || 'additional information is required',
    subject: item?.subject || `Invoice ${item?.invoice_number || ''}`.trim() || 'Invoice follow-up',
    sender_email: item?.sender || '',
    ...extra,
  };
}

export function buildReplyTemplatePrefill(template = {}, item = {}, extra = {}) {
  const context = buildReplyTemplateContext(item, extra);
  const audience = normalizeText(template?.audience, 'vendor').toLowerCase() === 'internal' ? 'internal' : 'vendor';
  return {
    to: audience === 'vendor' ? (extra?.to || context.sender_email || '') : (extra?.to || ''),
    subject: interpolateReplyTemplate(template?.subjectTemplate, context),
    body: interpolateReplyTemplate(template?.bodyTemplate, context).trim(),
    audience,
  };
}
