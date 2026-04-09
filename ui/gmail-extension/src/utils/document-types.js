const DOCUMENT_TYPE_ALIASES = {
  invoice: 'invoice',
  invoices: 'invoice',
  payment: 'receipt',
  payments: 'receipt',
  payment_confirmation: 'receipt',
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
  debit_note: 'debit_note',
  payment_request: 'payment_request',
  payment_requests: 'payment_request',
  paymentrequest: 'payment_request',
  subscription_notification: 'subscription',
  subscription: 'subscription',
  saas_charge: 'subscription',
  recurring_charge: 'subscription',
  remittance_advice: 'remittance',
  remittance: 'remittance',
  statement: 'statement',
  statements: 'statement',
  bank_statement: 'statement',
  bank_statements: 'statement',
  vendor_statement: 'statement',
  bank_notification: 'bank_notification',
  po_confirmation: 'po_confirmation',
  tax_document: 'tax_document',
  contract_renewal: 'contract',
  contract: 'contract',
  dispute_response: 'dispute_response',
  other: 'other',
};

const DOCUMENT_TYPE_LABELS = {
  invoice: 'Invoice',
  receipt: 'Receipt',
  refund: 'Refund',
  credit_note: 'Credit note',
  debit_note: 'Debit note',
  payment_request: 'Payment request',
  subscription: 'Subscription charge',
  remittance: 'Remittance advice',
  statement: 'Vendor statement',
  bank_notification: 'Bank notification',
  po_confirmation: 'PO confirmation',
  tax_document: 'Tax document',
  contract: 'Contract / renewal',
  dispute_response: 'Dispute response',
  other: 'Finance document',
};

const DOCUMENT_TYPE_PLURAL_LABELS = {
  invoice: 'Invoices',
  receipt: 'Receipts',
  refund: 'Refunds',
  credit_note: 'Credit notes',
  debit_note: 'Debit notes',
  payment_request: 'Payment requests',
  subscription: 'Subscription charges',
  remittance: 'Remittance advices',
  statement: 'Vendor statements',
  bank_notification: 'Bank notifications',
  po_confirmation: 'PO confirmations',
  tax_document: 'Tax documents',
  contract: 'Contracts & renewals',
  dispute_response: 'Dispute responses',
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
      return 'Vendor credit that reduces your payable. Link to the original invoice to apply it.';
    case 'debit_note':
      return 'Additional charge from vendor. Link to the original invoice if applicable.';
    case 'refund':
      return 'Refund confirmation. Record for reconciliation.';
    case 'receipt':
      return 'Payment already completed. Archive this record.';
    case 'subscription':
      return 'Subscription charge already billed to your card. Assign a GL code and close.';
    case 'payment_request':
      return 'Non-invoice payment request. Route to approval before payment.';
    case 'remittance':
      return 'Proof that payment was sent. Link to the payment record.';
    case 'statement':
      return 'Vendor account summary for reconciliation. Route to accounting.';
    case 'bank_notification':
      return 'Bank fee or notification. Archive or route to accounting.';
    case 'po_confirmation':
      return 'Vendor confirmed your purchase order. Archive or route to procurement.';
    case 'tax_document':
      return 'Tax document. Route to accounting for compliance filing.';
    case 'contract':
      return 'Vendor contract or renewal notice. Review terms and link to vendor profile.';
    case 'dispute_response':
      return 'Vendor reply to a dispute. Link to existing dispute and notify operator.';
    default:
      return 'Review this finance document before any downstream action.';
  }
}
