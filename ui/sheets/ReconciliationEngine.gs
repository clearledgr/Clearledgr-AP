/**
 * Clearledgr Reconciliation Engine
 * 
 * Local-first processing in Google Sheets.
 * 
 * INTELLIGENT DATA DETECTION: Automatically detects columns by analyzing
 * data patterns - no manual configuration required.
 */

// =============================================================================
// INTELLIGENT COLUMN DETECTION
// =============================================================================

/**
 * Intelligently read a sheet and auto-detect columns.
 * Works with ANY sheet structure - no manual mapping required.
 */
function readSheetIntelligently(sheetName) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  
  // Find sheet by name (case-insensitive)
  let sheet = null;
  const allSheets = ss.getSheets();
  for (let i = 0; i < allSheets.length; i++) {
    if (allSheets[i].getName().toLowerCase() === sheetName.toLowerCase()) {
      sheet = allSheets[i];
      break;
    }
  }
  
  if (!sheet) {
    Logger.log('Sheet not found: ' + sheetName);
    return [];
  }
  
  const data = sheet.getDataRange().getValues();
  if (data.length < 2) return [];
  
  // Find the header row (first row with multiple non-empty cells)
  let headerRowIndex = 0;
  let dataStartIndex = 1;
  
  for (let i = 0; i < Math.min(10, data.length); i++) {
    const nonEmptyCells = data[i].filter(cell => cell !== '' && cell !== null).length;
    if (nonEmptyCells >= 3) {
      // Check if this looks like a header (mostly text) or data (has numbers/dates)
      const hasNumber = data[i].some(cell => typeof cell === 'number' && Math.abs(cell) > 100);
      const hasDate = data[i].some(cell => cell instanceof Date || isDateString(cell));
      
      if (!hasNumber && !hasDate) {
        headerRowIndex = i;
        dataStartIndex = i + 1;
        break;
      } else if (i === 0) {
        // First row is data, no header - use column indices
        headerRowIndex = -1;
        dataStartIndex = 0;
        break;
      }
    }
  }
  
  // Analyze columns to detect types
  const columnAnalysis = analyzeColumns(data, dataStartIndex);
  
  // Build normalized transactions
  const transactions = [];
  for (let i = dataStartIndex; i < data.length; i++) {
    const row = data[i];
    
    // Skip empty rows
    if (row.every(cell => cell === '' || cell === null)) continue;
    
    const tx = {
      tx_id: extractValue(row, columnAnalysis.idColumn, i),
      amount: extractAmount(row, columnAnalysis.amountColumns),
      date: extractDate(row, columnAnalysis.dateColumn),
      reference: extractReference(row, columnAnalysis.referenceColumn, columnAnalysis.idColumn),
      raw_row: i + 1,
      raw_data: row
    };
    
    // Only include rows with valid amount
    if (tx.amount !== null && !isNaN(tx.amount)) {
      transactions.push(tx);
    }
  }
  
  Logger.log('Read ' + transactions.length + ' transactions from ' + sheetName);
  return transactions;
}

/**
 * Analyze columns to detect their types.
 */
function analyzeColumns(data, startRow) {
  const numCols = data[0].length;
  const sampleRows = data.slice(startRow, Math.min(startRow + 20, data.length));
  
  const analysis = {
    dateColumn: -1,
    amountColumns: [],
    idColumn: -1,
    referenceColumn: -1
  };
  
  for (let col = 0; col < numCols; col++) {
    const values = sampleRows.map(row => row[col]).filter(v => v !== '' && v !== null);
    if (values.length === 0) continue;
    
    const stats = analyzeColumnValues(values);
    
    // Date column: mostly dates
    if (stats.dateRatio > 0.7 && analysis.dateColumn === -1) {
      analysis.dateColumn = col;
    }
    
    // Amount column: mostly large numbers (likely currency)
    if (stats.isAmount && stats.numberRatio > 0.8) {
      analysis.amountColumns.push({ col: col, avgAbs: stats.avgAbsValue });
    }
    
    // ID column: sequential numbers or short alphanumeric
    if (stats.isSequential || (stats.isShortAlphanumeric && analysis.idColumn === -1)) {
      analysis.idColumn = col;
    }
    
    // Reference column: longer alphanumeric strings
    if (stats.isReference && analysis.referenceColumn === -1) {
      analysis.referenceColumn = col;
    }
  }
  
  // If multiple amount columns, pick the one with largest absolute values
  if (analysis.amountColumns.length > 1) {
    analysis.amountColumns.sort((a, b) => b.avgAbs - a.avgAbs);
  }
  
  // Fallback: if no ID column found, use row number
  if (analysis.idColumn === -1) {
    analysis.idColumn = 0;
  }
  
  Logger.log('Column analysis: date=' + analysis.dateColumn + 
             ', amounts=' + analysis.amountColumns.map(a => a.col).join(',') +
             ', id=' + analysis.idColumn + ', ref=' + analysis.referenceColumn);
  
  return analysis;
}

