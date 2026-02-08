/**
 * Clearledgr Transaction Categorization Engine
 * 
 * Local-first processing in Google Sheets.
 * Auto-classifies transactions to GL accounts based on:
 * - Vendor/payee name patterns
 * - Amount ranges
 * - Transaction descriptions
 * - Historical patterns (learns from user corrections)
 */

/**
 * Main categorization function - runs entirely client-side.
 * 
 * @param {Array} transactions - Transactions to categorize [{description, amount, vendor, date}, ...]
 * @param {Array} glAccounts - GL account chart [{code, name, category, keywords}, ...]
 * @param {Object} config - {confidenceThreshold: 0.7, useHistoricalPatterns: true}
 * @returns {Object} {categorized, suggestions, stats}
 */
function categorizeTransactionsLocal(transactions, glAccounts, config) {
  const confidenceThreshold = config.confidenceThreshold || 0.7;
  const useHistoricalPatterns = config.useHistoricalPatterns !== false;
  
  // Load historical patterns if enabled
  const historicalPatterns = useHistoricalPatterns ? loadHistoricalPatterns() : {};
  
  // Build lookup structures
  const accountsByKeyword = buildKeywordIndex(glAccounts);
  const accountsByCategory = buildCategoryIndex(glAccounts);
  
  const categorized = [];
  const suggestions = [];
  let autoMatched = 0;
  let needsReview = 0;
  
  transactions.forEach((tx, idx) => {
    const result = categorizeSingleTransaction(
      tx,
      glAccounts,
      accountsByKeyword,
      accountsByCategory,
      historicalPatterns,
      confidenceThreshold
    );
    
    if (result.confidence >= confidenceThreshold) {
      categorized.push({
        ...tx,
        gl_account_code: result.account.code,
        gl_account_name: result.account.name,
        category: result.account.category,
        confidence: result.confidence,
        confidence_pct: Math.round(result.confidence * 100),
        match_reason: result.reason,
        status: 'auto_categorized',
        reasoning: result.reasoning,
        reasoning_steps: result.reasoning_steps
      });
      autoMatched++;
    } else {
      suggestions.push({
        ...tx,
        suggested_accounts: result.topSuggestions,
        confidence: result.confidence,
        confidence_pct: Math.round(result.confidence * 100),
        status: 'needs_review',
        reasoning: result.reasoning,
        reasoning_steps: result.reasoning_steps
      });
      needsReview++;
    }
  });
  
  const stats = {
    total_transactions: transactions.length,
    auto_categorized: autoMatched,
    needs_review: needsReview,
    auto_rate: transactions.length > 0 ? (autoMatched / transactions.length) * 100 : 0,
    confidence_threshold: confidenceThreshold
  };
  
  return { categorized, suggestions, stats };
}


/**
 * Categorize a single transaction.
 */
