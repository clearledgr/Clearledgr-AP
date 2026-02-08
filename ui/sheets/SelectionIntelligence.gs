/**
 * Clearledgr Selection Intelligence
 * 
 * Option 3: Inline suggestions when user selects a cell
 * Option 5: Selection-based sidebar that shows analysis for selected rows
 * 
 * No formulas to type. Just select rows and see intelligence.
 */

// =============================================================================
// SELECTION CHANGE TRIGGER
// =============================================================================

/**
 * Triggered when user changes selection in the spreadsheet.
 * Shows inline suggestions and updates sidebar.
 */
function onSelectionChange(e) {
  try {
    var range = e.range;
    if (!range) return;
    
    var sheet = range.getSheet();
    var sheetName = sheet.getName();
    
    // Skip Clearledgr system sheets
    if (sheetName.startsWith('CL')) return;
    
    // Get selected rows
    var startRow = range.getRow();
    var numRows = range.getNumRows();
    
    // Skip header row
    if (startRow === 1 && numRows === 1) return;
    
    // Get the data for selected rows
    var lastCol = sheet.getLastColumn();
    if (lastCol < 1) return;
    
    var dataRange = sheet.getRange(startRow, 1, numRows, lastCol);
    var data = dataRange.getValues();
    
    // Analyze selection
    var analysis = analyzeSelectedRows(data, sheetName, startRow);
    
    // Show inline suggestion (toast for single row)
    if (numRows === 1 && analysis.suggestions.length > 0) {
      showInlineSuggestion(analysis.suggestions[0]);
    }
    
    // Store analysis for sidebar to pick up
    var cache = CacheService.getUserCache();
    cache.put('clearledgr_selection', JSON.stringify({
      sheetName: sheetName,
      startRow: startRow,
      numRows: numRows,
      analysis: analysis,
      timestamp: new Date().toISOString()
    }), 60); // Cache for 60 seconds
    
  } catch (err) {
    // Silent fail - don't interrupt user
    console.log('Selection change error: ' + err.message);
  }
}

/**
 * Analyze selected rows and return suggestions.
 */
function analyzeSelectedRows(data, sheetName, startRow) {
  var suggestions = [];
  var matches = [];
  var categories = [];
  var flags = [];
  
  for (var i = 0; i < data.length; i++) {
    var row = data[i];
    var rowNum = startRow + i;
    var rowAnalysis = analyzeTransactionRow(row, sheetName);
    
    if (rowAnalysis.match) {
      matches.push({
        row: rowNum,
        match: rowAnalysis.match
      });
      suggestions.push({
        type: 'match',
        row: rowNum,
        text: 'Match found: ' + rowAnalysis.match.reference + ' (' + rowAnalysis.match.confidence + '%)',
        action: 'link_match',
        data: rowAnalysis.match
      });
    }
    
    if (rowAnalysis.category) {
      categories.push({
        row: rowNum,
        category: rowAnalysis.category
      });
      if (!rowAnalysis.match) { // Don't double-suggest
        suggestions.push({
          type: 'category',
          row: rowNum,
          text: 'Categorize as: ' + rowAnalysis.category.name,
          action: 'apply_category',
          data: rowAnalysis.category
        });
      }
    }
    
    if (rowAnalysis.flags && rowAnalysis.flags.length > 0) {
      flags.push({
        row: rowNum,
        flags: rowAnalysis.flags
      });
    }
  }
  
  return {
    suggestions: suggestions,
    matches: matches,
    categories: categories,
    flags: flags,
    rowCount: data.length
  };
}

/**
 * Analyze a single transaction row.
 */
function analyzeTransactionRow(row, sourceSheet) {
  var result = {
    match: null,
    category: null,
    flags: []
  };
  
  // Extract transaction data
  var amount = findAmountInRow(row);
  var description = findDescriptionInRow(row);
  var date = findDateInRow(row);
  
  if (!amount && !description) return result;
  
  // Find potential matches in other sheets
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheets = ss.getSheets();
  
  for (var i = 0; i < sheets.length; i++) {
    var sheet = sheets[i];
    var name = sheet.getName();
    
    // Skip same sheet and system sheets
    if (name === sourceSheet || name.startsWith('CL')) continue;
    
    // Look for matching transactions
    var match = findBestMatch(amount, description, date, sheet);
    if (match && match.confidence > 70) {
      result.match = match;
      break;
    }
  }
  
  // Suggest category
  if (description) {
    result.category = suggestCategory(description);
  }
  
  // Check for flags
  if (amount && Math.abs(amount) > 10000) {
    result.flags.push('LARGE AMOUNT');
  }
  if (amount && amount % 1000 === 0 && Math.abs(amount) >= 1000) {
    result.flags.push('ROUND NUMBER');
  }
  if (date) {
    var day = new Date(date).getDay();
    if (day === 0 || day === 6) {
      result.flags.push('WEEKEND');
    }
  }
  
  return result;
}

/**
 * Find best match in a sheet.
 */