/**
 * Analyze values in a column to determine its type.
 */
function analyzeColumnValues(values) {
  let dateCount = 0;
  let numberCount = 0;
  let largeNumberCount = 0;
  let sequentialCount = 0;
  let shortAlphanumericCount = 0;
  let referenceCount = 0;
  let totalAbsValue = 0;
  let lastNum = null;
  
  values.forEach((val, idx) => {
    // Check if date
    if (val instanceof Date || isDateString(val)) {
      dateCount++;
    }
    
    // Check if number
    const num = parseNumber(val);
    if (num !== null) {
      numberCount++;
      totalAbsValue += Math.abs(num);
      
      // Large number (likely currency)
      if (Math.abs(num) > 100) {
        largeNumberCount++;
      }
      
      // Sequential check
      if (lastNum !== null && (num === lastNum + 1 || num === lastNum)) {
        sequentialCount++;
      }
      lastNum = num;
    }
    
    // String analysis
    if (typeof val === 'string') {
      if (val.length <= 10 && /^[A-Za-z0-9]+$/.test(val)) {
        shortAlphanumericCount++;
      }
      if (val.length >= 5 && val.length <= 30 && /^[A-Za-z0-9\-_]+$/.test(val)) {
        referenceCount++;
      }
    }
  });
  
  const count = values.length;
  return {
    dateRatio: dateCount / count,
    numberRatio: numberCount / count,
    isAmount: largeNumberCount > count * 0.5,
    avgAbsValue: numberCount > 0 ? totalAbsValue / numberCount : 0,
    isSequential: sequentialCount > count * 0.7,
    isShortAlphanumeric: shortAlphanumericCount > count * 0.7,
    isReference: referenceCount > count * 0.5
  };
}

/**
 * Check if a value looks like a date string.
 */
function isDateString(val) {
  if (typeof val !== 'string') return false;
  // Common date patterns: 24-Aug-11, 2024-01-15, 01/15/2024, etc.
  return /^\d{1,2}[-\/]\w{3}[-\/]\d{2,4}$/.test(val) ||
         /^\d{4}[-\/]\d{2}[-\/]\d{2}$/.test(val) ||
         /^\d{1,2}[-\/]\d{1,2}[-\/]\d{2,4}$/.test(val);
}

/**
 * Parse a number from various formats.
 */
function parseNumber(val) {
  if (typeof val === 'number') return val;
  if (typeof val !== 'string') return null;
  
  // Remove currency symbols, commas, spaces
  const cleaned = val.replace(/[$£€¥₦₵,\s]/g, '').trim();
  
  // Handle parentheses for negative (accounting format)
  if (/^\([\d.]+\)$/.test(cleaned)) {
    return -parseFloat(cleaned.replace(/[()]/g, ''));
  }
  
  const num = parseFloat(cleaned);
  return isNaN(num) ? null : num;
}

/**
 * Extract a value from a row for a given column.
 */
function extractValue(row, colIndex, rowIndex) {
  if (colIndex < 0 || colIndex >= row.length) {
    return 'ROW_' + (rowIndex + 1);
  }
  const val = row[colIndex];
  return val !== '' && val !== null ? String(val) : 'ROW_' + (rowIndex + 1);
}

/**
 * Extract amount from row, trying multiple columns.
 */
function extractAmount(row, amountColumns) {
  for (const ac of amountColumns) {
    const val = row[ac.col];
    const num = parseNumber(val);
    if (num !== null && !isNaN(num)) {
      return num;
    }
  }
  return null;
}

/**
 * Extract and parse date from row.
 */