function categorizeSingleTransaction(tx, glAccounts, keywordIndex, categoryIndex, historicalPatterns, threshold) {
  const scores = [];
  
  // Normalize transaction data
  const description = (tx.description || tx.memo || '').toLowerCase().trim();
  const vendor = (tx.vendor || tx.payee || tx.name || '').toLowerCase().trim();
  const vendorNormalized = normalizeVendorName(vendor);
  const amount = Math.abs(parseFloat(tx.amount) || 0);
  const isDebit = parseFloat(tx.amount) < 0;
  
  // Score each GL account
  glAccounts.forEach(account => {
    let score = 0;
    let reasons = [];
    
    // 1. Historical pattern match with FUZZY matching (highest weight)
    const vendorKey = vendorNormalized.replace(/[^a-z0-9]/g, '');
    
    // Exact historical match
    if (historicalPatterns[vendorKey] && historicalPatterns[vendorKey].account === account.code) {
      score += 0.5;
      reasons.push('historical_match');
    } else {
      // Fuzzy historical match - find similar vendors
      const fuzzyMatch = findFuzzyHistoricalMatch(vendorNormalized, historicalPatterns, account.code);
      if (fuzzyMatch) {
        score += 0.4;
        reasons.push(`fuzzy_historical:${fuzzyMatch.similarity}%`);
      }
    }
    
    // 2. Keyword match with fuzzy matching
    const keywords = (account.keywords || '').toLowerCase().split(',').map(k => k.trim()).filter(k => k);
    keywords.forEach(keyword => {
      // Exact match
      if (description.includes(keyword) || vendor.includes(keyword)) {
        score += 0.3;
        reasons.push(`keyword:${keyword}`);
      } 
      // Fuzzy keyword match (for typos/variations)
      else if (fuzzyContains(vendor, keyword, 0.8) || fuzzyContains(description, keyword, 0.8)) {
        score += 0.2;
        reasons.push(`fuzzy_keyword:${keyword}`);
      }
    });
    
    // 3. Account name similarity (with fuzzy)
    const accountNameLower = (account.name || '').toLowerCase();
    if (description.includes(accountNameLower) || vendor.includes(accountNameLower)) {
      score += 0.2;
      reasons.push('name_match');
    } else if (stringSimilarity(vendor, accountNameLower) > 0.6) {
      score += 0.15;
      reasons.push('fuzzy_name_match');
    }
    
    // 4. Category-based rules (expanded)
    const category = (account.category || '').toLowerCase();
    
    // Common vendor patterns - EXPANDED for EU/Africa markets
    if (category === 'travel' && matchesPattern(vendor, ['airline', 'hotel', 'uber', 'lyft', 'airbnb', 'expedia', 'booking.com', 'ryanair', 'easyjet', 'emirates', 'kenya airways', 'ethiopian', 'safarilink'])) {
      score += 0.25;
      reasons.push('category_pattern:travel');
    }
    if (category === 'software' && matchesPattern(vendor, ['aws', 'google', 'microsoft', 'adobe', 'slack', 'zoom', 'saas', 'atlassian', 'github', 'notion', 'figma', 'canva', 'hubspot'])) {
      score += 0.25;
      reasons.push('category_pattern:software');
    }
    if (category === 'office' && matchesPattern(vendor, ['staples', 'office depot', 'amazon', 'jumia', 'takealot', 'alibaba'])) {
      score += 0.2;
      reasons.push('category_pattern:office');
    }
    if (category === 'utilities' && matchesPattern(vendor, ['electric', 'water', 'gas', 'internet', 'phone', 'vodafone', 'mtn', 'safaricom', 'airtel', 'orange', 'edf', 'engie'])) {
      score += 0.25;
      reasons.push('category_pattern:utilities');
    }
    if (category === 'payroll' && matchesPattern(description, ['salary', 'payroll', 'wages', 'bonus', 'paye', 'pension', 'staff cost'])) {
      score += 0.3;
      reasons.push('category_pattern:payroll');
    }
    if (category === 'revenue' && matchesPattern(description, ['payment received', 'invoice paid', 'customer payment', 'sales', 'revenue', 'income'])) {
      score += 0.3;
      reasons.push('category_pattern:revenue');
    }
    if (category === 'banking' && matchesPattern(vendor, ['bank', 'barclays', 'hsbc', 'standard chartered', 'equity bank', 'stanbic', 'kcb', 'absa', 'fnb', 'nedbank'])) {
      score += 0.25;
      reasons.push('category_pattern:banking');
    }
    if (category === 'payment_processing' && matchesPattern(vendor, ['stripe', 'paypal', 'flutterwave', 'paystack', 'mpesa', 'chipper', 'wise', 'world remit'])) {
      score += 0.3;
      reasons.push('category_pattern:payment_processing');
    }
    
    // 5. Amount range hints
    if (account.min_amount && account.max_amount) {
      if (amount >= account.min_amount && amount <= account.max_amount) {
        score += 0.1;
        reasons.push('amount_range');
      }
    }
    
    // 6. Debit/Credit alignment
    if (account.typical_sign) {
      const expectDebit = account.typical_sign === 'debit';
      if (expectDebit === isDebit) {
        score += 0.1;
        reasons.push('sign_match');
      }
    }
    
    // 7. NEW: Recurring transaction pattern detection
    const recurringMatch = checkRecurringPattern(tx, account.code, amount);
    if (recurringMatch) {
      score += 0.2;
      reasons.push(`recurring:${recurringMatch.frequency}`);
    }
    
    // Cap score at 1.0
    score = Math.min(score, 1.0);
    
    scores.push({
      account: account,
      score: score,
      reasons: reasons
    });
  });
  
  // Sort by score
  scores.sort((a, b) => b.score - a.score);
  
  const topMatch = scores[0];
  const topSuggestions = scores.slice(0, 3).map(s => ({
    code: s.account.code,
    name: s.account.name,
    confidence: s.score,
    reasons: s.reasons
  }));
  
  // Build human-readable reasoning tree
  const reasoning = buildReasoningTree(tx, topMatch, topSuggestions);
  
  return {
    account: topMatch ? topMatch.account : null,
    confidence: topMatch ? topMatch.score : 0,
    reason: topMatch ? topMatch.reasons.join(', ') : 'no_match',
    topSuggestions: topSuggestions,
    reasoning: reasoning.display,
    reasoning_steps: reasoning.steps
  };
}