function findBestMatch(amount, description, date, searchSheet) {
  if (!amount) return null;
  
  try {
    var data = searchSheet.getDataRange().getValues();
    if (data.length < 2) return null;
    
    var bestMatch = null;
    var bestScore = 0;
    
    for (var i = 1; i < data.length; i++) { // Skip header
      var row = data[i];
      var rowAmount = findAmountInRow(row);
      var rowDesc = findDescriptionInRow(row);
      var rowDate = findDateInRow(row);
      
      if (!rowAmount) continue;
      
      var score = 0;
      
      // Amount matching (50 points max)
      var amountDiff = Math.abs(amount - rowAmount);
      var amountPct = Math.abs(amount) > 0 ? amountDiff / Math.abs(amount) : 1;
      if (amountPct === 0) score += 50;
      else if (amountPct <= 0.03) score += 40; // Within 3% (fees)
      else if (amountPct <= 0.05) score += 25;
      else if (amountPct <= 0.10) score += 10;
      
      // Date matching (30 points max)
      if (date && rowDate) {
        var daysDiff = Math.abs((new Date(date) - new Date(rowDate)) / (1000 * 60 * 60 * 24));
        if (daysDiff === 0) score += 30;
        else if (daysDiff <= 2) score += 25;
        else if (daysDiff <= 5) score += 15;
        else if (daysDiff <= 10) score += 5;
      }
      
      // Description matching (20 points max)
      if (description && rowDesc) {
        var descScore = calculateTextSimilarity(description, rowDesc);
        score += Math.round(descScore * 20);
      }
      
      if (score > bestScore) {
        bestScore = score;
        bestMatch = {
          sheet: searchSheet.getName(),
          row: i + 1,
          reference: searchSheet.getName() + '!Row ' + (i + 1),
          confidence: score,
          amount: rowAmount,
          description: rowDesc,
          amountDiff: amountDiff
        };
      }
    }
    
    return bestMatch;
    
  } catch (err) {
    return null;
  }
}

/**
 * Suggest category based on description.
 */
function suggestCategory(description) {
  var desc = String(description).toLowerCase();
  
  var rules = [
    { keywords: ['stripe', 'paypal', 'payment processing', 'merchant'], code: '6900', name: 'Bank & Processing Fees' },
    { keywords: ['aws', 'amazon web', 'azure', 'google cloud', 'heroku', 'digital ocean'], code: '6000', name: 'Cloud & Infrastructure' },
    { keywords: ['software', 'subscription', 'saas', 'license'], code: '6010', name: 'Software Subscriptions' },
    { keywords: ['consulting', 'legal', 'accounting', 'professional'], code: '6100', name: 'Professional Services' },
    { keywords: ['marketing', 'advertising', 'ads', 'campaign'], code: '6200', name: 'Marketing' },
    { keywords: ['travel', 'flight', 'hotel', 'airbnb'], code: '6400', name: 'Travel' },
    { keywords: ['office', 'supplies', 'equipment'], code: '6300', name: 'Office & Supplies' },
    { keywords: ['payroll', 'salary', 'wages', 'bonus'], code: '6800', name: 'Payroll' },
    { keywords: ['rent', 'lease', 'facility'], code: '6700', name: 'Rent & Facilities' },
    { keywords: ['insurance', 'policy'], code: '6600', name: 'Insurance' },
  ];
  
  for (var i = 0; i < rules.length; i++) {
    for (var j = 0; j < rules[i].keywords.length; j++) {
      if (desc.indexOf(rules[i].keywords[j]) !== -1) {
        return { code: rules[i].code, name: rules[i].name };
      }
    }
  }
  
  return { code: '6990', name: 'Other Expenses' };
}

// =============================================================================
// INLINE SUGGESTIONS (Option 3)
// =============================================================================

/**
 * Show inline suggestion as a toast notification.
 */
function showInlineSuggestion(suggestion) {
  var message = suggestion.text;
  
  // Add action hint
  if (suggestion.type === 'match') {
    message += ' → Open sidebar to link';
  } else if (suggestion.type === 'category') {
    message += ' → Open sidebar to apply';
  }
  
  SpreadsheetApp.getActiveSpreadsheet().toast(message, 'Clearledgr', 5);
}

// =============================================================================
// SELECTION-BASED SIDEBAR (Option 5)
// =============================================================================

/**
 * Get current selection analysis for sidebar.
 * Called by sidebar JavaScript.
 */
function getSelectionAnalysis() {
  var cache = CacheService.getUserCache();
  var cached = cache.get('clearledgr_selection');
  
  if (cached) {
    return JSON.parse(cached);
  }
  
  // No cached analysis - analyze current selection
  var sheet = SpreadsheetApp.getActiveSheet();
  var range = sheet.getActiveRange();
  
  if (!range) {
    return { analysis: null, message: 'No selection' };
  }
  
  var startRow = range.getRow();
  var numRows = range.getNumRows();
  var lastCol = sheet.getLastColumn();
  
  if (startRow === 1 && numRows === 1) {
    return { analysis: null, message: 'Select transaction rows (not header)' };
  }
  
  var dataRange = sheet.getRange(startRow, 1, numRows, lastCol);
  var data = dataRange.getValues();
  
  var analysis = analyzeSelectedRows(data, sheet.getName(), startRow);
  
  return {
    sheetName: sheet.getName(),
    startRow: startRow,
    numRows: numRows,
    analysis: analysis,
    timestamp: new Date().toISOString()
  };
}