function extractDate(row, colIndex) {
  if (colIndex < 0 || colIndex >= row.length) return new Date();
  
  const val = row[colIndex];
  
  if (val instanceof Date) return val;
  
  if (typeof val === 'string') {
    // Try parsing various formats
    const parsed = new Date(val);
    if (!isNaN(parsed.getTime())) return parsed;
    
    // Handle "24-Aug-11" format
    const match = val.match(/^(\d{1,2})[-\/](\w{3})[-\/](\d{2,4})$/);
    if (match) {
      const months = {jan:0, feb:1, mar:2, apr:3, may:4, jun:5, jul:6, aug:7, sep:8, oct:9, nov:10, dec:11};
      const day = parseInt(match[1]);
      const month = months[match[2].toLowerCase()];
      let year = parseInt(match[3]);
      if (year < 100) year += 2000;
      if (month !== undefined) {
        return new Date(year, month, day);
      }
    }
  }
  
  return new Date();
}

/**
 * Extract reference from row.
 */
function extractReference(row, refColIndex, idColIndex) {
  if (refColIndex >= 0 && refColIndex < row.length) {
    const val = row[refColIndex];
    if (val !== '' && val !== null) return String(val);
  }
  // Fallback to ID column
  if (idColIndex >= 0 && idColIndex < row.length) {
    return String(row[idColIndex]);
  }
  return '';
}

// =============================================================================
// MAIN RECONCILIATION ENGINE
// =============================================================================

/**
 * Main reconciliation function - runs entirely client-side.
 * 
 * @param {Array} gatewayData - Gateway transactions [{tx_id, amount, date, reference}, ...]
 * @param {Array} bankData - Bank transactions [{tx_id, amount, date, reference}, ...]
 * @param {Array} internalData - Internal ledger [{tx_id, amount, date, reference}, ...]
 * @param {Object} config - {amountTolerancePct: 0.5, dateWindowDays: 3}
 * @returns {Object} {summary, reconciled, exceptions}
 */