/**
 * Build human-readable reasoning tree for categorization decision.
 */
function buildReasoningTree(tx, topMatch, alternatives) {
  const description = tx.description || tx.vendor || 'Unknown';
  const amount = Math.abs(parseFloat(tx.amount) || 0);
  
  if (!topMatch || !topMatch.account) {
    return {
      display: 'No match found\n└── Unable to determine category',
      steps: [{ factor: 'Match', observation: 'No matching category found', impact: 'negative' }]
    };
  }
  
  const lines = [`Categorized as: ${topMatch.account.name} (${topMatch.account.code})`];
  const steps = [];
  
  // Parse reasons and create tree
  topMatch.reasons.forEach((reason, idx) => {
    const isLast = idx === topMatch.reasons.length - 1;
    const prefix = isLast ? '└──' : '├──';
    let observation = '';
    let impact = 'positive';
    
    if (reason === 'historical_match') {
      observation = `Historical: Previously categorized similar transactions here`;
      steps.push({ factor: 'Historical Pattern', observation: 'Matches learned pattern', impact: 'positive' });
    } else if (reason.startsWith('keyword:')) {
      const keyword = reason.replace('keyword:', '');
      observation = `Keyword: Found "${keyword}" in description`;
      steps.push({ factor: 'Keyword Match', observation: `Found "${keyword}"`, impact: 'positive' });
    } else if (reason === 'name_match') {
      observation = `Name: Description matches account name`;
      steps.push({ factor: 'Account Name', observation: 'Description matches account', impact: 'positive' });
    } else if (reason.startsWith('category_pattern:')) {
      const pattern = reason.replace('category_pattern:', '');
      observation = `Pattern: Matches ${pattern} vendor pattern`;
      steps.push({ factor: 'Category Pattern', observation: `Matches ${pattern} pattern`, impact: 'positive' });
    } else if (reason === 'amount_range') {
      observation = `Amount: €${amount.toLocaleString('de-DE')} within expected range`;
      steps.push({ factor: 'Amount Range', observation: 'Within expected range', impact: 'positive' });
    } else if (reason === 'sign_match') {
      observation = `Sign: Transaction type matches account expectation`;
      steps.push({ factor: 'Debit/Credit', observation: 'Sign matches account type', impact: 'positive' });
    } else {
      observation = reason;
      steps.push({ factor: 'Other', observation: reason, impact: 'neutral' });
    }
    
    lines.push(`${prefix} ${observation}`);
  });
  
  // Add confidence
  const confidencePct = Math.round(topMatch.score * 100);
  const confidenceLevel = confidencePct >= 90 ? 'high' : confidencePct >= 70 ? 'medium' : 'low';
  lines.push(`└── Confidence: ${confidencePct}% (${confidenceLevel})`);
  steps.push({ factor: 'Confidence', observation: `${confidencePct}% (${confidenceLevel})`, impact: 'neutral' });
  
  // Add alternatives if close
  if (alternatives.length > 1 && alternatives[1].confidence > 0.5) {
    lines.push('');
    lines.push('Also considered:');
    alternatives.slice(1, 3).forEach(alt => {
      lines.push(`  • ${alt.name} (${Math.round(alt.confidence * 100)}%)`);
    });
  }
  
  return {
    display: lines.join('\n'),
    steps: steps
  };
}


