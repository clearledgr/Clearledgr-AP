/**
 * DiscoveryEngine - Core intelligence for identifying reconcile-able transactions
 * 
 * This engine analyzes email threads to determine if they contain financial
 * documents that can be matched against bank feeds or ERP records.
 * 
 * Classification Types:
 * - INVOICE: Payable document requiring GL posting
 * - REMITTANCE: Payment advice to match against bank feed
 * - STATEMENT: Bank/vendor statement for reconciliation
 * - RECEIPT: Proof of payment for audit trail
 * - EXCEPTION: Requires human review (disputes, chargebacks, etc.)
 * - NOISE: Non-financial email
 */

const DiscoveryEngine = {
  // Classification confidence thresholds
  CONFIDENCE_THRESHOLD: 0.75,
  HIGH_CONFIDENCE: 0.90,
  
  // Known financial senders (domain patterns)
  FINANCIAL_SENDERS: {
    payment_processors: [
      'stripe.com', 'paypal.com', 'square.com', 'adyen.com',
      'braintree.com', 'checkout.com', 'paystack.com', 'flutterwave.com'
    ],
    banks: [
      'deutsche-bank', 'hsbc.com', 'barclays', 'chase.com', 'wellsfargo',
      'bankofamerica', 'citi.com', 'jpmorgan', 'ubs.com', 'credit-suisse',
      'revolut.com', 'wise.com', 'mercury.com', 'brex.com', 'ramp.com'
    ],
    erp_accounting: [
      'sap.com', 'oracle.com', 'netsuite.com', 'quickbooks', 'xero.com',
      'sage.com', 'freshbooks', 'bill.com', 'tipalti.com', 'coupa.com'
    ],
    expense_management: [
      'expensify.com', 'concur.com', 'divvy.com', 'airbase.io',
      'spendesk.com', 'pleo.io', 'soldo.com'
    ]
  },
  
  // Document type patterns
  DOCUMENT_PATTERNS: {
    INVOICE: {
      subject: [
        /invoice\s*#?\s*[\w-]+/i,
        /inv[- ]?\d+/i,
        /bill\s+(for|from)/i,
        /amount\s+due/i,
        /payment\s+request/i,
        /please\s+pay/i
      ],
      body: [
        /total\s*(amount|due)[\s:]*[\d€$£,]+/i,
        /invoice\s*(number|no|#)[\s:]*[\w-]+/i,
        /due\s*date[\s:]/i,
        /payment\s+terms/i,
        /remit\s+to/i,
        /bank\s+details/i
      ],
      attachments: ['invoice', 'inv_', 'bill_', 'rechnung']
    },
    
    REMITTANCE: {
      subject: [
        /remittance\s+advice/i,
        /payment\s+(confirmation|notification|advice)/i,
        /payout\s+(complete|sent|processed)/i,
        /funds?\s+(transfer|sent|received)/i,
        /wire\s+transfer/i,
        /ach\s+(payment|transfer)/i
      ],
      body: [
        /payment\s+reference[\s:]/i,
        /transaction\s+id[\s:]/i,
        /amount\s+(paid|transferred|sent)[\s:]/i,
        /credited\s+to\s+your\s+account/i,
        /settlement\s+amount/i
      ],
      attachments: ['remittance', 'payment_advice', 'payout']
    },
    
    STATEMENT: {
      subject: [
        /statement\s+(is\s+)?ready/i,
        /(bank|account|monthly)\s+statement/i,
        /your\s+\w+\s+statement/i,
        /statement\s+for\s+\w+/i
      ],
      body: [
        /opening\s+balance/i,
        /closing\s+balance/i,
        /statement\s+period/i,
        /account\s+(number|ending)/i,
        /transaction\s+history/i
      ],
      attachments: ['statement', 'stmt_', 'account_summary']
    },
    
    RECEIPT: {
      subject: [
        /receipt\s+(for|from)/i,
        /thank\s+you\s+for\s+(your\s+)?(payment|purchase|order)/i,
        /order\s+confirmation/i,
        /payment\s+received/i
      ],
      body: [
        /receipt\s*(number|no|#)/i,
        /order\s*(number|no|#)/i,
        /thank\s+you\s+for\s+your\s+(payment|purchase)/i,
        /payment\s+method[\s:]/i
      ],
      attachments: ['receipt', 'order_confirm', 'purchase']
    },
    
    EXCEPTION: {
      subject: [
        /dispute/i,
        /chargeback/i,
        /refund\s+request/i,
        /payment\s+(failed|declined|rejected)/i,
        /insufficient\s+funds/i,
        /overdue/i,
        /past\s+due/i,
        /collection/i,
        /urgent[\s:]/i
      ],
      body: [
        /dispute\s+(case|id|reference)/i,
        /chargeback\s+(notification|alert)/i,
        /refund\s+(processed|pending|request)/i,
        /payment\s+(failure|declined)/i,
        /action\s+required/i,
        /respond\s+by/i
      ],
      attachments: ['dispute', 'chargeback', 'refund']
    }
  },
  
  // Amount extraction patterns (multi-currency)
  AMOUNT_PATTERNS: [
    /[€$£¥]\s*([\d,]+\.?\d*)/,
    /([\d,]+\.?\d*)\s*[€$£¥]/,
    /([\d,]+\.?\d*)\s*(EUR|USD|GBP|CHF|CAD|AUD)/i,
    /(EUR|USD|GBP|CHF|CAD|AUD)\s*([\d,]+\.?\d*)/i,
    /(?:total|amount|sum|due)[\s:]*[€$£]?\s*([\d,]+\.\d{2})/i
  ],
  
  /**
   * Analyze an email thread for reconcile-able transactions
   * @param {Object} emailThread - Gmail thread object with messages
   * @returns {Object} Discovery result with classification and extracted data
   */
  async analyzeThread(emailThread) {
    const result = {
      threadId: emailThread.id,
      isReconcileable: false,
      classification: 'NOISE',
      confidence: 0,
      extractedData: null,
      matchPotential: null,
      suggestedActions: [],
      analysisTimestamp: new Date().toISOString()
    };
    
    try {
      // Get the primary message (usually the first/latest)
      const primaryMessage = this._getPrimaryMessage(emailThread);
      if (!primaryMessage) return result;
      
      // Extract email components
      const emailData = this._extractEmailComponents(primaryMessage);
      
      // Step 1: Sender classification
      const senderScore = this._analyzeSender(emailData.sender);
      
      // Step 2: Subject classification
      const subjectAnalysis = this._analyzeSubject(emailData.subject);
      
      // Step 3: Body classification (if available)
      const bodyAnalysis = this._analyzeBody(emailData.body);
      
      // Step 4: Attachment analysis
      const attachmentAnalysis = this._analyzeAttachments(emailData.attachments);
      
      // Step 5: Combine signals for final classification
      const classification = this._computeClassification({
        senderScore,
        subjectAnalysis,
        bodyAnalysis,
        attachmentAnalysis
      });
      
      result.classification = classification.type;
      result.confidence = classification.confidence;
      result.isReconcileable = classification.confidence >= this.CONFIDENCE_THRESHOLD && 
                               classification.type !== 'NOISE';
      
      // Step 6: Extract financial data if reconcileable
      if (result.isReconcileable) {
        result.extractedData = this._extractFinancialData(emailData, classification.type);
        result.suggestedActions = this._determineSuggestedActions(classification.type, result.extractedData);
      }
      
      return result;
      
    } catch (error) {
      console.error('[DiscoveryEngine] Analysis failed:', error);
      result.error = error.message;
      return result;
    }
  },
  
  /**
   * Quick classification for inbox scanning (lighter weight)
   * @param {Object} email - Basic email object with subject, sender, snippet
   * @returns {Object} Quick classification result
   */
  quickClassify(email) {
    const subject = (email.subject || '').toLowerCase();
    const sender = (email.sender || '').toLowerCase();
    const snippet = (email.snippet || '').toLowerCase();
    
    // FIRST: Check if this looks like a conversation/discussion, not an actual document
    const isConversation = this._isLikelyConversation(subject, sender, snippet);
    if (isConversation.likely) {
      return { 
        type: 'NOISE', 
        confidence: 0.85, 
        reason: 'conversation',
        isConversation: true 
      };
    }
    
    // Check sender first (fastest signal)
    const senderType = this._getSenderType(sender);
    if (!senderType && !this._hasFinancialKeywords(subject + ' ' + snippet)) {
      return { type: 'NOISE', confidence: 0.9 };
    }
    
    // Check for exception signals (high priority)
    for (const pattern of this.DOCUMENT_PATTERNS.EXCEPTION.subject) {
      if (pattern.test(subject)) {
        return { type: 'EXCEPTION', confidence: 0.85, urgent: true };
      }
    }
    
    // Check document types
    for (const [docType, patterns] of Object.entries(this.DOCUMENT_PATTERNS)) {
      if (docType === 'EXCEPTION') continue;
      
      for (const pattern of patterns.subject) {
        if (pattern.test(subject)) {
          return { 
            type: docType, 
            confidence: senderType ? 0.90 : 0.75,
            senderType 
          };
        }
      }
    }
    
    // Known sender but unclear document type
    if (senderType) {
      return { type: 'INVOICE', confidence: 0.60, senderType, needsReview: true };
    }
    
    return { type: 'NOISE', confidence: 0.7 };
  },
  
  /**
   * Quick check if email is likely a conversation vs actual financial document
   */
  _isLikelyConversation(subject, sender, snippet) {
    let score = 0;
    
    // Reply/forward indicators
    if (/^re:/i.test(subject) || /^fwd?:/i.test(subject)) {
      score += 0.4;
    }
    
    // Personal greetings
    if (/\b(hi|hey|hello|dear)\s+[a-z]+/i.test(snippet)) {
      score += 0.3;
    }
    
    // Question/request patterns
    if (/\b(can you|could you|please|question|issue|problem|help)\b/i.test(snippet)) {
      score += 0.2;
    }
    
    // Personal email domains
    const personalDomains = ['gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com', 'icloud.com'];
    for (const domain of personalDomains) {
      if (sender.includes(domain)) {
        score += 0.3;
        break;
      }
    }
    
    // BUT: If it has strong financial document signals, reduce score
    if (/\b(invoice|receipt|statement|payment confirmation|remittance)\b/i.test(subject)) {
      score -= 0.4;
    }
    
    // If sender is a known financial service, it's probably not a conversation
    const senderType = this._getSenderType(sender);
    if (senderType) {
      score -= 0.5;
    }
    
    return {
      likely: score >= 0.5,
      score: score
    };
  },
  
  /**
   * Check if email can be matched against bank feed
   * @param {Object} extractedData - Extracted financial data
   * @param {Array} bankFeedCache - Cached bank transactions
   * @returns {Object} Match result
   */
  async checkBankFeedMatch(extractedData, bankFeedCache) {
    if (!extractedData?.amount || !bankFeedCache?.length) {
      return { matched: false, reason: 'Insufficient data' };
    }
    
    const potentialMatches = bankFeedCache.filter(txn => {
      // Amount match (within 1% tolerance for FX)
      const amountMatch = Math.abs(txn.amount - extractedData.amount) / extractedData.amount < 0.01;
      
      // Date proximity (within 5 days)
      const txnDate = new Date(txn.date);
      const docDate = new Date(extractedData.date || extractedData.dueDate);
      const daysDiff = Math.abs((txnDate - docDate) / (1000 * 60 * 60 * 24));
      const dateMatch = daysDiff <= 5;
      
      // Vendor/description match
      const vendorMatch = txn.description?.toLowerCase().includes(extractedData.vendor?.toLowerCase()) ||
                          extractedData.vendor?.toLowerCase().includes(txn.counterparty?.toLowerCase());
      
      return amountMatch && (dateMatch || vendorMatch);
    });
    
    if (potentialMatches.length === 1) {
      return { 
        matched: true, 
        confidence: 0.95,
        matchedTransaction: potentialMatches[0],
        matchType: 'EXACT'
      };
    } else if (potentialMatches.length > 1) {
      return {
        matched: true,
        confidence: 0.70,
        matchedTransactions: potentialMatches,
        matchType: 'MULTIPLE',
        needsReview: true
      };
    }
    
    return { matched: false, reason: 'No matching transaction found' };
  },
  
  /**
   * Check if email can be matched against ERP records
   * @param {Object} extractedData - Extracted financial data
   * @param {Object} erpRegistry - ERP data (POs, invoices, vendors)
   * @returns {Object} Match result
   */
  async checkERPMatch(extractedData, erpRegistry) {
    const result = {
      matched: false,
      poMatch: null,
      vendorMatch: null,
      glSuggestion: null
    };
    
    if (!extractedData || !erpRegistry) return result;
    
    // Check for PO match
    if (extractedData.poNumber && erpRegistry.purchaseOrders) {
      const po = erpRegistry.purchaseOrders.find(p => 
        p.number === extractedData.poNumber ||
        p.number === extractedData.invoiceNumber
      );
      if (po) {
        result.poMatch = po;
        result.matched = true;
      }
    }
    
    // Check for vendor match
    if (extractedData.vendor && erpRegistry.vendors) {
      const vendor = erpRegistry.vendors.find(v =>
        v.name.toLowerCase().includes(extractedData.vendor.toLowerCase()) ||
        extractedData.vendor.toLowerCase().includes(v.name.toLowerCase()) ||
        v.taxId === extractedData.taxId
      );
      if (vendor) {
        result.vendorMatch = vendor;
        result.glSuggestion = vendor.defaultGLCode;
      }
    }
    
    // Suggest GL code based on classification
    if (!result.glSuggestion && extractedData.category) {
      result.glSuggestion = this._suggestGLCode(extractedData.category);
    }
    
    return result;
  },
  
  // ==================== PRIVATE METHODS ====================
  
  _getPrimaryMessage(thread) {
    if (!thread.messages?.length) return null;
    // Return the most recent message
    return thread.messages[thread.messages.length - 1];
  },
  
  _extractEmailComponents(message) {
    const headers = message.payload?.headers || [];
    const getHeader = (name) => headers.find(h => h.name.toLowerCase() === name.toLowerCase())?.value || '';
    
    return {
      subject: getHeader('Subject'),
      sender: getHeader('From'),
      date: getHeader('Date'),
      body: this._extractBody(message.payload),
      attachments: this._extractAttachmentNames(message.payload)
    };
  },
  
  _extractBody(payload) {
    if (!payload) return '';
    
    // Check for plain text body
    if (payload.body?.data) {
      return atob(payload.body.data.replace(/-/g, '+').replace(/_/g, '/'));
    }
    
    // Check parts for multipart messages
    if (payload.parts) {
      for (const part of payload.parts) {
        if (part.mimeType === 'text/plain' && part.body?.data) {
          return atob(part.body.data.replace(/-/g, '+').replace(/_/g, '/'));
        }
      }
    }
    
    return '';
  },
  
  _extractAttachmentNames(payload) {
    const attachments = [];
    
    const extractFromParts = (parts) => {
      if (!parts) return;
      for (const part of parts) {
        if (part.filename) {
          attachments.push(part.filename.toLowerCase());
        }
        if (part.parts) {
          extractFromParts(part.parts);
        }
      }
    };
    
    extractFromParts(payload?.parts);
    return attachments;
  },
  
  _analyzeSender(sender) {
    const senderLower = sender.toLowerCase();
    let score = 0;
    let type = null;
    
    for (const [category, domains] of Object.entries(this.FINANCIAL_SENDERS)) {
      for (const domain of domains) {
        if (senderLower.includes(domain)) {
          score = 0.9;
          type = category;
          break;
        }
      }
      if (type) break;
    }
    
    // Check for generic finance-related sender patterns
    if (!type && /billing|invoice|accounts|finance|payment|treasury/i.test(senderLower)) {
      score = 0.6;
      type = 'generic_finance';
    }
    
    return { score, type };
  },
  
  _getSenderType(sender) {
    for (const [category, domains] of Object.entries(this.FINANCIAL_SENDERS)) {
      for (const domain of domains) {
        if (sender.includes(domain)) {
          return category;
        }
      }
    }
    return null;
  },
  
  _hasFinancialKeywords(text) {
    const keywords = /invoice|payment|receipt|statement|remittance|payout|transfer|amount|due|billing/i;
    return keywords.test(text);
  },
  
  _analyzeSubject(subject) {
    const results = {};
    
    for (const [docType, patterns] of Object.entries(this.DOCUMENT_PATTERNS)) {
      let maxScore = 0;
      for (const pattern of patterns.subject) {
        if (pattern.test(subject)) {
          maxScore = Math.max(maxScore, 0.8);
        }
      }
      if (maxScore > 0) {
        results[docType] = maxScore;
      }
    }
    
    return results;
  },
  
  _analyzeBody(body) {
    if (!body) return {};
    
    const results = {};
    
    for (const [docType, patterns] of Object.entries(this.DOCUMENT_PATTERNS)) {
      let matchCount = 0;
      for (const pattern of patterns.body) {
        if (pattern.test(body)) {
          matchCount++;
        }
      }
      if (matchCount > 0) {
        results[docType] = Math.min(0.9, 0.3 + (matchCount * 0.2));
      }
    }
    
    return results;
  },
  
  _analyzeAttachments(attachments) {
    if (!attachments?.length) return {};
    
    const results = {};
    
    for (const [docType, patterns] of Object.entries(this.DOCUMENT_PATTERNS)) {
      for (const attachment of attachments) {
        for (const pattern of patterns.attachments) {
          if (attachment.includes(pattern)) {
            results[docType] = Math.max(results[docType] || 0, 0.85);
          }
        }
      }
    }
    
    // PDF/image attachments boost confidence for invoices
    const hasFinancialAttachment = attachments.some(a => 
      a.endsWith('.pdf') || a.endsWith('.png') || a.endsWith('.jpg')
    );
    if (hasFinancialAttachment && !Object.keys(results).length) {
      results.INVOICE = 0.5; // Weak signal
    }
    
    return results;
  },
  
  _computeClassification(signals) {
    const { senderScore, subjectAnalysis, bodyAnalysis, attachmentAnalysis } = signals;
    
    // Combine scores for each document type
    const typeScores = {};
    
    for (const docType of Object.keys(this.DOCUMENT_PATTERNS)) {
      let score = 0;
      let weights = 0;
      
      // Sender contributes if it's a known financial sender
      if (senderScore.score > 0) {
        score += senderScore.score * 0.3;
        weights += 0.3;
      }
      
      // Subject analysis
      if (subjectAnalysis[docType]) {
        score += subjectAnalysis[docType] * 0.35;
        weights += 0.35;
      }
      
      // Body analysis
      if (bodyAnalysis[docType]) {
        score += bodyAnalysis[docType] * 0.2;
        weights += 0.2;
      }
      
      // Attachment analysis
      if (attachmentAnalysis[docType]) {
        score += attachmentAnalysis[docType] * 0.15;
        weights += 0.15;
      }
      
      if (weights > 0) {
        typeScores[docType] = score / weights;
      }
    }
    
    // Find the highest scoring type
    let bestType = 'NOISE';
    let bestScore = 0;
    
    for (const [type, score] of Object.entries(typeScores)) {
      if (score > bestScore) {
        bestScore = score;
        bestType = type;
      }
    }
    
    // EXCEPTION takes priority if detected
    if (typeScores.EXCEPTION && typeScores.EXCEPTION > 0.6) {
      return { type: 'EXCEPTION', confidence: typeScores.EXCEPTION };
    }
    
    return { type: bestType, confidence: bestScore };
  },
  
  _extractFinancialData(emailData, docType) {
    const data = {
      vendor: null,
      amount: null,
      currency: null,
      date: emailData.date,
      invoiceNumber: null,
      poNumber: null,
      dueDate: null,
      taxId: null,
      category: null,
      isConversation: false
    };
    
    const combined = `${emailData.subject} ${emailData.body}`;
    
    // CRITICAL: Detect if this is a conversation about an invoice vs an actual invoice
    const conversationSignals = this._detectConversation(emailData);
    data.isConversation = conversationSignals.isConversation;
    
    // Extract vendor - use smart extraction that looks at body content, not just sender
    data.vendor = this._extractVendorSmart(emailData, conversationSignals);
    
    // Extract amount
    for (const pattern of this.AMOUNT_PATTERNS) {
      const match = combined.match(pattern);
      if (match) {
        let numStr = match[1];
        if (/^(EUR|USD|GBP)/i.test(numStr)) {
          numStr = match[2];
        }
        numStr = numStr.replace(/,/g, '');
        data.amount = parseFloat(numStr);
        break;
      }
    }
    
    // Extract currency
    if (/€|EUR/i.test(combined)) data.currency = 'EUR';
    else if (/\$|USD/i.test(combined)) data.currency = 'USD';
    else if (/£|GBP/i.test(combined)) data.currency = 'GBP';
    else data.currency = 'USD';
    
    // Extract invoice number
    const invMatch = combined.match(/(?:invoice|inv)[#:\s-]*([A-Z0-9]+-?[A-Z0-9]+-?\d+)/i);
    if (invMatch) data.invoiceNumber = invMatch[1].toUpperCase();
    
    // Extract PO number
    const poMatch = combined.match(/(?:po|purchase\s*order)[#:\s-]*([A-Z0-9-]+)/i);
    if (poMatch) data.poNumber = poMatch[1].toUpperCase();
    
    // Suggest category based on vendor
    data.category = this._suggestCategory(data.vendor);
    
    return data;
  },
  
  /**
   * Detect if an email is a conversation/discussion vs an actual financial document
   */
  _detectConversation(emailData) {
    const subject = (emailData.subject || '').toLowerCase();
    const body = (emailData.body || '').toLowerCase();
    const sender = (emailData.sender || '').toLowerCase();
    
    const result = {
      isConversation: false,
      conversationType: null,
      mentionedVendor: null,
      confidence: 0
    };
    
    // Strong conversation signals
    const conversationPatterns = [
      /^re:/i,                           // Reply
      /^fwd?:/i,                         // Forward
      /hi\s+\w+|hey\s+\w+|hello\s+\w+/i, // Greeting with name
      /thanks|thank you|cheers|regards/i, // Sign-offs
      /can you|could you|please\s+\w+/i,  // Requests
      /question about|issue with|problem with/i, // Issue discussions
      /following up|circling back/i,      // Follow-ups
      /let me know|let us know/i,         // Requests for info
      /attached is|please find attached/i // But could be legit invoice
    ];
    
    // Check for conversation signals in subject
    let conversationScore = 0;
    for (const pattern of conversationPatterns) {
      if (pattern.test(subject)) conversationScore += 0.3;
      if (pattern.test(body)) conversationScore += 0.2;
    }
    
    // Personal email domains are strong conversation signals
    const personalDomains = ['gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com', 'icloud.com', 'me.com'];
    for (const domain of personalDomains) {
      if (sender.includes(domain)) {
        conversationScore += 0.4;
        break;
      }
    }
    
    // Check if sender looks like a person name (not a company)
    const senderNameMatch = sender.match(/^([^<]+)/);
    if (senderNameMatch) {
      const name = senderNameMatch[1].trim();
      // Personal names typically have 2-3 words, no special chars
      if (/^[A-Za-z]+\s+[A-Za-z]+(\s+[A-Za-z]+)?$/.test(name)) {
        // Looks like "John Smith" or "John David Smith"
        conversationScore += 0.3;
      }
    }
    
    // Look for vendor mentions in the body (the ACTUAL vendor being discussed)
    const vendorMentionPatterns = [
      /(?:from|with|to|about|regarding)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)/,
      /([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(?:invoice|bill|payment|statement)/i,
      /invoice\s+(?:from|for)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)/i
    ];
    
    for (const pattern of vendorMentionPatterns) {
      const match = body.match(pattern) || subject.match(pattern);
      if (match && match[1]) {
        // Check if this looks like a company name, not a person
        const potentialVendor = match[1].trim();
        if (this._looksLikeCompany(potentialVendor)) {
          result.mentionedVendor = potentialVendor;
          break;
        }
      }
    }
    
    result.isConversation = conversationScore >= 0.5;
    result.confidence = Math.min(1, conversationScore);
    
    return result;
  },
  
  /**
   * Check if a name looks like a company vs a person
   */
  _looksLikeCompany(name) {
    if (!name) return false;
    
    const companyIndicators = [
      /\b(inc|llc|ltd|corp|co|gmbh|ag|sa|plc|limited|incorporated)\b/i,
      /\b(technologies|solutions|services|systems|software|consulting)\b/i,
      /\b(group|holdings|partners|associates|international)\b/i
    ];
    
    // Known companies (even without suffixes)
    const knownCompanies = [
      'stripe', 'paypal', 'google', 'microsoft', 'amazon', 'aws', 'apple',
      'salesforce', 'oracle', 'sap', 'xero', 'quickbooks', 'freshbooks',
      'slack', 'zoom', 'dropbox', 'github', 'atlassian', 'hubspot',
      'cursor', 'vercel', 'netlify', 'heroku', 'digitalocean', 'cloudflare'
    ];
    
    const nameLower = name.toLowerCase();
    
    // Check known companies
    for (const company of knownCompanies) {
      if (nameLower.includes(company)) return true;
    }
    
    // Check company indicators
    for (const pattern of companyIndicators) {
      if (pattern.test(name)) return true;
    }
    
    // Single word names are more likely companies than people
    if (!/\s/.test(name.trim()) && name.length > 3) {
      return true;
    }
    
    return false;
  },
  
  /**
   * Smart vendor extraction that considers context
   */
  _extractVendorSmart(emailData, conversationSignals) {
    const sender = emailData.sender || '';
    const subject = emailData.subject || '';
    const body = emailData.body || '';
    
    // Known vendor mappings (highest priority)
    const vendorMappings = {
      'stripe': 'Stripe',
      'paypal': 'PayPal',
      'aws': 'Amazon Web Services',
      'amazon': 'Amazon',
      'google': 'Google',
      'microsoft': 'Microsoft',
      'sap': 'SAP',
      'oracle': 'Oracle',
      'salesforce': 'Salesforce',
      'xero': 'Xero',
      'quickbooks': 'QuickBooks',
      'freshbooks': 'FreshBooks',
      'cursor': 'Cursor',
      'github': 'GitHub',
      'slack': 'Slack',
      'zoom': 'Zoom'
    };
    
    const senderLower = sender.toLowerCase();
    const combined = `${subject} ${body}`.toLowerCase();
    
    // 1. Check if sender is a known vendor
    for (const [key, name] of Object.entries(vendorMappings)) {
      if (senderLower.includes(key)) return name;
    }
    
    // 2. If this is a conversation, look for the mentioned vendor instead
    if (conversationSignals.isConversation && conversationSignals.mentionedVendor) {
      // Check if mentioned vendor is in our known list
      const mentionedLower = conversationSignals.mentionedVendor.toLowerCase();
      for (const [key, name] of Object.entries(vendorMappings)) {
        if (mentionedLower.includes(key)) return name;
      }
      // Return the mentioned vendor if it looks like a company
      if (this._looksLikeCompany(conversationSignals.mentionedVendor)) {
        return conversationSignals.mentionedVendor;
      }
    }
    
    // 3. Look for vendor patterns in the email body
    const vendorPatterns = [
      /(?:invoice|bill|payment)\s+(?:from|to)\s+([A-Z][A-Za-z0-9]+(?:\s+[A-Z][A-Za-z0-9]+)?)/i,
      /(?:vendor|supplier|merchant)[\s:]+([A-Z][A-Za-z0-9]+(?:\s+[A-Z][A-Za-z0-9]+)?)/i,
      /(?:billed\s+by|charged\s+by)[\s:]+([A-Z][A-Za-z0-9]+(?:\s+[A-Z][A-Za-z0-9]+)?)/i
    ];
    
    for (const pattern of vendorPatterns) {
      const match = combined.match(pattern);
      if (match && match[1] && this._looksLikeCompany(match[1])) {
        return match[1].trim();
      }
    }
    
    // 4. Check for known vendors mentioned anywhere in the email
    for (const [key, name] of Object.entries(vendorMappings)) {
      if (combined.includes(key)) return name;
    }
    
    // 5. If sender is from a company domain (not personal email), use domain as vendor
    const domainMatch = sender.match(/@([^.>]+)\./);
    if (domainMatch) {
      const domain = domainMatch[1].toLowerCase();
      
      // Skip personal email domains
      const personalDomains = ['gmail', 'yahoo', 'hotmail', 'outlook', 'icloud', 'me', 'aol', 'proton', 'protonmail'];
      if (!personalDomains.includes(domain)) {
        // Capitalize domain name
        return domain.charAt(0).toUpperCase() + domain.slice(1);
      }
    }
    
    // 6. If this is clearly a conversation from a person, return null (no vendor)
    if (conversationSignals.isConversation && conversationSignals.confidence > 0.7) {
      return null; // Don't extract person names as vendors
    }
    
    // 7. Last resort: try to extract company name from sender
    const nameMatch = sender.match(/^([^<]+)/);
    if (nameMatch) {
      const name = nameMatch[1].trim();
      // Only use if it looks like a company
      if (this._looksLikeCompany(name)) {
        return name;
      }
    }
    
    return null; // Return null instead of "Unknown" - better to have no vendor than wrong vendor
  },
  
  _extractVendor(sender) {
    // Legacy method - kept for compatibility but prefer _extractVendorSmart
    const vendorMappings = {
      'stripe': 'Stripe',
      'paypal': 'PayPal',
      'aws': 'Amazon Web Services',
      'google': 'Google',
      'microsoft': 'Microsoft',
      'sap': 'SAP',
      'oracle': 'Oracle',
      'salesforce': 'Salesforce'
    };
    
    const senderLower = sender.toLowerCase();
    for (const [key, name] of Object.entries(vendorMappings)) {
      if (senderLower.includes(key)) return name;
    }
    
    // Extract from domain (skip personal emails)
    const domainMatch = sender.match(/@([^.>]+)\./);
    if (domainMatch) {
      const domain = domainMatch[1].toLowerCase();
      const personalDomains = ['gmail', 'yahoo', 'hotmail', 'outlook', 'icloud', 'me'];
      if (!personalDomains.includes(domain)) {
        return domain.charAt(0).toUpperCase() + domain.slice(1);
      }
    }
    
    return null;
  },
  
  _suggestCategory(vendor) {
    const categoryMap = {
      'stripe': 'Payment Processing',
      'paypal': 'Payment Processing',
      'aws': 'Cloud Infrastructure',
      'google': 'Technology Services',
      'microsoft': 'Software Licenses',
      'sap': 'Enterprise Software',
      'default': 'General Expense'
    };
    
    const vendorLower = (vendor || '').toLowerCase();
    for (const [key, category] of Object.entries(categoryMap)) {
      if (vendorLower.includes(key)) return category;
    }
    return categoryMap.default;
  },
  
  _suggestGLCode(category) {
    const glMap = {
      'Payment Processing': '6150',
      'Cloud Infrastructure': '6200',
      'Technology Services': '6200',
      'Software Licenses': '6210',
      'Enterprise Software': '6210',
      'Professional Services': '6300',
      'General Expense': '6000'
    };
    return glMap[category] || '6000';
  },
  
  _determineSuggestedActions(docType, extractedData) {
    const actions = [];
    
    switch (docType) {
      case 'INVOICE':
        actions.push({
          id: 'match_po',
          label: 'Match to PO',
          description: 'Find matching Purchase Order in ERP',
          priority: 1
        });
        actions.push({
          id: 'post_ledger',
          label: 'Post to Ledger',
          description: 'Create AP entry in ERP',
          priority: 2,
          requiresApproval: extractedData?.amount > 10000
        });
        break;
        
      case 'REMITTANCE':
        actions.push({
          id: 'match_bank',
          label: 'Match to Bank Feed',
          description: 'Find corresponding bank transaction',
          priority: 1
        });
        actions.push({
          id: 'clear_invoice',
          label: 'Clear Invoice',
          description: 'Mark related invoice as paid',
          priority: 2
        });
        break;
        
      case 'STATEMENT':
        actions.push({
          id: 'reconcile',
          label: 'Start Reconciliation',
          description: 'Begin bank reconciliation workflow',
          priority: 1
        });
        break;
        
      case 'EXCEPTION':
        actions.push({
          id: 'escalate',
          label: 'Escalate',
          description: 'Route to finance manager for review',
          priority: 1,
          urgent: true
        });
        actions.push({
          id: 'respond',
          label: 'Draft Response',
          description: 'Generate response to dispute/inquiry',
          priority: 2
        });
        break;
    }
    
    return actions;
  }
};

// Export for use in background.js and content scripts
if (typeof module !== 'undefined' && module.exports) {
  module.exports = DiscoveryEngine;
}