function runReconciliationLocal(gatewayData, bankData, internalData, config) {
  const amountTolerance = config.amountTolerancePct || 0.5;
  const dateWindow = config.dateWindowDays || 3;
  
  // Normalize data
  const gateway = normalizeTransactions(gatewayData, 'gateway');
  const bank = normalizeTransactions(bankData, 'bank');
  const internal = normalizeTransactions(internalData, 'internal');
  
  // Track what's been matched
  const matchedGateway = new Set();
  const matchedBank = new Set();
  const matchedInternal = new Set();
  
  const reconciled = [];
  const exceptions = [];
  
  // Phase 1: 3-way matching (gateway + bank + internal)
  gateway.forEach(gw => {
    if (matchedGateway.has(gw.tx_id)) return;
    
    const bankMatch = findMatch(gw, bank, matchedBank, amountTolerance, dateWindow);
    const internalMatch = findMatch(gw, internal, matchedInternal, amountTolerance, dateWindow);
    
    if (bankMatch && internalMatch) {
      // 3-way match - calculate match score based on how close the matches are
      const bankScore = calculateMatchScore(gw, bankMatch.tx, amountTolerance, dateWindow);
      const internalScore = calculateMatchScore(gw, internalMatch.tx, amountTolerance, dateWindow);
      const matchScore = (bankScore + internalScore) / 2;
      
      reconciled.push({
        match_type: '3-way',
        gateway_tx_ids: [gw.tx_id],
        bank_tx_ids: [bankMatch.tx.tx_id],
        internal_tx_ids: [internalMatch.tx.tx_id],
        amount: gw.amount,
        date: gw.date,
        status: 'matched',
        matchScore: matchScore,
        source: gw,
        reasoning: buildMatchReasoning(gw, bankMatch, internalMatch, '3-way')
      });
      
      matchedGateway.add(gw.tx_id);
      matchedBank.add(bankMatch.tx.tx_id);
      matchedInternal.add(internalMatch.tx.tx_id);
    }
  });
  
  // Phase 2: 2-way matching (gateway + bank only)
  gateway.forEach(gw => {
    if (matchedGateway.has(gw.tx_id)) return;
    
    const bankMatch = findMatch(gw, bank, matchedBank, amountTolerance, dateWindow);
    
    if (bankMatch) {
      const matchScore = calculateMatchScore(gw, bankMatch.tx, amountTolerance, dateWindow);
      
      reconciled.push({
        match_type: '2-way-gb',
        gateway_tx_ids: [gw.tx_id],
        bank_tx_ids: [bankMatch.tx.tx_id],
        internal_tx_ids: [],
        amount: gw.amount,
        date: gw.date,
        status: 'partial',
        matchScore: matchScore,
        source: gw,
        reasoning: buildMatchReasoning(gw, bankMatch, null, '2-way-gb')
      });
      
      matchedGateway.add(gw.tx_id);
      matchedBank.add(bankMatch.tx.tx_id);
    }
  });
  
  // Phase 3: 2-way matching (gateway + internal only)
  gateway.forEach(gw => {
    if (matchedGateway.has(gw.tx_id)) return;
    
    const internalMatch = findMatch(gw, internal, matchedInternal, amountTolerance, dateWindow);
    
    if (internalMatch) {
      const matchScore = calculateMatchScore(gw, internalMatch.tx, amountTolerance, dateWindow);
      
      reconciled.push({
        match_type: '2-way-gi',
        gateway_tx_ids: [gw.tx_id],
        bank_tx_ids: [],
        internal_tx_ids: [internalMatch.tx.tx_id],
        amount: gw.amount,
        date: gw.date,
        status: 'partial',
        matchScore: matchScore,
        source: gw,
        reasoning: buildMatchReasoning(gw, null, internalMatch, '2-way-gi')
      });
      
      matchedGateway.add(gw.tx_id);
      matchedInternal.add(internalMatch.tx.tx_id);
    }
  });
  
  // Phase 4: Collect exceptions (unmatched transactions)
  gateway.forEach(gw => {
    if (!matchedGateway.has(gw.tx_id)) {
      exceptions.push(createException(gw, 'gateway', 'no_match', bank, internal, amountTolerance, dateWindow));
    }
  });
  
  bank.forEach(bk => {
    if (!matchedBank.has(bk.tx_id)) {
      exceptions.push(createException(bk, 'bank', 'no_match', gateway, internal, amountTolerance, dateWindow));
    }
  });
  
  internal.forEach(int => {
    if (!matchedInternal.has(int.tx_id)) {
      exceptions.push(createException(int, 'internal', 'no_match', gateway, bank, amountTolerance, dateWindow));
    }
  });
  
  // Build summary
  const totalGatewayVolume = gateway.reduce((sum, t) => sum + t.amount, 0);
  const totalBankVolume = bank.reduce((sum, t) => sum + t.amount, 0);
  const matchedVolume = reconciled.reduce((sum, r) => sum + r.amount, 0);
  const matchedPct = totalGatewayVolume > 0 ? (matchedVolume / totalGatewayVolume) * 100 : 0;
  
  const summary = {
    run_timestamp: new Date().toISOString(),
    total_gateway_volume: totalGatewayVolume,
    total_bank_volume: totalBankVolume,
    matched_volume: matchedVolume,
    matched_pct: matchedPct,
    total_reconciled: reconciled.length,
    total_exceptions: exceptions.length,
    three_way_matches: reconciled.filter(r => r.match_type === '3-way').length,
    two_way_matches: reconciled.filter(r => r.match_type.startsWith('2-way')).length
  };
  
  return { summary, reconciled, exceptions };
}


/**
 * Normalize transactions to consistent format.
 */
function normalizeTransactions(data, source) {
  return data.map((row, idx) => {
    // Handle both array (from sheet) and object formats
    let tx_id, amount, date, reference;
    
    if (Array.isArray(row)) {
      // From sheet: [tx_id, amount, date, reference, ...]
      tx_id = row[0] || `${source}_${idx}`;
      amount = parseFloat(row[1]) || 0;
      date = parseDate(row[2]);
      reference = row[3] || '';
    } else {
      // Object format
      tx_id = row.tx_id || row.id || `${source}_${idx}`;
      amount = parseFloat(row.amount) || 0;
      date = parseDate(row.date);
      reference = row.reference || row.ref || '';
    }
    
    return {
      tx_id: String(tx_id),
      amount: Math.abs(amount),
      date: date,
      reference: String(reference).toLowerCase().trim(),
      source: source
    };
  }).filter(t => t.amount > 0);
}


/**
 * Parse date to consistent format.
 */
function parseDate(dateVal) {
  if (!dateVal) return null;
  
  if (dateVal instanceof Date) {
    return dateVal;
  }
  
  // Try parsing string
  const parsed = new Date(dateVal);
  if (!isNaN(parsed.getTime())) {
    return parsed;
  }
  
  return null;
}