/**
 * Check if text matches any pattern.
 */
function matchesPattern(text, patterns) {
  return patterns.some(p => text.includes(p));
}


/**
 * Normalize vendor name for better matching.
 * Handles variations like "UBER EATS", "Uber Eats Inc.", "uber eats llc"
 */
function normalizeVendorName(vendor) {
  if (!vendor) return '';
  
  return vendor
    .toLowerCase()
    .replace(/\b(inc|llc|ltd|limited|corp|corporation|plc|gmbh|sa|sas|bv|ag)\b\.?/gi, '')
    .replace(/[^\w\s]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}


/**
 * Calculate string similarity using Levenshtein distance.
 * Returns value between 0 (no match) and 1 (exact match).
 */
function stringSimilarity(str1, str2) {
  if (!str1 || !str2) return 0;
  if (str1 === str2) return 1;
  
  const s1 = str1.toLowerCase();
  const s2 = str2.toLowerCase();
  
  const longer = s1.length > s2.length ? s1 : s2;
  const shorter = s1.length > s2.length ? s2 : s1;
  
  if (longer.length === 0) return 1;
  
  const distance = levenshteinDistance(longer, shorter);
  return (longer.length - distance) / longer.length;
}


/**
 * Levenshtein distance calculation.
 */
function levenshteinDistance(str1, str2) {
  const matrix = [];
  
  for (let i = 0; i <= str2.length; i++) {
    matrix[i] = [i];
  }
  for (let j = 0; j <= str1.length; j++) {
    matrix[0][j] = j;
  }
  
  for (let i = 1; i <= str2.length; i++) {
    for (let j = 1; j <= str1.length; j++) {
      if (str2.charAt(i - 1) === str1.charAt(j - 1)) {
        matrix[i][j] = matrix[i - 1][j - 1];
      } else {
        matrix[i][j] = Math.min(
          matrix[i - 1][j - 1] + 1,
          matrix[i][j - 1] + 1,
          matrix[i - 1][j] + 1
        );
      }
    }
  }
  
  return matrix[str2.length][str1.length];
}


/**
 * Check if text fuzzy-contains a keyword.
 */
function fuzzyContains(text, keyword, threshold) {
  if (!text || !keyword) return false;
  
  const textLower = text.toLowerCase();
  const keywordLower = keyword.toLowerCase();
  
  // Exact contain check first
  if (textLower.includes(keywordLower)) return true;
  
  // Split text into words and check each
  const words = textLower.split(/\s+/);
  return words.some(word => stringSimilarity(word, keywordLower) >= threshold);
}


/**
 * Find fuzzy match in historical patterns.
 */
function findFuzzyHistoricalMatch(vendorNormalized, historicalPatterns, accountCode) {
  if (!historicalPatterns || Object.keys(historicalPatterns).length === 0) return null;
  
  let bestMatch = null;
  let bestSimilarity = 0;
  const threshold = 0.75;
  
  for (const [patternVendor, patternData] of Object.entries(historicalPatterns)) {
    if (patternData.account !== accountCode) continue;
    
    const similarity = stringSimilarity(vendorNormalized, patternVendor);
    if (similarity >= threshold && similarity > bestSimilarity) {
      bestSimilarity = similarity;
      bestMatch = {
        vendor: patternVendor,
        account: patternData.account,
        similarity: Math.round(similarity * 100)
      };
    }
  }
  
  return bestMatch;
}


/**
 * Check for recurring transaction patterns.
 * Detects monthly, weekly, or bi-weekly transactions.
 */
function checkRecurringPattern(tx, accountCode, amount) {
  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const categorizedSheet = ss.getSheetByName('CL_CATEGORIZED');
    if (!categorizedSheet) return null;
    
    const data = categorizedSheet.getDataRange().getValues();
    if (data.length < 3) return null; // Need history
    
    const vendor = normalizeVendorName(tx.vendor || tx.payee || '');
    if (!vendor) return null;
    
    // Find past transactions with similar vendor and account
    const matches = [];
    for (let i = 1; i < data.length; i++) {
      const rowVendor = normalizeVendorName(String(data[i][1] || ''));
      const rowAccount = data[i][4]; // GL Code column
      const rowAmount = Math.abs(parseFloat(data[i][2]) || 0);
      const rowDate = data[i][3];
      
      if (rowAccount === accountCode && stringSimilarity(vendor, rowVendor) > 0.8) {
        // Similar amount (within 5%)
        if (Math.abs(rowAmount - amount) / Math.max(rowAmount, amount) < 0.05) {
          matches.push({
            date: new Date(rowDate),
            amount: rowAmount
          });
        }
      }
    }
    
    if (matches.length < 2) return null;
    
    // Sort by date
    matches.sort((a, b) => a.date - b.date);
    
    // Calculate average gap between transactions
    let totalGap = 0;
    for (let i = 1; i < matches.length; i++) {
      totalGap += (matches[i].date - matches[i-1].date) / (1000 * 60 * 60 * 24);
    }
    const avgGap = totalGap / (matches.length - 1);
    
    // Determine frequency
    if (avgGap >= 25 && avgGap <= 35) {
      return { frequency: 'monthly', count: matches.length };
    } else if (avgGap >= 12 && avgGap <= 16) {
      return { frequency: 'bi-weekly', count: matches.length };
    } else if (avgGap >= 5 && avgGap <= 9) {
      return { frequency: 'weekly', count: matches.length };
    } else if (avgGap >= 85 && avgGap <= 95) {
      return { frequency: 'quarterly', count: matches.length };
    }
    
    return null;
  } catch (e) {
    return null;
  }
}