/**
 * Apply match from suggestion.
 */
function applyMatch(sourceRow, matchData) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sourceSheet = ss.getActiveSheet();
  
  // Add match reference to a "Match" column if it exists, or create note
  var headers = sourceSheet.getRange(1, 1, 1, sourceSheet.getLastColumn()).getValues()[0];
  var matchColIndex = -1;
  
  for (var i = 0; i < headers.length; i++) {
    var h = String(headers[i]).toLowerCase();
    if (h.indexOf('match') !== -1 || h.indexOf('reconciled') !== -1) {
      matchColIndex = i + 1;
      break;
    }
  }
  
  if (matchColIndex > 0) {
    sourceSheet.getRange(sourceRow, matchColIndex).setValue(matchData.reference + ' (' + matchData.confidence + '%)');
  } else {
    // Add as note
    var cell = sourceSheet.getRange(sourceRow, 1);
    cell.setNote('Clearledgr Match: ' + matchData.reference + ' (' + matchData.confidence + '%)');
  }
  
  // Log to reconciled sheet
  logMatch(sourceRow, matchData);
  
  return { success: true, message: 'Match linked: ' + matchData.reference };
}

/**
 * Apply category from suggestion.
 */
function applyCategory(sourceRow, categoryData) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sourceSheet = ss.getActiveSheet();
  
  // Find or create Category column
  var headers = sourceSheet.getRange(1, 1, 1, sourceSheet.getLastColumn()).getValues()[0];
  var catColIndex = -1;
  
  for (var i = 0; i < headers.length; i++) {
    var h = String(headers[i]).toLowerCase();
    if (h.indexOf('category') !== -1 || h.indexOf('gl') !== -1 || h.indexOf('account') !== -1) {
      catColIndex = i + 1;
      break;
    }
  }
  
  if (catColIndex < 0) {
    // Create new column
    catColIndex = sourceSheet.getLastColumn() + 1;
    sourceSheet.getRange(1, catColIndex).setValue('Category');
  }
  
  sourceSheet.getRange(sourceRow, catColIndex).setValue(categoryData.code + ' - ' + categoryData.name);
  
  return { success: true, message: 'Category applied: ' + categoryData.name };
}

/**
 * Log match to reconciled sheet.
 */
function logMatch(sourceRow, matchData) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var reconSheet = ss.getSheetByName('CLRECONCILED');
  
  if (!reconSheet) {
    reconSheet = ss.insertSheet('CLRECONCILED');
    reconSheet.getRange(1, 1, 1, 6).setValues([['Timestamp', 'Source', 'Match', 'Confidence', 'Amount Diff', 'User']]);
  }
  
  reconSheet.appendRow([
    new Date(),
    SpreadsheetApp.getActiveSheet().getName() + '!Row ' + sourceRow,
    matchData.reference,
    matchData.confidence + '%',
    matchData.amountDiff || 0,
    Session.getActiveUser().getEmail()
  ]);
}

// =============================================================================
// HELPER FUNCTIONS
// =============================================================================

function findAmountInRow(row) {
  for (var i = 0; i < row.length; i++) {
    var val = row[i];
    if (typeof val === 'number' && !isNaN(val) && val !== 0) {
      return val;
    }
    if (typeof val === 'string') {
      var cleaned = val.replace(/[€$£,\s]/g, '');
      var parsed = parseFloat(cleaned);
      if (!isNaN(parsed) && parsed !== 0) return parsed;
    }
  }
  return null;
}

function findDescriptionInRow(row) {
  for (var i = 0; i < row.length; i++) {
    var val = row[i];
    if (typeof val === 'string' && val.length > 5) {
      // Skip if it looks like a number or date
      if (!isNaN(parseFloat(val.replace(/[€$£,]/g, '')))) continue;
      if (!isNaN(Date.parse(val))) continue;
      return val;
    }
  }
  return null;
}

function findDateInRow(row) {
  for (var i = 0; i < row.length; i++) {
    var val = row[i];
    if (val instanceof Date) return val;
    if (typeof val === 'string') {
      var parsed = new Date(val);
      if (!isNaN(parsed.getTime())) return parsed;
    }
  }
  return null;
}

function calculateTextSimilarity(text1, text2) {
  var s1 = String(text1).toLowerCase().replace(/[^a-z0-9]/g, ' ').trim();
  var s2 = String(text2).toLowerCase().replace(/[^a-z0-9]/g, ' ').trim();
  
  if (s1 === s2) return 1.0;
  
  var words1 = s1.split(/\s+/).filter(function(w) { return w.length > 2; });
  var words2 = s2.split(/\s+/).filter(function(w) { return w.length > 2; });
  
  if (words1.length === 0 || words2.length === 0) return 0;
  
  var overlap = 0;
  for (var i = 0; i < words1.length; i++) {
    if (words2.indexOf(words1[i]) !== -1) overlap++;
  }
  
  return overlap / Math.max(words1.length, words2.length);
}