/**
 * Find matching transaction within tolerance.
 * Returns match details including score and reasoning.
 */
function findMatch(source, candidates, alreadyMatched, amountTolerance, dateWindow) {
  let bestMatch = null;
  let bestScore = 0;
  
  for (const candidate of candidates) {
    if (alreadyMatched.has(candidate.tx_id)) continue;
    
    // Check amount tolerance
    const amountDiff = Math.abs(source.amount - candidate.amount);
    const amountPct = source.amount > 0 ? (amountDiff / source.amount) * 100 : 0;
    
    if (amountPct > amountTolerance) continue;
    
    // Check date window
    let daysDiff = 0;
    if (source.date && candidate.date) {
      daysDiff = Math.abs((source.date - candidate.date) / (1000 * 60 * 60 * 24));
      if (daysDiff > dateWindow) continue;
    }
    
    // Calculate match score
    const amountScore = 1 - (amountPct / amountTolerance);
    const dateScore = 1 - (daysDiff / dateWindow);
    const referenceScore = (source.reference && candidate.reference && 
                           source.reference === candidate.reference) ? 1 : 0;
    
    // Weighted score
    const score = (amountScore * 0.5) + (dateScore * 0.3) + (referenceScore * 0.2);
    
    if (score > bestScore) {
      bestScore = score;
      bestMatch = {
        tx: candidate,
        score: score,
        amountDiffPct: amountPct,
        daysDiff: daysDiff,
        referenceMatch: referenceScore > 0
      };
    }
  }
  
  return bestMatch;
}


/**
 * Calculate match score between two transactions.
 */
function calculateMatchScore(source, target, amountTolerance, dateWindow) {
  if (!source || !target) return 0;
  
  const amountDiff = Math.abs(source.amount - target.amount);
  const amountPct = source.amount > 0 ? (amountDiff / source.amount) * 100 : 0;
  const amountScore = Math.max(0, 1 - (amountPct / amountTolerance));
  
  let dateScore = 1;
  if (source.date && target.date) {
    const daysDiff = Math.abs((source.date - target.date) / (1000 * 60 * 60 * 24));
    dateScore = Math.max(0, 1 - (daysDiff / dateWindow));
  }
  
  const referenceScore = (source.reference && target.reference && 
                         source.reference === target.reference) ? 1 : 0;
  
  return (amountScore * 0.5) + (dateScore * 0.3) + (referenceScore * 0.2);
}


/**
 * Build human-readable reasoning for a match.
 */
function buildMatchReasoning(source, bankMatch, internalMatch, matchType) {
  const lines = [];
  
  if (matchType === '3-way') {
    lines.push('Matched: Gateway ↔ Bank ↔ Internal');
    lines.push('├── Amount: All 3 sources within tolerance');
    if (bankMatch && bankMatch.amountDiffPct !== undefined) {
      lines.push(`│   └── Bank variance: ${bankMatch.amountDiffPct.toFixed(2)}%`);
    }
    if (internalMatch && internalMatch.amountDiffPct !== undefined) {
      lines.push(`│   └── Internal variance: ${internalMatch.amountDiffPct.toFixed(2)}%`);
    }
    if (bankMatch && bankMatch.daysDiff !== undefined) {
      lines.push(`├── Date: Within ${Math.round(Math.max(bankMatch.daysDiff, internalMatch ? internalMatch.daysDiff : 0))} day(s)`);
    }
    if (bankMatch && bankMatch.referenceMatch) {
      lines.push('└── Reference: Matched');
    } else {
      lines.push('└── Reference: No match (not required)');
    }
  } else if (matchType === '2-way-gb') {
    lines.push('Matched: Gateway ↔ Bank (no internal)');
    if (bankMatch) {
      lines.push(`├── Amount variance: ${bankMatch.amountDiffPct.toFixed(2)}%`);
      lines.push(`├── Date offset: ${Math.round(bankMatch.daysDiff)} day(s)`);
      lines.push('└── Missing: Internal ledger entry [!]');
    }
  } else if (matchType === '2-way-gi') {
    lines.push('Matched: Gateway ↔ Internal (no bank)');
    if (internalMatch) {
      lines.push(`├── Amount variance: ${internalMatch.amountDiffPct.toFixed(2)}%`);
      lines.push(`├── Date offset: ${Math.round(internalMatch.daysDiff)} day(s)`);
      lines.push('└── Missing: Bank transaction [!]');
    }
  }
  
  return lines.join('\n');
}