/**
 * Build keyword to account index.
 */
function buildKeywordIndex(glAccounts) {
  const index = {};
  glAccounts.forEach(account => {
    const keywords = (account.keywords || '').split(',').map(k => k.trim().toLowerCase()).filter(k => k);
    keywords.forEach(keyword => {
      if (!index[keyword]) index[keyword] = [];
      index[keyword].push(account);
    });
  });
  return index;
}


/**
 * Build category to accounts index.
 */
function buildCategoryIndex(glAccounts) {
  const index = {};
  glAccounts.forEach(account => {
    const category = (account.category || 'uncategorized').toLowerCase();
    if (!index[category]) index[category] = [];
    index[category].push(account);
  });
  return index;
}


/**
 * Load historical patterns from CL_PATTERNS sheet.
 */
function loadHistoricalPatterns() {
  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const sheet = ss.getSheetByName('CL_PATTERNS');
    
    if (!sheet) return {};
    
    const data = sheet.getDataRange().getValues();
    const patterns = {};
    
    // Skip header row
    for (let i = 1; i < data.length; i++) {
      const vendor = String(data[i][0] || '').toLowerCase().replace(/[^a-z0-9]/g, '');
      const account = data[i][1];
      const count = parseInt(data[i][2]) || 1;
      
      if (vendor && account) {
        patterns[vendor] = { account: account, count: count };
      }
    }
    
    return patterns;
  } catch (e) {
    return {};
  }
}


