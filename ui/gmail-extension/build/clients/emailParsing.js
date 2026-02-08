(function() {
  function parseFinancialData(emailData) {
    const subject = emailData.subject || '';
    const rawBody = emailData.bodyText || emailData.bodyHtml || emailData.body || '';
    const body = emailData.bodyText ? rawBody : stripHtml(rawBody);
    const attachmentText = normalizeText(emailData.attachmentText || '');
    const baseText = [subject, body].filter(Boolean).join('\n').trim();
    const combined = [baseText, attachmentText].filter(Boolean).join('\n').trim();

    const vendorBase = extractVendor({ ...emailData, subject, bodyText: body });
    const vendorAttachment = attachmentText ? extractVendorFromBody(attachmentText) : null;
    const vendorChoice = chooseBestVendor(vendorBase, vendorAttachment);

    const amountBase = extractAmountDetails(baseText);
    const amountAttachment = attachmentText ? extractAmountDetails(attachmentText) : null;
    let amountChoice = chooseBestAmount(amountBase, amountAttachment);

    const invoiceBase = extractInvoiceNumberDetails(baseText, emailData.attachments || []);
    const invoiceAttachment = attachmentText ? extractInvoiceNumberDetails(attachmentText, emailData.attachments || []) : null;
    const invoiceChoice = chooseBestInvoice(invoiceBase, invoiceAttachment);

    if (amountChoice && invoiceChoice && amountMatchesInvoice(amountChoice.value, invoiceChoice.value)) {
      amountChoice = null;
    }

    const dateBase = extractDateDetails(baseText);
    const dateAttachment = attachmentText ? extractDateDetails(attachmentText) : null;
    const dateChoice = chooseBestDate(dateBase, dateAttachment);

    const paymentTerms = extractPaymentTerms(combined);

    return {
      vendor: vendorChoice?.value || vendorBase || 'Unknown Vendor',
      vendorScore: vendorChoice?.score || scoreVendorCandidate(vendorBase),
      vendorSource: vendorChoice?.source || 'email',
      amount: amountChoice?.formatted || null,
      amountRaw: amountChoice?.value || null,
      amountScore: amountChoice?.score || 0,
      amountSource: amountChoice?.source || 'email',
      currency: amountChoice?.currency || null,
      invoiceNumber: invoiceChoice?.value || null,
      invoiceScore: invoiceChoice?.score || 0,
      invoiceSource: invoiceChoice?.source || 'email',
      invoiceDate: dateChoice?.value || null,
      invoiceDateScore: dateChoice?.score || 0,
      invoiceDateSource: dateChoice?.source || 'email',
      paymentTerms,
      subject,
      senderEmail: emailData.senderEmail,
      hasAttachments: (emailData.attachments || []).length > 0,
      attachmentTextUsed: !!attachmentText
    };
  }

  /**
   * Parse PDF/CSV-derived text into normalized bank transactions (best-effort).
   */
  function parseTextTransactions(text) {
    const normalized = normalizeText(text);
    if (!normalized) return [];

    const lines = normalized.split(/\n+/).filter(Boolean);
    const dateRegex =
      /\b(\d{4}[/-]\d{1,2}[/-]\d{1,2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|[A-Za-z]{3}\s+\d{1,2},?\s+\d{2,4})\b/;
    const amountRegex = /([-+]?\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})|\d+\.\d{2})/;
    const txs = [];

    lines.forEach((line, idx) => {
      const dateMatch = line.match(dateRegex);
      const amountMatch = line.match(amountRegex);
      if (!dateMatch || !amountMatch) return;

      const amountVal = parseAmountValue(amountMatch[1]);
      if (amountVal === null || amountVal === undefined) return;

      const dateVal = parseDateValue(dateMatch[1]);
      const txId = `pdf_tx_${idx}_${dateMatch[1]}_${amountMatch[1]}`.replace(/\s+/g, '');
      txs.push({
        transaction_id: txId,
        transaction_date: dateVal ? dateVal.toISOString().split('T')[0] : new Date().toISOString().split('T')[0],
        description: line.slice(0, 140),
        counterparty: null,
        amount: { amount: Math.abs(amountVal), currency: 'EUR' },
        source: 'bank',
        metadata: { raw: line }
      });
    });

    return txs;
  }

  /**
   * Parse CSV attachments into normalized bank transactions.
   */
  function parseAttachmentTransactions(attachments) {
    const csvAttachments = (attachments || []).filter((att) => {
      const name = (att.name || att.filename || '').toLowerCase();
      return name.endsWith('.csv');
    });
    const allTx = [];

    csvAttachments.forEach((att) => {
      try {
        const content = att.getDataAsString ? att.getDataAsString() : att.content_text || att.contentText || '';
        if (!content) return;
        const rows = Utilities.parseCsv(content);
        if (!rows || rows.length < 2) return;
        const headers = rows[0].map((h) => String(h || '').toLowerCase().trim());
        const idx = {
          date: findHeader(headers, ['date', 'transaction date', 'posting date', 'transaction_date']),
          amount: findHeader(headers, ['amount', 'value', 'amt', 'transaction amount']),
          description: findHeader(headers, ['description', 'details', 'narration', 'memo']),
          counterparty: findHeader(headers, ['counterparty', 'merchant', 'vendor', 'name']),
          reference: findHeader(headers, ['reference', 'ref', 'id', 'transaction id'])
        };
        for (let i = 1; i < rows.length; i++) {
          const row = rows[i];
          const amountVal = parseAmountValue(row[idx.amount]);
          if (amountVal === null || amountVal === undefined) continue;
          const dateVal = parseDateValue(row[idx.date]);
          const txId = row[idx.reference] || `bank_tx_${i}`;
          allTx.push({
            transaction_id: String(txId),
            transaction_date: dateVal ? dateVal.toISOString().split('T')[0] : new Date().toISOString().split('T')[0],
            description: row[idx.description] || row[idx.counterparty] || 'Transaction',
            counterparty: row[idx.counterparty] || row[idx.description] || null,
            amount: { amount: Math.abs(amountVal), currency: 'EUR' },
            source: 'bank',
            metadata: { raw: row }
          });
        }
      } catch (e) {
        // ignore parse errors per attachment
      }
    });
    return allTx;
  }

  function findHeader(headers, candidates) {
    for (let i = 0; i < headers.length; i++) {
      if (candidates.some((c) => headers[i].includes(c))) return i;
    }
    return -1;
  }

  function parseDateValue(val) {
    if (!val) return null;
    try {
      if (val instanceof Date) return val;
      const parsed = new Date(val);
      if (!isNaN(parsed.getTime())) return parsed;
    } catch (e) {
      return null;
    }
    return null;
  }

  function stripHtml(text) {
    return String(text || '')
      .replace(/<[^>]*>/g, ' ')
      .replace(/&nbsp;|&#160;/gi, ' ')
      .replace(/&amp;/gi, '&')
      .replace(/&lt;/gi, '<')
      .replace(/&gt;/gi, '>')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function normalizeText(text) {
    return String(text || '')
      .replace(/\u00a0/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function extractVendor(emailData) {
    const senderName = emailData.senderName || '';
    const senderEmail = emailData.senderEmail || '';
    const subject = normalizeText(emailData.subject || '');
    const body = emailData.bodyText || '';

    const subjectPrefixVendor = extractVendorFromSubjectPrefix(subject);
    if (subjectPrefixVendor) return subjectPrefixVendor;

    const senderVendor = cleanVendorCandidate(senderName);
    if (senderVendor) return senderVendor;

    const subjectVendor = extractVendorFromSubject(subject);
    if (subjectVendor) return subjectVendor;

    const bodyVendor = extractVendorFromBody(body);
    if (bodyVendor) return bodyVendor;

    const fromPattern = /^(.+?)\s+from\s+(.+)$/i;
    const fromMatch = senderName.match(fromPattern);
    if (fromMatch) {
      const candidate = cleanVendorCandidate(fromMatch[2].trim());
      if (candidate) return candidate;
    }

    const dashPattern = /^(.+?)\s*[-|]\s*(.+)$/;
    const dashMatch = senderName.match(dashPattern);
    if (dashMatch) {
      const candidate = cleanVendorCandidate(dashMatch[1].trim());
      if (candidate) return candidate;
    }

    const domain = senderEmail.split('@')[1] || '';
    if (domain) {
      const companyName = domain.split('.')[0];
      return companyName.charAt(0).toUpperCase() + companyName.slice(1);
    }

    const subjectMatch = subject.match(/from\s+([A-Za-z0-9& .,'-]{2,})/i);
    if (subjectMatch) {
      const candidate = cleanVendorCandidate(subjectMatch[1].trim());
      if (candidate) return candidate;
    }

    return 'Unknown Vendor';
  }

  function chooseBestVendor(baseVendor, attachmentVendor) {
    const baseScore = scoreVendorCandidate(baseVendor);
    const attachmentScore = scoreVendorCandidate(attachmentVendor);

    if (attachmentVendor && (baseScore < 15 || attachmentScore >= baseScore + 5)) {
      return { value: attachmentVendor, score: attachmentScore, source: 'attachment' };
    }

    if (baseVendor) {
      return { value: baseVendor, score: baseScore, source: 'email' };
    }

    if (attachmentVendor) {
      return { value: attachmentVendor, score: attachmentScore, source: 'attachment' };
    }

    return null;
  }

  function scoreVendorCandidate(value) {
    if (!value) return 0;
    const cleaned = String(value || '').trim();
    if (!cleaned || cleaned === 'Unknown Vendor') return 0;

    let score = 20;

    if (cleaned.length >= 4) score += 5;
    if (cleaned.length >= 10) score += 5;
    if (/\b(inc|llc|ltd|corp|gmbh|plc|co\.|company)\b/i.test(cleaned)) score += 6;
    if (/\d/.test(cleaned)) score -= 8;
    if (looksLikeId(cleaned)) score -= 20;
    if (/^(billing|payment|invoice|receipt|statement|notification|alert|support|team|service|account|payments?)$/i.test(cleaned)) score -= 20;

    return score;
  }

  function cleanVendorCandidate(value) {
    if (!value) return null;
    let cleaned = String(value);
    if (cleaned.includes('@')) return null;
    cleaned = cleaned.replace(/<[^>]+>/g, ' ');
    cleaned = cleaned.replace(/\s*[-–—|•]\s*.*$/, ' ');
    cleaned = cleaned.replace(/\s*(?:invoice|receipt|payment|billing|statement|alert|notification)\b.*$/i, ' ');
    cleaned = cleaned.replace(/\s+\b(?:was|is)\b\s+(?:unsuccessful|declined|failed|rejected|refused|not\s+successful|not\s+processed|could\s+not\s+be\s+processed).*$/i, ' ');
    cleaned = cleaned.replace(/\s+\b(?:unsuccessful|declined|failed|rejected|refused)\b.*$/i, ' ');
    cleaned = cleaned.replace(/[$€£]\s*\d.*$/, ' ');
    cleaned = cleaned.replace(/\s+/g, ' ').trim();

    if (cleaned.length < 2) return null;
    if (/^(your|you|this|invoice|payment|receipt|billing|statement|notification|notifications|alert|support|team|service|account|payments?|billing\s+team|no-?reply|noreply)$/i.test(cleaned)) return null;
    if (looksLikeId(cleaned)) return null;
    return cleaned;
  }

  function looksLikeId(value) {
    const raw = String(value || '').trim();
    if (!raw) return false;
    if (raw.length < 4) return false;
    if (raw.includes(' ')) return false;

    const compact = raw.replace(/[^A-Za-z0-9]/g, '');
    if (compact.length < 4) return false;

    const digits = compact.replace(/\D/g, '').length;
    const letters = compact.replace(/[^A-Za-z]/g, '').length;

    if (digits >= 3 && digits >= letters) return true;
    if (digits >= 2 && letters >= 1 && raw.length >= 6) return true;
    if (/^[A-F0-9-]{8,}$/i.test(raw)) return true;
    return false;
  }

  function extractVendorFromSubjectPrefix(subject) {
    if (!subject || !subject.includes(':')) return null;
    const prefix = subject.split(':')[0].trim();
    if (!prefix || prefix.length < 3) return null;
    if (/(invoice|payment|receipt|statement|bill|billing|charge|transaction|alert|notification)/i.test(prefix)) {
      return null;
    }
    const candidate = cleanVendorCandidate(prefix);
    return candidate || null;
  }

  function extractVendorFromSubject(subject) {
    const normalized = normalizeText(subject);
    if (!normalized) return null;

    const financeKeywords = /\b(invoice|receipt|payment|charge|bill|billing|statement|subscription|transaction|payout|refund|failed|declined)\b/i;
    if (!financeKeywords.test(normalized)) return null;

    const patterns = [
      /\b(?:payment|charge|transaction)\b.*?\b(?:to|for|at)\s+([A-Za-z0-9& .,'-]+?)(?:\s+\b(?:was|is)\b\s+(?:unsuccessful|declined|failed|rejected|refused|not\s+successful|not\s+processed)|$)/i,
      /\b(?:payment failed|payment declined|charge failed|card declined)\b.*?\b(?:for|to|at)\s+(.+)$/i,
      /\b(?:invoice|receipt|payment|charge|bill|billing|statement|subscription|transaction|payout|refund)\b.*?\b(?:from|by|at|to|for)\s+(.+)$/i,
      /^([A-Za-z0-9& .,'-]{2,})\s+(?:invoice|receipt|payment|bill|statement|charge)\b/i
    ];

    for (const pattern of patterns) {
      const match = normalized.match(pattern);
      if (match && match[1]) {
        const candidate = cleanVendorCandidate(match[1]);
        if (candidate) return candidate;
      }
    }

    return null;
  }

  function extractVendorFromBody(body) {
    const text = String(body || '');
    if (!text) return null;

    const patterns = [
      /(?:^|\n|\r)\s*(?:vendor|merchant|payee|supplier|seller|billed by|bill from|billing from)\s*[:\-]\s*([^\n]{2,80})/i
    ];

    for (const pattern of patterns) {
      const match = text.match(pattern);
      if (match && match[1]) {
        const candidate = cleanVendorCandidate(match[1]);
        if (candidate) return candidate;
      }
    }

    return null;
  }

  function extractAmountDetails(text) {
    const currencySymbols = [
      '$',
      '\u00a3',
      '\u20ac',
      '\u00a5',
      '\u20a6',
      '\u20b5',
      '\u20b9',
      'R',
      '\u20b1',
      '\u20a9',
      '\u20ab',
      '\u0e3f',
      'RM',
      'Rp'
    ];
    const currencyCodes = [
      'USD', 'EUR', 'GBP', 'GHS', 'NGN', 'KES', 'ZAR', 'INR', 'JPY', 'CNY',
      'CAD', 'AUD', 'CHF', 'PHP', 'KRW', 'VND', 'THB', 'MYR', 'IDR', 'BRL', 'MXN'
    ];

    const source = String(text || '').replace(/\u00a0/g, ' ');
    const candidates = collectAmountCandidates(source, currencySymbols, currencyCodes);
    if (!candidates.length) return null;

    candidates.sort((a, b) => b.score - a.score || b.value - a.value);
    const best = candidates[0];
    if (best.score < 8 && !best.currency) return null;

    return {
      value: best.value,
      formatted: formatAmount(best.value, best.currency),
      currency: best.currency,
      score: best.score,
      context: best.context || ''
    };
  }

  function extractAmount(text) {
    const details = extractAmountDetails(text);
    return details ? details.formatted : null;
  }

  function escapeRegex(value) {
    return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }

  function parseAmountValue(raw) {
    if (raw === null || raw === undefined) return null;
    const cleaned = String(raw).replace(/\s/g, '');
    if (!cleaned) return null;

    const hasComma = cleaned.includes(',');
    const hasDot = cleaned.includes('.');
    let normalized = cleaned;

    if (hasComma && hasDot) {
      if (cleaned.lastIndexOf(',') > cleaned.lastIndexOf('.')) {
        normalized = cleaned.replace(/\./g, '').replace(',', '.');
      } else {
        normalized = cleaned.replace(/,/g, '');
      }
    } else if (hasComma && !hasDot) {
      const parts = cleaned.split(',');
      if (parts.length === 2 && parts[1].length === 2) {
        normalized = parts[0].replace(/\./g, '') + '.' + parts[1];
      } else {
        normalized = cleaned.replace(/,/g, '');
      }
    } else {
      normalized = cleaned.replace(/,/g, '');
    }

    const value = Number(normalized);
    if (!Number.isFinite(value)) return null;
    return value;
  }

  function normalizeDigits(value) {
    return String(value || '').replace(/\D/g, '');
  }

  function amountMatchesInvoice(amountValue, invoiceValue) {
    if (amountValue === null || amountValue === undefined || !invoiceValue) return false;
    const amountNumber = Number(amountValue);
    if (!Number.isFinite(amountNumber)) return false;

    const invoiceDigits = normalizeDigits(invoiceValue);
    if (!invoiceDigits || invoiceDigits.length < 6) return false;

    const amountDigits = normalizeDigits(amountNumber);
    if (!amountDigits) return false;

    if (amountDigits === invoiceDigits) return true;
    if (invoiceDigits.length >= 8 && amountNumber >= 10000000 && Number.isInteger(amountNumber)) {
      return true;
    }
    return false;
  }

  function collectAmountCandidates(text, currencySymbols, currencyCodes) {
    const candidates = [];
    const seen = new Set();

    const numberPattern = '(?:\\d{1,3}(?:[.,]\\d{3})+|\\d+)(?:[.,]\\d{2})?';
    const codesPattern = currencyCodes.join('|');
    const symbolsPattern = currencySymbols.map(escapeRegex).join('|');

    const patterns = [
      {
        regex: new RegExp(`\\b(${codesPattern})\\s*(${numberPattern})\\b`, 'gi'),
        currencyIndex: 1,
        amountIndex: 2
      },
      {
        regex: new RegExp(`\\b(${numberPattern})\\s*(${codesPattern})\\b`, 'gi'),
        currencyIndex: 2,
        amountIndex: 1
      },
      {
        regex: new RegExp(`(?:^|\\s)(${symbolsPattern})\\s*(${numberPattern})`, 'gi'),
        currencyIndex: 1,
        amountIndex: 2
      },
      {
        regex: new RegExp(`\\b(?:total|amount due|balance due|amount|due|paid|payment failed|charged)\\b[^0-9]{0,10}(${numberPattern})\\b`, 'gi'),
        currencyIndex: null,
        amountIndex: 1
      },
      {
        regex: new RegExp(`\\b((?:\\d{1,3}(?:,\\d{3})+|\\d+)\\.\\d{2})\\b`, 'gi'),
        currencyIndex: null,
        amountIndex: 1
      }
    ];

    const looksLikeYear = (val) => Number.isInteger(val) && val >= 1900 && val <= 2105;

    const addCandidate = (value, currency, index) => {
      if (value === null || value === undefined) return;
      if (value < 0 || value > 10000000) return;
      const key = `${index}|${value}|${currency || ''}`;
      if (seen.has(key)) return;

      const context = getAmountContext(text, index);
      const hasAmountWord = /\b(amount|total|due|balance|payment|paid|charge|charged|usd|eur|gbp|cad|aud)\b/i.test(
        context
      );
      const hasDateWord = /\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|date|issued|invoice date)\b/i.test(
        context
      );
      if (!hasAmountWord && hasDateWord && looksLikeYear(value)) return;

      const score = scoreAmountCandidate(context, value, currency);
      candidates.push({ value, currency, score, context });
      seen.add(key);
    };

    patterns.forEach(({ regex, currencyIndex, amountIndex }) => {
      let match;
      while ((match = regex.exec(text)) !== null) {
        const rawAmount = match[amountIndex];
        const value = parseAmountValue(rawAmount);
        if (value === null) continue;

        let currency = '';
        if (currencyIndex) {
          const rawCurrency = String(match[currencyIndex]);
          currency = /^[A-Za-z]{2,3}$/.test(rawCurrency) ? rawCurrency.toUpperCase() : rawCurrency;
        }
        addCandidate(value, currency, match.index);
      }
    });

    return candidates;
  }

  function getAmountContext(text, index) {
    const start = text.lastIndexOf('\n', index);
    const end = text.indexOf('\n', index);
    let context = text.slice(start + 1, end === -1 ? text.length : end);
    context = normalizeText(context);

    if (!context || context.length < 8) {
      const windowStart = Math.max(0, index - 40);
      const windowEnd = Math.min(text.length, index + 40);
      context = normalizeText(text.slice(windowStart, windowEnd));
    }

    return context;
  }

  function scoreAmountCandidate(context, value, currency) {
    const ctx = normalizeText(context).toLowerCase();
    let score = 0;

    if (/total\s+due|amount\s+due|balance\s+due|grand\s+total|total\s+amount/.test(ctx)) score += 45;
    if (/\btotal\b/.test(ctx)) score += 20;
    if (/\bamount\b/.test(ctx)) score += 12;
    if (/\bpayment failed\b|\bpayment declined\b|\bpayment\b|\bcharged\b|\bcharge\b|\bpaid\b|\bbilled\b/.test(ctx)) score += 12;
    if (/\bsubtotal\b|\btax\b|\bvat\b|\bshipping\b|\bdiscount\b|\bfee\b|\btip\b|\binterest\b/.test(ctx)) score -= 20;
    if (/\b(invoice|order)\s*#/.test(ctx) && !/\bamount|total|due|payment|charged|paid\b/.test(ctx)) score -= 12;
    if (/\baccount\b|\breference\b|\border\b/.test(ctx) && !/\bamount|total|due|payment|charged|paid\b/.test(ctx)) score -= 12;

    if (currency) score += 5;
    if (currency === 'R' && !/\bamount|total|due|payment|charged|paid\b/.test(ctx)) score -= 10;
    if (value >= 100) score += 2;
    if (value < 2) score -= 4;
    if (value >= 1900 && value <= 2105 && /jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|date/.test(ctx) && !/\bamount|total|due|balance|payment|paid|charge|charged\b/.test(ctx)) {
      score -= 50;
    }

    return score;
  }

  function formatAmount(value, currency) {
    const formatted = Number(value).toLocaleString('en-US', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    });

    if (!currency) return formatted;
    return `${currency} ${formatted}`;
  }

  function extractInvoiceNumber(text, attachments) {
    const details = extractInvoiceNumberDetails(text, attachments);
    return details ? details.value : null;
  }

  function extractInvoiceNumberFromText(text) {
    const details = extractInvoiceNumberDetails(text);
    return details ? details.value : null;
  }

  function extractInvoiceNumberDetails(text, attachments) {
    const cleaned = normalizeText(text).replace(/[_]/g, ' ');
    const candidates = collectInvoiceCandidatesFromText(cleaned, 0, 'text');

    const attachmentList = attachments || [];
    attachmentList.forEach((attachment) => {
      const name = String(attachment?.name || '').trim();
      if (!name) return;
      const fromName = collectInvoiceCandidatesFromText(name, -20, 'filename');
      candidates.push(...fromName);
    });

    if (!candidates.length) return null;

    candidates.sort((a, b) => b.score - a.score || b.value.length - a.value.length);
    return candidates[0];
  }

  function collectInvoiceCandidatesFromText(text, scoreAdjust, source) {
    if (!text) return [];
    const candidates = [];
    const patterns = [
      { regex: /\binvoice\s*(?:number|no\.?|#|id)?\s*[:#-]?\s*([A-Z0-9][A-Z0-9\-\/]{2,})/ig, score: 90 },
      { regex: /\binv\s*(?:number|no\.?|#|id)?\s*[:#-]?\s*([A-Z0-9][A-Z0-9\-\/]{2,})/ig, score: 70 },
      { regex: /\b(reference|ref|order|statement|bill)\s*(?:number|no\.?|#|id)?\s*[:#-]?\s*([A-Z0-9][A-Z0-9\-\/]{2,})/ig, score: 50, group: 2 },
      { regex: /\binvoice\b[^A-Za-z0-9]{0,6}([A-Z0-9][A-Z0-9\-\/]{2,})/ig, score: 45 }
    ];

    patterns.forEach((pattern) => {
      let match;
      while ((match = pattern.regex.exec(text)) !== null) {
        const groupIndex = pattern.group || 1;
        const raw = match[groupIndex];
        const candidate = normalizeInvoiceCandidate(raw);
        if (!candidate) continue;
        const context = getInvoiceContext(text, match.index);
        let score = scoreInvoiceCandidate(context, candidate, pattern.score);
        score += scoreAdjust || 0;
        candidates.push({ value: candidate, score, context, source });
      }
    });

    return candidates;
  }

  function normalizeInvoiceCandidate(value) {
    if (!value) return null;
    let cleaned = String(value).trim();
    cleaned = cleaned.replace(/^[#:\-]/g, '');
    cleaned = cleaned.replace(/[^\w\-\/]/g, '');
    cleaned = cleaned.replace(/[_]/g, '-');
    if (cleaned.length < 3 || cleaned.length > 30) return null;
    if (!/\d/.test(cleaned)) return null;
    if (isLikelyDateToken(cleaned)) return null;
    return cleaned.toUpperCase();
  }

  function getInvoiceContext(text, index) {
    const start = Math.max(0, index - 60);
    const end = Math.min(text.length, index + 60);
    return normalizeText(text.slice(start, end));
  }

  function scoreInvoiceCandidate(context, candidate, baseScore) {
    const ctx = normalizeText(context).toLowerCase();
    let score = baseScore || 0;

    if (/\binvoice\s*number|\binvoice\s*#|\binvoice\s*id/.test(ctx)) score += 20;
    if (/\binvoice\b/.test(ctx)) score += 8;
    if (/\bstatement\b|\border\b|\breference\b/.test(ctx)) score += 4;
    if (/\binvoice\s+for\b/.test(ctx)) score -= 12;
    if (candidate.length >= 8) score += 4;
    if (/[A-Z]/.test(candidate) && /\d/.test(candidate)) score += 3;
    if (/^\d{10,}$/.test(candidate)) score -= 2;

    return score;
  }

  function isLikelyDateToken(value) {
    return (
      /^\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}$/.test(value) ||
      /^\d{4}[\/\-]\d{1,2}[\/\-]\d{1,2}$/.test(value)
    );
  }

  function extractDate(text) {
    const details = extractDateDetails(text);
    return details ? details.value : null;
  }

  function extractDateDetails(text) {
    if (!text) return null;
    const candidates = [];
    const patterns = [
      { regex: /\b(invoice date|date of issue|issued on|issue date)\b[:\s]*([A-Za-z0-9,\-/ ]{6,})/ig, score: 90, group: 2 },
      { regex: /\b(due date|payment due|pay by)\b[:\s]*([A-Za-z0-9,\-/ ]{6,})/ig, score: 70, group: 2 },
      { regex: /\b(date|dated|issued)\b[:\s]*([A-Za-z0-9,\-/ ]{6,})/ig, score: 55, group: 2 },
      { regex: /\b(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})\b/g, score: 45, group: 1 },
      { regex: /\b(\d{4}[\/\-]\d{1,2}[\/\-]\d{1,2})\b/g, score: 40, group: 1 },
      { regex: /\b(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})\b/g, score: 35, group: 1 },
      { regex: /\b([A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})\b/g, score: 35, group: 1 }
    ];

    patterns.forEach((pattern) => {
      let match;
      while ((match = pattern.regex.exec(text)) !== null) {
        const raw = match[pattern.group || 1];
        const candidate = normalizeDateCandidate(raw);
        if (!candidate) continue;
        const context = getDateContext(text, match.index);
        const score = scoreDateCandidate(context, candidate, pattern.score);
        candidates.push({ value: candidate, score, context });
      }
    });

    if (!candidates.length) return null;
    candidates.sort((a, b) => b.score - a.score);
    return candidates[0];
  }

  function normalizeDateCandidate(value) {
    if (!value) return null;
    const cleaned = String(value).trim().replace(/\s+/g, ' ');
    if (cleaned.length < 6 || cleaned.length > 30) return null;

    const hasDigit = /\d/.test(cleaned);
    const hasMonth = /\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|january|february|march|april|june|july|august|september|october|november|december)\b/i.test(cleaned);
    if (!hasDigit && !hasMonth) return null;

    if (/\bbilling\s+id\b/i.test(cleaned)) return null;
    if (/\bdomain\s+name\b/i.test(cleaned)) return null;
    if (/billing\s+period|statement\s+period/i.test(cleaned)) return null;
    return cleaned;
  }

  function getDateContext(text, index) {
    const start = Math.max(0, index - 50);
    const end = Math.min(text.length, index + 50);
    return normalizeText(text.slice(start, end));
  }

  function scoreDateCandidate(context, candidate, baseScore) {
    const ctx = normalizeText(context).toLowerCase();
    let score = baseScore || 0;
    if (/\binvoice date|\bissue date|\bdate of issue/.test(ctx)) score += 12;
    if (/\bdue date|\bpayment due|\bpay by/.test(ctx)) score -= 6;
    if (/\bbilling period|\bstatement period/.test(ctx)) score -= 8;
    if (/\bdate\b/.test(ctx)) score += 4;
    if (candidate.length >= 10) score += 2;
    return score;
  }

  function extractPaymentTerms(text) {
    const patterns = [
      /net\s*(\d+)/i,
      /due\s*in\s*(\d+)\s*days?/i,
      /payment\s*terms?\s*:\s*net\s*(\d+)/i
    ];

    for (const pattern of patterns) {
      const match = text.match(pattern);
      if (match && match[1]) {
        return `Net ${match[1]}`;
      }
    }
    return null;
  }

  function chooseBestAmount(base, attachment) {
    if (attachment && (!base || base.score < 20 || attachment.score >= base.score + 8)) {
      return { ...attachment, source: 'attachment' };
    }
    if (base) {
      return { ...base, source: 'email' };
    }
    if (attachment) {
      return { ...attachment, source: 'attachment' };
    }
    return null;
  }

  function chooseBestInvoice(base, attachment) {
    if (attachment && (!base || attachment.score >= base.score + 5)) {
      return { ...attachment, source: 'attachment' };
    }
    if (base) {
      return { ...base, source: 'email' };
    }
    if (attachment) {
      return { ...attachment, source: 'attachment' };
    }
    return null;
  }

  function chooseBestDate(base, attachment) {
    if (attachment && (!base || attachment.score >= base.score + 5)) {
      return { ...attachment, source: 'attachment' };
    }
    if (base) {
      return { ...base, source: 'email' };
    }
    if (attachment) {
      return { ...attachment, source: 'attachment' };
    }
    return null;
  }

  async function extractAttachmentText(attachments, options = {}) {
    const pdfjs = window.pdfjsLib;
    const attachmentList = attachments || [];
    if (!attachmentList.length) {
      return { text: '', reason: 'No attachments' };
    }

    const maxBytes = options.maxBytes || 8 * 1024 * 1024;
    const maxPages = options.maxPages || 4;
    const maxChars = options.maxChars || 16000;
    const maxCandidates = options.maxCandidates || 3;
    let lastError = '';

    if (pdfjs && pdfjs.GlobalWorkerOptions && !pdfjs.GlobalWorkerOptions.workerSrc && window.chrome?.runtime?.getURL) {
      pdfjs.GlobalWorkerOptions.workerSrc = window.chrome.runtime.getURL('vendor/pdfjs/pdf.worker.min.js');
    }

    const candidates = [];
    (attachmentList || []).forEach((attachment) => {
      const type = getAttachmentType(attachment);
      if (type === 'unknown') return;
      const score = scoreAttachment(attachment, type);
      candidates.push({ attachment, type, score });
    });

    if (!candidates.length && attachmentList.length) {
      const fallbackType = getAttachmentType(attachmentList[0]) !== 'unknown' ? getAttachmentType(attachmentList[0]) : 'pdf';
      candidates.push({ attachment: attachmentList[0], type: fallbackType, score: 10 });
    }
    candidates.sort((a, b) => b.score - a.score);
    if (!candidates.length) {
      return { text: '', reason: 'No supported attachment types' };
    }

    let best = null;

    for (const candidate of candidates.slice(0, maxCandidates)) {
      try {
        const result = await extractTextFromAttachment(candidate.attachment, candidate.type, {
          maxBytes,
          maxPages,
          maxChars
        });
        if (result?.text) {
          const quality = scoreExtractedText(result.text);
          const score = quality + candidate.score;
          if (!best || score > best.score) {
            best = {
              ...result,
              score,
              quality,
              type: candidate.type
            };
          }
          if (quality >= 70) {
            break;
          }
        } else if (result?.reason) {
          lastError = result.reason;
        }
      } catch (error) {
        console.warn('[Clearledgr] Attachment parse error:', error);
        lastError = error?.message || 'Attachment parse error';
      }
    }

    if (best && best.text) {
      return {
        text: best.text,
        name: best.name || 'attachment',
        pages: best.pages,
        type: best.type,
        quality: best.quality
      };
    }

    return { text: '', reason: lastError || 'No extractable text found' };
  }

  async function extractAttachmentPayloads(attachments, options = {}) {
    const attachmentList = attachments || [];
    if (!attachmentList.length) return [];

    const pdfjs = window.pdfjsLib;
    if (pdfjs && pdfjs.GlobalWorkerOptions && !pdfjs.GlobalWorkerOptions.workerSrc && window.chrome?.runtime?.getURL) {
      pdfjs.GlobalWorkerOptions.workerSrc = window.chrome.runtime.getURL('vendor/pdfjs/pdf.worker.min.js');
    }

    const includeImages = options.includeImages !== false;
    let candidates = buildAttachmentCandidates(attachmentList, { includeImages })
      .filter((candidate) => candidate.type === 'pdf' || candidate.type === 'image');
    if (!candidates.length && attachmentList.length) {
      const firstType = getAttachmentType(attachmentList[0]);
      const useType = firstType === 'unknown' ? 'pdf' : firstType;
      candidates = [{ attachment: attachmentList[0], type: useType, score: 5 }];
    }
    if (!candidates.length) return [];

    const maxCandidates = options.maxCandidates || 1;
    const payloads = [];

    for (const candidate of candidates.slice(0, maxCandidates)) {
      try {
        const payload = await buildAttachmentPayload(candidate.attachment, candidate.type, options);
        if (payload) payloads.push(payload);
      } catch (error) {
        console.warn('[Clearledgr] Attachment payload error:', error);
      }
    }

    return payloads;
  }

  async function buildAttachmentPayload(attachment, type, options) {
    if (!attachment) return null;

    const fileName = attachment.name || attachment.filename || 'attachment';
    const maxPdfBytes = options.maxPdfBytes || 6 * 1024 * 1024;
    const maxImageBytes = options.maxImageBytes || 4 * 1024 * 1024;
    const maxWidth = options.maxWidth || 1200;

    if (type === 'image') {
      const buffer = await fetchAttachmentArrayBuffer(attachment, maxImageBytes);
      if (!buffer) return null;
      const contentType = attachment.mimeType || attachment.type || 'image/png';
      return {
        filename: fileName,
        contentType,
        contentBase64: arrayBufferToBase64(buffer)
      };
    }

    if (type === 'pdf') {
      const buffer = await fetchAttachmentArrayBuffer(attachment, maxPdfBytes);
      if (!buffer) return null;
      const base64 = await renderPdfToPngBase64(buffer, maxWidth);
      if (!base64) return null;
      return {
        filename: fileName,
        contentType: 'image/png',
        contentBase64: base64
      };
    }

    return null;
  }

  async function renderPdfToPngBase64(buffer, maxWidth) {
    const pdfjs = window.pdfjsLib;
    if (!pdfjs) return null;

    const doc = await pdfjs.getDocument({ data: buffer }).promise;
    if (!doc || !doc.numPages) return null;

    const page = await doc.getPage(1);
    const baseViewport = page.getViewport({ scale: 1 });
    const scale = baseViewport.width > maxWidth ? maxWidth / baseViewport.width : 1;
    const viewport = page.getViewport({ scale });

    const canvas = document.createElement('canvas');
    canvas.width = Math.round(viewport.width);
    canvas.height = Math.round(viewport.height);

    const ctx = canvas.getContext('2d', { alpha: false });
    if (!ctx) return null;

    await page.render({ canvasContext: ctx, viewport }).promise;
    const dataUrl = canvas.toDataURL('image/png');
    const base64 = dataUrl.split(',')[1];
    return base64 || null;
  }

  async function fetchAttachmentArrayBuffer(attachment, maxBytes) {
    const url = getAttachmentUrl(attachment);
    if (!url) return null;

    const response = await fetch(url, { credentials: 'include' });
    if (!response.ok) {
      return null;
    }

    const contentLength = parseInt(response.headers.get('content-length') || '0', 10);
    if (contentLength && contentLength > maxBytes) {
      return null;
    }

    const buffer = await response.arrayBuffer();
    if (buffer.byteLength > maxBytes) {
      return null;
    }

    return buffer;
  }

  function arrayBufferToBase64(buffer) {
    const bytes = new Uint8Array(buffer);
    const chunkSize = 0x8000;
    let binary = '';

    for (let i = 0; i < bytes.length; i += chunkSize) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunkSize));
    }

    return btoa(binary);
  }

  async function extractTextFromAttachment(attachment, type, options) {
    if (type === 'pdf') {
      return extractPdfAttachment(attachment, options);
    }
    if (type === 'image') {
      // Optional OCR hook could be added here
      return { text: '', reason: 'Image attachments require OCR' };
    }
    if (!type || type === 'unknown') {
      return { text: '', reason: 'Unsupported attachment type' };
    }
    return extractTextAttachment(attachment, type, options);
  }

  async function extractPdfAttachment(attachment, options) {
    const pdfjs = window.pdfjsLib;
    if (!pdfjs) {
      return { text: '', reason: 'PDF parser unavailable' };
    }

    const url = getAttachmentUrl(attachment);
    if (!url) return { text: '', reason: 'Attachment URL missing' };

    const response = await fetch(url, { credentials: 'include' });
    if (!response.ok) {
      return { text: '', reason: `Attachment fetch failed (${response.status})` };
    }

    const maxBytes = options.maxBytes || 8 * 1024 * 1024;
    const maxPages = options.maxPages || 4;
    const maxChars = options.maxChars || 16000;

    const contentLength = parseInt(response.headers.get('content-length') || '0', 10);
    if (contentLength && contentLength > maxBytes) {
      return { text: '', reason: 'Attachment too large' };
    }

    const buffer = await response.arrayBuffer();
    if (buffer.byteLength > maxBytes) {
      return { text: '', reason: 'Attachment too large' };
    }

    const doc = await pdfjs.getDocument({ data: buffer }).promise;
    const pageCount = Math.min(doc.numPages || 0, maxPages);
    let text = '';

    for (let pageNum = 1; pageNum <= pageCount; pageNum += 1) {
      const page = await doc.getPage(pageNum);
      const content = await page.getTextContent();
      const strings = content.items.map((item) => item.str).join(' ');
      text += `\n${strings}`;
      if (text.length >= maxChars) break;
    }

    const normalized = normalizeText(text).slice(0, maxChars);
    if (!normalized) {
      return { text: '', reason: 'No extractable text found' };
    }

    return {
      text: normalized,
      name: attachment.name || 'attachment',
      pages: pageCount
    };
  }

  async function extractTextAttachment(attachment, type, options) {
    const url = getAttachmentUrl(attachment);
    if (!url) return { text: '', reason: 'Attachment URL missing' };

    const response = await fetch(url, { credentials: 'include' });
    if (!response.ok) {
      return { text: '', reason: `Attachment fetch failed (${response.status})` };
    }

    const maxBytes = options.maxBytes || 2 * 1024 * 1024;
    const maxChars = options.maxChars || 16000;
    const contentLength = parseInt(response.headers.get('content-length') || '0', 10);
    if (contentLength && contentLength > maxBytes) {
      return { text: '', reason: 'Attachment too large' };
    }

    const buffer = await response.arrayBuffer();
    if (buffer.byteLength > maxBytes) {
      return { text: '', reason: 'Attachment too large' };
    }

    let decoded = '';
    try {
      const contentType = response.headers.get('content-type') || '';
      const charsetMatch = contentType.match(/charset=([^;]+)/i);
      const charset = charsetMatch ? charsetMatch[1].trim() : 'utf-8';
      decoded = new TextDecoder(charset, { fatal: false }).decode(buffer);
    } catch (err) {
      decoded = new TextDecoder('utf-8', { fatal: false }).decode(buffer);
    }

    if (!decoded) {
      return { text: '', reason: 'Attachment empty' };
    }

    if (isMostlyBinary(decoded)) {
      return { text: '', reason: 'Attachment not text-based' };
    }

    const cleaned = normalizeAttachmentText(decoded, type);
    if (!cleaned) {
      return { text: '', reason: 'No extractable text found' };
    }

    return {
      text: cleaned.slice(0, maxChars),
      name: attachment.name || 'attachment'
    };
  }

  function normalizeAttachmentText(text, type) {
    let cleaned = String(text || '');
    if (!cleaned) return '';

    if (type === 'html' || type === 'xml') {
      cleaned = stripHtml(cleaned);
    } else if (type === 'json') {
      cleaned = normalizeJsonText(cleaned);
    } else if (type === 'csv' || type === 'tsv') {
      cleaned = cleaned.replace(/[,;\t]/g, ' ');
    } else if (type === 'rtf') {
      cleaned = stripRtf(cleaned);
    } else if (type === 'eml') {
      cleaned = extractBodyFromEml(cleaned);
    }

    return normalizeText(cleaned);
  }

  function normalizeJsonText(raw) {
    try {
      const parsed = JSON.parse(raw);
      return JSON.stringify(parsed, null, 2);
    } catch (err) {
      return raw;
    }
  }

  function extractBodyFromEml(raw) {
    const splitIndex = raw.search(/\r?\n\r?\n/);
    if (splitIndex === -1) return raw;
    return raw.slice(splitIndex).replace(/\r?\n/g, ' ').trim();
  }

  function stripRtf(text) {
    return String(text || '')
      .replace(/\\par[d]?/gi, ' ')
      .replace(/\\'[0-9a-fA-F]{2}/g, ' ')
      .replace(/\\[a-z]+\d*/gi, ' ')
      .replace(/[{}]/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function isMostlyBinary(text) {
    if (!text) return true;
    if (text.includes('\u0000')) return true;
    const sample = text.slice(0, 2000);
    const controlChars = sample.match(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g) || [];
    return controlChars.length / Math.max(sample.length, 1) > 0.05;
  }

  function scoreExtractedText(text) {
    const normalized = normalizeText(text).toLowerCase();
    let score = 0;

    if (normalized.length > 200) score += 8;
    if (normalized.length > 1000) score += 10;
    if (normalized.length > 4000) score += 6;

    if (/\binvoice\b|\bbill\b|\bstatement\b|\breceipt\b/.test(normalized)) score += 20;
    if (/total\s+due|amount\s+due|balance\s+due|grand\s+total/.test(normalized)) score += 25;
    if (/\bsubtotal\b|\btax\b|\bvat\b/.test(normalized)) score += 6;
    if (/\bpayment\b|\bcharged\b|\bpaid\b|\bamount\b/.test(normalized)) score += 8;
    if (/\b(invoice|inv|reference|order)\s*(#|no\.?|number|id)\b/.test(normalized)) score += 15;
    if (/\b(usd|eur|gbp|zar|aud|cad|jpy|inr)\b/.test(normalized)) score += 8;
    if (/[€£$]\s*\d/.test(text)) score += 8;

    return score;
  }

  function buildAttachmentCandidates(attachments, options = {}) {
    const includeImages = options.includeImages === true;
    const candidates = [];
    (attachments || []).forEach((attachment) => {
      const type = getAttachmentType(attachment);
      if (type === 'unknown') return;
      if (type === 'image' && !includeImages) return;
      const score = scoreAttachment(attachment, type);
      candidates.push({ attachment, type, score });
    });

    candidates.sort((a, b) => b.score - a.score);
    return candidates;
  }

  function scoreAttachment(attachment, type) {
    const name = String(attachment?.name || '').toLowerCase();
    let score = 0;

    if (/(invoice|receipt|statement|bill|billing|remittance|remit)/.test(name)) score += 35;
    if (/(payment|charge|order|subscription|ap|ar)/.test(name)) score += 12;
    if (/\b(credit|debit)\b/.test(name)) score += 6;

    const typeBoost = {
      pdf: 40,
      html: 28,
      text: 22,
      csv: 18,
      tsv: 18,
      xml: 16,
      json: 12,
      rtf: 12,
      eml: 10,
      image: 26
    };
    score += typeBoost[type] || 0;
    return score;
  }

  function getAttachmentType(attachment) {
    const name = (attachment?.name || '').toLowerCase();
    const mime = (attachment?.mimeType || attachment?.type || '').toLowerCase();
    const downloadUrl = (attachment?.downloadUrl || '').toLowerCase();

    const looksPdf =
      mime.includes('pdf') ||
      downloadUrl.startsWith('application/pdf') ||
      downloadUrl.includes('pdf') ||
      name.endsWith('.pdf') ||
      name.includes('pdf');
    if (looksPdf) {
      return 'pdf';
    }
    if (mime.includes('text/plain') || name.endsWith('.txt') || name.endsWith('.log')) {
      return 'text';
    }
    if (mime.includes('text/csv') || name.endsWith('.csv')) {
      return 'csv';
    }
    if (mime.includes('text/tab-separated-values') || name.endsWith('.tsv')) {
      return 'tsv';
    }
    if (mime.includes('text/html') || name.endsWith('.html') || name.endsWith('.htm')) {
      return 'html';
    }
    if (mime.includes('application/json') || name.endsWith('.json')) {
      return 'json';
    }
    if (mime.includes('application/xml') || mime.includes('text/xml') || name.endsWith('.xml')) {
      return 'xml';
    }
    if (mime.includes('text/rtf') || name.endsWith('.rtf')) {
      return 'rtf';
    }
    if (mime.includes('message/rfc822') || name.endsWith('.eml')) {
      return 'eml';
    }
    if (mime.startsWith('image/') || /\.(png|jpe?g|gif|tiff|bmp)$/.test(name)) {
      return 'image';
    }

    return 'unknown';
  }

  function getAttachmentUrl(attachment) {
    const raw = attachment?.url || attachment?.downloadUrl || '';
    if (!raw) return '';
    const httpsIndex = raw.indexOf('https://');
    if (httpsIndex !== -1) return raw.slice(httpsIndex);
    const httpIndex = raw.indexOf('http://');
    if (httpIndex !== -1) return raw.slice(httpIndex);
    return raw;
  }

  window.ClearledgrEmailParsing = {
    parseFinancialData,
    extractVendor,
    extractAmount,
    extractAmountDetails,
    extractInvoiceNumber,
    extractInvoiceNumberDetails,
    extractDate,
    extractDateDetails,
    extractPaymentTerms,
    extractAttachmentText,
    extractAttachmentPayloads
  };
})();