/**
 * Create exception with metadata for LLM explanation.
 */
function createException(tx, source, reason, compareSet1, compareSet2, amountTolerance, dateWindow) {
  // Find closest matches to provide context
  const nearMatches = findNearMatches(tx, [...compareSet1, ...compareSet2], amountTolerance * 3, dateWindow * 2);
  
  // Build metadata for LLM context
  const metadata = {
    exception_type: reason,
    source_type: source,
    has_near_amount_match: nearMatches.some(m => m.amountDiffPct < amountTolerance * 2),
    has_near_date_match: nearMatches.some(m => m.daysDiff < dateWindow * 2),
    nearest_amount_diff_pct: nearMatches.length > 0 ? nearMatches[0].amountDiffPct : null,
    nearest_days_diff: nearMatches.length > 0 ? nearMatches[0].daysDiff : null,
    tolerance_pct: amountTolerance,
    date_window_days: dateWindow
  };
  
  // Build reasoning for the exception
  const exceptionReasoning = buildExceptionReasoning(tx, source, reason, nearMatches, amountTolerance, dateWindow);
  
  return {
    tx_id: tx.tx_id,
    source: source,
    amount: tx.amount,
    date: tx.date ? tx.date.toISOString().split('T')[0] : null,
    reference: tx.reference,
    reason: reason,
    exception_type: reason === 'no_match' ? 'unmatched' : reason,
    metadata: metadata,
    source_tx: tx,  // Include full tx for categorization
    llm_explanation: '',  // To be filled by LLM
    suggested_action: generateRuleBasedSuggestion(metadata),  // Fallback
    reasoning: exceptionReasoning,
    explanation: exceptionReasoning  // Human-readable explanation
  };
}


/**
 * Build human-readable reasoning for an exception.
 */
function buildExceptionReasoning(tx, source, reason, nearMatches, amountTolerance, dateWindow) {
  const lines = [`Unmatched ${source} transaction`];
  
  if (nearMatches.length === 0) {
    lines.push('├── No similar transactions found');
    lines.push('└── Action: Review source data or investigate missing entry');
  } else {
    lines.push('├── Near matches found:');
    nearMatches.slice(0, 3).forEach((match, idx) => {
      const isLast = idx === Math.min(nearMatches.length, 3) - 1;
      const prefix = isLast ? '│   └──' : '│   ├──';
      let reason = [];
      
      if (match.amountDiffPct > amountTolerance) {
        reason.push(`amount off by ${match.amountDiffPct.toFixed(1)}%`);
      }
      if (match.daysDiff > dateWindow) {
        reason.push(`date off by ${Math.round(match.daysDiff)} days`);
      }
      
      lines.push(`${prefix} ${match.source}: ${reason.join(', ') || 'no match'}`);
    });
    
    const closestMatch = nearMatches[0];
    if (closestMatch.amountDiffPct <= amountTolerance * 2 && closestMatch.daysDiff <= dateWindow * 2) {
      lines.push('└── Action: Consider increasing tolerance or manual review');
    } else {
      lines.push('└── Action: Investigate missing transaction');
    }
  }
  
  return lines.join('\n');
}


/**
 * Find near matches for context.
 */
function findNearMatches(tx, candidates, amountToleranceExpanded, dateWindowExpanded) {
  const matches = [];
  
  for (const candidate of candidates) {
    const amountDiff = Math.abs(tx.amount - candidate.amount);
    const amountDiffPct = tx.amount > 0 ? (amountDiff / tx.amount) * 100 : 100;
    
    let daysDiff = 999;
    if (tx.date && candidate.date) {
      daysDiff = Math.abs((tx.date - candidate.date) / (1000 * 60 * 60 * 24));
    }
    
    if (amountDiffPct <= amountToleranceExpanded || daysDiff <= dateWindowExpanded) {
      matches.push({
        tx_id: candidate.tx_id,
        source: candidate.source,
        amountDiffPct: amountDiffPct,
        daysDiff: daysDiff
      });
    }
  }
  
  // Sort by closest match
  matches.sort((a, b) => (a.amountDiffPct + a.daysDiff) - (b.amountDiffPct + b.daysDiff));
  
  return matches.slice(0, 3);
}