/**
 * Save a user correction to learn from.
 */
function saveCategorizationCorrection(vendor, correctAccountCode) {
  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    let sheet = ss.getSheetByName('CL_PATTERNS');
    
    if (!sheet) {
      sheet = ss.insertSheet('CL_PATTERNS');
      sheet.getRange(1, 1, 1, 3).setValues([['Vendor', 'GL Account', 'Count']]);
    }
    
    const vendorKey = String(vendor).toLowerCase().replace(/[^a-z0-9]/g, '');
    const data = sheet.getDataRange().getValues();
    
    // Find existing pattern
    let found = false;
    for (let i = 1; i < data.length; i++) {
      if (String(data[i][0]).toLowerCase().replace(/[^a-z0-9]/g, '') === vendorKey) {
        // Update existing
        sheet.getRange(i + 1, 2).setValue(correctAccountCode);
        sheet.getRange(i + 1, 3).setValue((parseInt(data[i][2]) || 0) + 1);
        found = true;
        break;
      }
    }
    
    if (!found) {
      // Add new pattern
      sheet.appendRow([vendor, correctAccountCode, 1]);
    }
    
    return { success: true, message: 'Pattern saved for future categorization' };
  } catch (e) {
    return { success: false, message: e.message };
  }
}


/**
 * Read GL chart of accounts from sheet.
 */
function readChartOfAccounts(sheetName) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheetByName(sheetName || 'Chart of Accounts');
  
  if (!sheet) {
    throw new Error('Chart of Accounts sheet not found');
  }
  
  const data = sheet.getDataRange().getValues();
  const headers = data[0].map(h => String(h).toLowerCase().trim());
  
  const accounts = [];
  for (let i = 1; i < data.length; i++) {
    const row = data[i];
    const account = {};
    
    headers.forEach((header, idx) => {
      if (header === 'code' || header === 'account code' || header === 'gl code') {
        account.code = row[idx];
      } else if (header === 'name' || header === 'account name' || header === 'description') {
        account.name = row[idx];
      } else if (header === 'category' || header === 'type') {
        account.category = row[idx];
      } else if (header === 'keywords' || header === 'tags') {
        account.keywords = row[idx];
      }
    });
    
    if (account.code) {
      accounts.push(account);
    }
  }
  
  return accounts;
}


/**
 * Write categorization results to sheet.
 */
