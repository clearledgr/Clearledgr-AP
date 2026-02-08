// Clearledgr AP classifier (shared across Gmail UI + queue manager)
// Single source of truth for AP detection signals in the extension.

const NOISE_PATTERNS = [
  // Marketing & promotions
  /\bbonus\b/i, /\bpromo(tion|tional)?\b/i, /\bdiscount\b/i, /\bsale\b/i,
  /\bspots?\s*left\b/i, /\bclaim\s*(your|now)\b/i, /\blimited\s*time\b/i,
  /\bfree\s*trial\b/i, /\bspecial\s*offer\b/i, /\bexclusive\b/i,
  // Newsletters & events
  /\bnewsletter\b/i, /\bwebinar\b/i, /\bregistration\b/i, /\bevent\b/i,
  /\bhappening\s*(this|next)\b/i, /\bbest\s*of\b/i, /\bupcoming\b/i,
  /\bjoin\s*us\b/i, /\binvit(e|ation)\b/i, /\brsvp\b/i,
  // Learning & tips
  /\bpro\s*tip\b/i, /\blearn\s*how\b/i, /\bhow\s*to\b/i, /\btips?\s*(for|on|to)\b/i,
  /\bguide\b/i, /\btutorial\b/i, /\bbest\s*practices\b/i,
  // Product updates & announcements
  /\bnew\s*(on|in|at|feature)\b/i, /\bproduct\s*update\b/i, /\bwhat'?s\s*new\b/i,
  /\bannouncing\b/i, /\bintroducing\b/i, /\blaunch(ing|ed)?\b/i,
  // Failures & notifications (not AP)
  /\bunsuccessful\b/i, /\bfailed\b/i, /\bdeclined\b/i, /\berror\b/i,
  /\bweren'?t\s*able\b/i, /\bcouldn'?t\s*(process|charge)\b/i,
  /\bpayment\s*(failed|declined|unsuccessful)\b/i,
  // Receipts & confirmations (not AP)
  /\breceipt\b/i, /\bpayment\s*received\b/i, /\border\s*confirmation\b/i,
  /\bthank\s+you\s+for\s+your\s+purchase\b/i,
  // Unsubscribe signals
  /\bunsubscribe\b/i, /\bopt[- ]?out\b/i, /\bpreferences\b/i,
  // Social & community
  /\bcommunity\b/i, /\bforum\b/i, /\bdiscussion\b/i, /\bfeedback\b/i,
  // Generic marketing from finance companies
  /\bsave\s*\$?\d+/i, /\bearn\s*\$?\d+/i, /\bget\s*\$?\d+\s*(back|off|credit)\b/i
];

const STRONG_INVOICE_PATTERNS = [
  /\binvoice\s*#?\s*:?\s*[A-Z0-9-]+/i,
  /\binv[-_]?\d+/i,
  /\b(amount|total)\s*(due|owed|payable)\s*:?\s*[\$€£]?[\d,]+/i,
  /\bdue\s*date\s*:?\s*\d/i,
  /\bpayment\s*(terms|due)\s*:?\s*(net|upon)/i,
  /\bpurchase\s*order\s*#?\s*:?\s*[A-Z0-9-]+/i,
];

const PAYMENT_REQUEST_PATTERNS = [
  /\bpayment\s*request\b/i,
  /\brequest\s*for\s*payment\b/i,
  /\bplease\s*pay\b/i,
  /\bpay\s+this\s+invoice\b/i,
  /\bpayable\b/i
];

const INVOICE_KEYWORDS = [
  /\binvoice\b/i,
  /\bbill\b/i,
  /\bamount\s+due\b/i,
  /\btotal\s+due\b/i,
  /\bbalance\s+due\b/i,
  /\boverdue\b/i
];

const AMOUNT_PATTERN = /(?:\$|€|£)\s*[\d,.]+|(?:USD|EUR|GBP|CHF)\s*[\d,.]+|[\d,.]+\s*(?:USD|EUR|GBP|CHF)/i;