/**
 * Generate rule-based suggestion (fallback if LLM unavailable).
 */
function generateRuleBasedSuggestion(metadata) {
  if (metadata.has_near_amount_match && !metadata.has_near_date_match) {
    return 'Found a potential match with similar amount but outside date window. Verify transaction dates.';
  }
  
  if (metadata.has_near_date_match && !metadata.has_near_amount_match) {
    return 'Found a potential match within date range but amount differs. Check for partial payments or adjustments.';
  }
  
  if (metadata.has_near_amount_match && metadata.has_near_date_match) {
    return 'Near match found but outside tolerances. Consider adjusting tolerance settings or investigate discrepancy.';
  }
  
  if (metadata.source_type === 'gateway') {
    return 'No matching bank or internal entry found. Verify transaction was processed and recorded.';
  }
  
  if (metadata.source_type === 'bank') {
    return 'Bank transaction not matched to gateway. Check if payment was received through alternate channel.';
  }
  
  if (metadata.source_type === 'internal') {
    return 'Internal record not matched. Verify journal entry and source documentation.';
  }
  
  return 'Review transaction and compare against source systems.';
}


/**
 * Read sheet data as array of arrays.
 */
function readSheetData(sheetName) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheetByName(sheetName);
  
  if (!sheet) {
    throw new Error(`Sheet "${sheetName}" not found`);
  }
  
  const data = sheet.getDataRange().getValues();
  
  // Skip header row
  if (data.length > 1) {
    return data.slice(1);
  }
  
  return [];
}


/**
 * Write reconciliation results to sheets WITH Gmail message IDs
 */