function writeCategorizationResults(results) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  
  // Write categorized transactions
  let catSheet = ss.getSheetByName('CL_CATEGORIZED');
  if (!catSheet) {
    catSheet = ss.insertSheet('CL_CATEGORIZED');
  }
  catSheet.clear();
  
  const catHeaders = ['Description', 'Vendor', 'Amount', 'Date', 'GL Code', 'GL Account', 'Category', 'Confidence', 'Match Reason'];
  const catData = [catHeaders];
  
  results.categorized.forEach(tx => {
    catData.push([
      tx.description || '',
      tx.vendor || '',
      tx.amount,
      tx.date || '',
      tx.gl_account_code,
      tx.gl_account_name,
      tx.category || '',
      (tx.confidence * 100).toFixed(0) + '%',
      tx.match_reason
    ]);
  });
  
  if (catData.length > 1) {
    catSheet.getRange(1, 1, catData.length, catHeaders.length).setValues(catData);
  }
  
  // Write suggestions (needs review)
  let sugSheet = ss.getSheetByName('CL_NEEDS_REVIEW');
  if (!sugSheet) {
    sugSheet = ss.insertSheet('CL_NEEDS_REVIEW');
  }
  sugSheet.clear();
  
  const sugHeaders = ['Description', 'Vendor', 'Amount', 'Date', 'Suggestion 1', 'Conf 1', 'Suggestion 2', 'Conf 2', 'Suggestion 3', 'Conf 3'];
  const sugData = [sugHeaders];
  
  results.suggestions.forEach(tx => {
    const sug = tx.suggested_accounts || [];
    sugData.push([
      tx.description || '',
      tx.vendor || '',
      tx.amount,
      tx.date || '',
      sug[0] ? `${sug[0].code} - ${sug[0].name}` : '',
      sug[0] ? (sug[0].confidence * 100).toFixed(0) + '%' : '',
      sug[1] ? `${sug[1].code} - ${sug[1].name}` : '',
      sug[1] ? (sug[1].confidence * 100).toFixed(0) + '%' : '',
      sug[2] ? `${sug[2].code} - ${sug[2].name}` : '',
      sug[2] ? (sug[2].confidence * 100).toFixed(0) + '%' : ''
    ]);
  });
  
  if (sugData.length > 1) {
    sugSheet.getRange(1, 1, sugData.length, sugHeaders.length).setValues(sugData);
  }
  
  // Write summary
  let summarySheet = ss.getSheetByName('CL_CAT_SUMMARY');
  if (!summarySheet) {
    summarySheet = ss.insertSheet('CL_CAT_SUMMARY');
  }
  summarySheet.clear();
  
  const summaryData = [
    ['Categorization Summary', ''],
    ['Total Transactions', results.stats.total_transactions],
    ['Auto-Categorized', results.stats.auto_categorized],
    ['Needs Review', results.stats.needs_review],
    ['Auto-Categorization Rate', results.stats.auto_rate.toFixed(1) + '%'],
    ['Confidence Threshold', (results.stats.confidence_threshold * 100).toFixed(0) + '%'],
    ['Run Timestamp', new Date().toISOString()]
  ];
  
  summarySheet.getRange(1, 1, summaryData.length, 2).setValues(summaryData);
  
  return { success: true, stats: results.stats };
}


/**
 * AUTONOMOUS CATEGORIZATION
 * 
 * Runs automatically when the spreadsheet opens.
 * Detects uncategorized transactions and processes them.
 * Only surfaces exceptions that need human review.
 */
function runAutonomousCategorization() {
  var result = { categorized: 0, needsReview: [], processed: 0 };
  
  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const config = getClearledgrConfig();
    
    // Check if categorization is enabled
    if (!config.autoCategorization) {
      return result;
    }
    
    // Find transactions sheet
    const txSheetName = config.transactionsSheet || 'Transactions';
    const txSheet = ss.getSheetByName(txSheetName);
    if (!txSheet) {
      return result; // No transactions sheet, skip
    }
    
    // Find chart of accounts
    const glSheetName = config.chartOfAccountsSheet || 'Chart of Accounts';
    const glSheet = ss.getSheetByName(glSheetName);
    if (!glSheet) {
      return result; // No GL sheet, skip
    }
    
    // Read data
    const transactions = readSheetData(txSheetName);
    const glAccounts = readChartOfAccounts(glSheetName);
    
    if (transactions.length === 0 || glAccounts.length === 0) {
      return result;
    }
    
    // Check for uncategorized transactions
    const uncategorized = findUncategorizedTransactions(transactions);
    if (uncategorized.length === 0) {
      return result; // Nothing new to categorize
    }
    
    result.processed = uncategorized.length;
    
    // Run categorization on uncategorized items only
    const categorizationConfig = { 
      confidenceThreshold: config.confidenceThreshold || 0.7, 
      useHistoricalPatterns: true 
    };
    const results = categorizeTransactionsLocal(uncategorized, glAccounts, categorizationConfig);
    
    // Write results
    writeCategorizationResults(results);
    
    // Update result
    result.categorized = results.stats.auto_categorized || 0;
    result.needsReview = results.needs_review || [];
    
    // If there are exceptions, notify
    if (results.stats.needs_review > 0) {
      notifyCategorizationExceptions(results.stats);
    }
    
    // Log autonomous action
    logAutonomousAction('categorization', {
      processed: uncategorized.length,
      auto_categorized: results.stats.auto_categorized,
      needs_review: results.stats.needs_review
    });
    
    return result;
    
  } catch (e) {
    // Fail silently - don't interrupt user
    console.log('Autonomous categorization error: ' + e.message);
    return result;
  }
}