const BILLING_SENDER_HINTS = [
  /billing@/i,
  /invoices@/i,
  /accounts@/i,
  /ar@/i,
  /receivables@/i,
  /payments@/i,
  /finance@/i,
  /noreply@.*bill/i
];

const DOC_ATTACHMENT_PATTERN = /\.(pdf|xlsx?|csv|docx|png|jpg|jpeg)$/i;

function normalizeText(...parts) {
  return parts.filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}

function hasDocAttachment(attachments = []) {
  if (!Array.isArray(attachments)) return false;
  return attachments.some((a) => {
    const name = String(a?.filename || a?.name || '').toLowerCase();
    const mime = String(a?.mimeType || a?.type || '').toLowerCase();
    return DOC_ATTACHMENT_PATTERN.test(name) || mime.includes('pdf') || mime.includes('image/');
  });
}

function detectNoiseScore(text) {
  let score = 0;
  for (const pattern of NOISE_PATTERNS) {
    if (pattern.test(text)) score += 30;
  }
  return score;
}

function detectInvoiceKeyword(text) {
  return INVOICE_KEYWORDS.some((p) => p.test(text));
}

function detectStrongIndicator(text) {
  return STRONG_INVOICE_PATTERNS.some((p) => p.test(text));
}

function detectPaymentRequest(text) {
  return PAYMENT_REQUEST_PATTERNS.some((p) => p.test(text));
}

function detectSenderBilling(senderEmail) {
  if (!senderEmail) return false;
  return BILLING_SENDER_HINTS.some((p) => p.test(senderEmail));
}

function detectAmount(text) {
  return AMOUNT_PATTERN.test(text);
}

function detectDueDate(text) {
  return /\bdue\s*(date|by|on)?\s*:?\s*\d{1,2}[\/\-]\d{1,2}/i.test(text);
}

export function classifyApEmail(email = {}, { mode = 'dom' } = {}) {
  const subject = String(email.subject || '');
  const sender = String(email.sender || '');
  const senderEmail = String(email.senderEmail || '');
  const snippet = String(email.snippet || '');
  const combined = normalizeText(subject, sender, snippet).toLowerCase();

  const noiseScore = detectNoiseScore(combined);
  if (noiseScore >= 60) {
    return {
      score: 0,
      isAp: false,
      shouldQueue: false,
      type: 'noise',
      signals: { noiseScore }
    };
  }

  const hasStrongIndicator = detectStrongIndicator(combined);
  const hasInvoiceKeyword = detectInvoiceKeyword(combined);
  const hasPaymentRequest = detectPaymentRequest(combined);
  const hasDoc = hasDocAttachment(email.attachments);
  const hasAmount = detectAmount(combined);
  const hasDueDate = detectDueDate(combined);
  const hasBillingSender = detectSenderBilling(senderEmail);

  let score = 0;
  if (hasStrongIndicator) score += 45;
  if (hasInvoiceKeyword) score += 25;
  if (hasPaymentRequest) score += 20;
  if (hasDoc) score += 20;
  if (hasAmount) score += 15;
  if (hasDueDate) score += 15;
  if (hasBillingSender) score += 15;

  score = Math.max(0, score - noiseScore);

  if (!hasStrongIndicator && score > 60) {
    score = 60;
  }

  const isAp = hasStrongIndicator || ((hasInvoiceKeyword || hasPaymentRequest) && (hasAmount || hasDoc));
  const type = hasPaymentRequest ? 'payment_request' : (hasInvoiceKeyword || hasStrongIndicator ? 'invoice' : 'unknown');
  const shouldQueue = isAp && (mode !== 'dom' || score >= 60);

  return {
    score: Math.min(100, score),
    isAp,
    shouldQueue,
    type,
    signals: {
      hasStrongIndicator,
      hasInvoiceKeyword,
      hasPaymentRequest,
      hasDoc,
      hasAmount,
      hasDueDate,
      hasBillingSender,
      noiseScore
    }
  };
}