function writeReconciliationResults(results) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();

  // Write Summary
  let summarySheet = ss.getSheetByName('CLSUMMARY');
  if (!summarySheet) summarySheet = ss.insertSheet('CLSUMMARY');
  summarySheet.clear();
  
  const summaryHeaders = ['Metric', 'Value'];
  const summaryData = [
    summaryHeaders,
    ['Run Timestamp', results.summary.runtimestamp],
    ['Total Gateway Volume', results.summary.totalgatewayvolume],
    ['Total Bank Volume', results.summary.totalbankvolume],
    ['Matched Volume', results.summary.matchedvolume],
    ['Match Rate (%)', results.summary.matchedpct.toFixed(1)],
    ['3-Way Matches', results.summary.threewaymatches],
    ['2-Way Matches', results.summary.twowaymatches],
    ['Total Exceptions', results.summary.totalexceptions]
  ];
  summarySheet.getRange(1, 1, summaryData.length, 2).setValues(summaryData);

  // Write Reconciled WITH Gmail message ID column (NEW!)
  let reconciledSheet = ss.getSheetByName('CLRECONCILED');
  if (!reconciledSheet) reconciledSheet = ss.insertSheet('CLRECONCILED');
  reconciledSheet.clear();
  
  const reconciledHeaders = ['match_group_id', 'match_type', 'gateway_tx_ids', 'bank_tx_ids', 'sap_doc_numbers', 'amount_gateway', 'amount_bank', 'amount_sap', 'confidence_score', 'date_gateway', 'date_bank', 'date_sap', 'fee_detected', 'fee_amount', 'reconciled_at', 'je_created', 'je_entry_id', 'gmail_message_id'];
  const reconciledData = [reconciledHeaders];
  
  results.reconciled.forEach(function(r, idx) {
    reconciledData.push([
      r.match_group_id || `match_${idx + 1}`,
      r.matchtype,
      r.gatewaytxids.join(', '),
      r.banktxids.join(', '),
      (r.sapdocnumbers || []).join(', '),
      r.amount_gateway || r.amount || '',
      r.amount_bank || '',
      r.amount_sap || '',
      r.matchScore || r.confidence || '',
      r.date_gateway || (r.date ? r.date.toISOString().split('T')[0] : ''),
      r.date_bank || '',
      r.date_sap || '',
      r.fee_detected || false,
      r.fee_amount || 0,
      new Date().toISOString(),
      r.je_created || false,
      r.je_entry_id || '',
      r.gmailMessageId || ''
    ]);
  });
  
  reconciledSheet.getRange(1, 1, reconciledData.length, reconciledHeaders.length).setValues(reconciledData);
  reconciledSheet.getRange(1, 1, 1, reconciledHeaders.length).setFontWeight('bold').setBackground('#366092').setFontColor('#FFFFFF');
  reconciledSheet.autoResizeColumns(1, reconciledHeaders.length);

  // Write Exceptions WITH Gmail message ID column (NEW!)
  let exceptionsSheet = ss.getSheetByName('CLEXCEPTIONS');
  if (!exceptionsSheet) exceptionsSheet = ss.insertSheet('CLEXCEPTIONS');
  exceptionsSheet.clear();
  
  const exceptionsHeaders = ['exception_id', 'source', 'transaction_ids', 'date', 'amount', 'description', 'reason', 'explanation', 'suggested_action', 'priority', 'status', 'assigned_to', 'notes', 'gmail_message_id'];
  const exceptionsData = [exceptionsHeaders];
  
  results.exceptions.forEach(function(e, idx) {
    exceptionsData.push([
      e.exception_id || `exc_${idx + 1}`,
      e.source,
      e.txids ? e.txids.join(', ') : e.txid,
      e.date || '',
      e.amount || '',
      e.reference || e.description || '',
      e.reason,
      e.llmexplanation || '',
      e.suggestedaction || '',
      e.priority || 'Medium',
      e.status || 'Pending',
      e.assigned_to || '',
      '',
      e.gmailMessageId || ''
    ]);
  });
  
  exceptionsSheet.getRange(1, 1, exceptionsData.length, exceptionsHeaders.length).setValues(exceptionsData);
  
  // Format header
  exceptionsSheet.getRange(1, 1, 1, exceptionsHeaders.length).setFontWeight('bold').setBackground('#366092').setFontColor('#FFFFFF');
  exceptionsSheet.autoResizeColumns(1, exceptionsHeaders.length);

  // Write Draft Journal Entries (local draft surface for approvals)
  let draftsSheet = ss.getSheetByName('CLDRAFTENTRIES');
  if (!draftsSheet) draftsSheet = ss.insertSheet('CLDRAFTENTRIES');
  draftsSheet.clear();

  const draftHeaders = [
    'entry_id',
    'date',
    'description',
    'debit_accounts',
    'credit_accounts',
    'total_debits',
    'total_credits',
    'confidence',
    'match_group_id',
    'status',
    'sap_doc_number',
    'created_at',
    'approved_by',
    'approved_at',
    'posted_at',
    'gmail_message_ids'
  ];
  const draftData = [draftHeaders];

  // Derive lightweight drafts from matched groups (local-only; backend drafts remain authoritative)
  if (results.reconciled && results.reconciled.length > 0) {
    const nowTs = new Date().toISOString();
    results.reconciled.forEach(function(r, idx) {
      const entryId = 'DRAFT_' + nowTs.replace(/[:.T-]/g, '') + '_' + idx;
      const desc = (r.matchtype || 'Match') + (r.reference ? ' ' + r.reference : '');
      const ids = [r.gatewaytxids, r.banktxids, r.internaltxids]
        .filter(Boolean)
        .map(function(arr) { return (arr || []).join(', '); })
        .filter(function(x) { return x; })
        .join(' | ');
      const gmailIds = r.gmailMessageId ? r.gmailMessageId : '';
      const amount = r.amount || 0;
      const matchGroupId = ids || entryId;
      const debitAccounts = JSON.stringify([{ account: '1010', name: 'Cash', amount: amount }]);
      const creditAccounts = JSON.stringify([{ account: '1200', name: 'Clearing', amount: amount }]);
      const confidence = r.matchScore || r.score || 0.9;
      draftData.push([
        entryId,
        r.date ? (r.date.toISOString ? r.date.toISOString().split('T')[0] : r.date) : '',
        desc,
        debitAccounts,
        creditAccounts,
        amount,
        amount,
        confidence,
        matchGroupId,
        'DRAFT',
        '', // sap_doc_number
        nowTs,
        '', // approved_by
        '', // approved_at
        '', // posted_at
        gmailIds
      ]);
    });
  }

  draftsSheet.getRange(1, 1, draftData.length, draftHeaders.length).setValues(draftData);
  draftsSheet.getRange(1, 1, 1, draftHeaders.length).setFontWeight('bold').setBackground('#e5f1fb').setFontColor('#1f2937');
  draftsSheet.autoResizeColumns(1, draftHeaders.length);

  return { success: true, summary: results.summary };
}
