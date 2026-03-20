const DOCUMENT_TYPE_ALIASES = {
  invoice: 'invoice',
  invoices: 'invoice',
  receipt: 'receipt',
  receipts: 'receipt',
  refund: 'refund',
  refunds: 'refund',
  credit_note: 'credit_note',
  credit_notes: 'credit_note',
  creditnote: 'credit_note',
  credit_memo: 'credit_note',
  credit_memos: 'credit_note',
  creditmemo: 'credit_note',
  payment_request: 'payment_request',
  payment_requests: 'payment_request',
  paymentrequest: 'payment_request',
  statement: 'statement',
  statements: 'statement',
  bank_statement: 'statement',
  bank_statements: 'statement',
  other: 'other',
};

const DOCUMENT_TYPE_LABELS = {
  invoice: 'Invoice',
  receipt: 'Receipt',
  refund: 'Refund',
  credit_note: 'Credit note',
  payment_request: 'Payment request',
  statement: 'Bank statement',
  other: 'Finance document',
};

const DOCUMENT_TYPE_PLURAL_LABELS = {
  invoice: 'Invoices',
  receipt: 'Receipts',
  refund: 'Refunds',
  credit_note: 'Credit notes',
  payment_request: 'Payment requests',
  statement: 'Bank statements',
  other: 'Finance documents',
};

export function normalizeDocumentType(value) {
  const raw = String(value || '').trim().toLowerCase().replace(/[\s-]+/g, '_');
  if (!raw) return 'invoice';
  return DOCUMENT_TYPE_ALIASES[raw] || raw;
}

export function isInvoiceDocumentType(value) {
  return normalizeDocumentType(value) === 'invoice';
}

export function getDocumentTypeLabel(value, options = {}) {
  const { plural = false, lowercase = false } = options;
  const normalized = normalizeDocumentType(value);
  const base = plural
    ? (DOCUMENT_TYPE_PLURAL_LABELS[normalized] || DOCUMENT_TYPE_PLURAL_LABELS.other)
    : (DOCUMENT_TYPE_LABELS[normalized] || DOCUMENT_TYPE_LABELS.other);
  if (!lowercase) return base;
  return `${base.charAt(0).toLowerCase()}${base.slice(1)}`;
}

export function getDocumentReferenceLabel(value) {
  return isInvoiceDocumentType(value) ? 'Invoice #' : 'Reference #';
}

export function getDocumentReferenceText(value, reference) {
  const normalized = normalizeDocumentType(value);
  const label = getDocumentTypeLabel(normalized);
  const referenceText = String(reference || '').trim();
  if (!referenceText) return label;
  if (normalized === 'invoice') return `Invoice ${referenceText}`;
  return `${label} · Ref ${referenceText}`;
}

export function getNonInvoiceWorkflowGuidance(value) {
  switch (normalizeDocumentType(value)) {
    case 'credit_note':
      return 'Review this credit note and link it to the related invoice before any downstream action.';
    case 'refund':
      return 'Review this refund and link it to the related payment or vendor balance activity.';
    case 'receipt':
      return 'Review this receipt and link it to the related payment activity.';
    case 'payment_request':
      return 'Review this payment request before routing it outside the invoice workflow.';
    case 'statement':
      return 'Review this bank statement before reconciliation or follow-up.';
    default:
      return 'Review this finance document before any downstream action.';
  }
}