/**
 * Find transactions that haven't been categorized yet.
 */
function findUncategorizedTransactions(transactions) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const categorizedSheet = ss.getSheetByName('CL_CATEGORIZED');
  
  // Get already categorized transaction IDs
  const categorizedIds = new Set();
  if (categorizedSheet) {
    const data = categorizedSheet.getDataRange().getValues();
    for (let i = 1; i < data.length; i++) {
      // Use description + amount as unique key
      const key = String(data[i][0]) + '|' + String(data[i][2]);
      categorizedIds.add(key);
    }
  }
  
  // Filter to uncategorized only
  return transactions.filter(tx => {
    const key = String(tx.description || tx.memo || '') + '|' + String(tx.amount);
    return !categorizedIds.has(key);
  });
}


/**
 * Notify about categorization exceptions via Slack/Teams.
 */
function notifyCategorizationExceptions(stats) {
  const config = getClearledgrConfig();
  
  // Only notify if there are meaningful exceptions
  if (stats.needs_review < 1) return;
  
  const message = {
    title: 'Clearledgr: Transactions Need Review',
    text: `Auto-categorized ${stats.auto_categorized} transactions.\n` +
          `${stats.needs_review} items need your review (below ${(stats.confidence_threshold * 100).toFixed(0)}% confidence).\n` +
          `Auto-rate: ${stats.auto_rate.toFixed(1)}%`,
    spreadsheet_url: SpreadsheetApp.getActiveSpreadsheet().getUrl()
  };
  
  // Send to Slack/Teams if configured
  sendNotification(message);
}


/**
 * Log autonomous actions for audit trail.
 */
function logAutonomousAction(action, details) {
  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    let logSheet = ss.getSheetByName('CL_AUDIT_LOG');
    
    if (!logSheet) {
      logSheet = ss.insertSheet('CL_AUDIT_LOG');
      logSheet.getRange(1, 1, 1, 5).setValues([['Timestamp', 'Action', 'Details', 'User', 'Spreadsheet']]);
    }
    
    logSheet.appendRow([
      new Date().toISOString(),
      action,
      JSON.stringify(details),
      Session.getActiveUser().getEmail() || 'autonomous',
      ss.getName()
    ]);
  } catch (e) {
    // Fail silently
  }
}


/**
 * Get Clearledgr configuration from document properties.
 */
function getClearledgrConfig() {
  try {
    const props = PropertiesService.getDocumentProperties();
    const configStr = props.getProperty('clearledgr_config');
    if (configStr) {
      return JSON.parse(configStr);
    }
  } catch (e) {}
  
  // Default configuration
  return {
    autoCategorization: true,
    autoReconciliation: true,
    transactionsSheet: 'Transactions',
    chartOfAccountsSheet: 'Chart of Accounts',
    confidenceThreshold: 0.7
  };
}


/**
 * Save Clearledgr configuration.
 */
function saveClearledgrConfig(config) {
  const props = PropertiesService.getDocumentProperties();
  props.setProperty('clearledgr_config', JSON.stringify(config));
}


/**
 * Activate patterns sheet.
 */
function activatePatternsSheet() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheetByName('CL_PATTERNS');
  if (sheet) {
    ss.setActiveSheet(sheet);
  } else {
    SpreadsheetApp.getUi().alert('No patterns learned yet. Patterns are saved when you review and correct categorizations.');
  }
}
