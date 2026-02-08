/**
 * Clearledgr Custom Functions for Google Sheets
 * 
 * Finance teams think in spreadsheets. These custom functions bring
 * Clearledgr's AI directly into cells - no sidebar needed.
 * 
 * Usage:
 *   =CLEARLEDGR.MATCH(A2, Bank!A:E)
 *   =CLEARLEDGR.CATEGORIZE(B2, "vendor")
 *   =CLEARLEDGR.CONFIDENCE(A2, B2)
 *   =CLEARLEDGR.EXPLAIN(A2)
 */

// =============================================================================
// MATCHING FUNCTIONS
// =============================================================================

/**
 * Find the best matching transaction from another range.
 * 
 * @param {string|number} transaction - The transaction to match (amount, description, or reference)
 * @param {range} searchRange - The range to search in
 * @param {string} matchType - Type of match: "amount", "description", "reference", "auto" (default)
 * @param {number} threshold - Minimum confidence threshold (0-1, default 0.7)
 * @return {string} The matched transaction reference or "NO MATCH"
 * @customfunction
 */
function CLEARLEDGR_MATCH(transaction, searchRange, matchType, threshold) {
  matchType = matchType || "auto";
  threshold = threshold || 0.7;
  
  if (!transaction || !searchRange) return "NO MATCH";
  
  var bestMatch = null;
  var bestScore = 0;
  
  // Determine what we're matching
  var isAmount = !isNaN(parseFloat(transaction));
  var txValue = isAmount ? parseFloat(transaction) : String(transaction).toLowerCase();
  
  // Search through range
  for (var i = 0; i < searchRange.length; i++) {
    var row = searchRange[i];
    var score = 0;
    var matchRef = row[0]; // First column is reference
    
    for (var j = 0; j < row.length; j++) {
      var cell = row[j];
      if (!cell) continue;
      
      if (matchType === "amount" || matchType === "auto") {
        // Amount matching with tolerance
        if (!isNaN(parseFloat(cell))) {
          var cellAmount = parseFloat(cell);
          var amountScore = calculateAmountScore(txValue, cellAmount);
          if (amountScore > score) score = amountScore;
        }
      }
      
      if (matchType === "description" || matchType === "auto") {
        // Fuzzy text matching
        if (typeof cell === "string") {
          var textScore = calculateTextScore(String(transaction), cell);
          if (textScore > score) score = textScore;
        }
      }
      
      if (matchType === "reference" || matchType === "auto") {
        // Exact reference matching
        if (String(cell).toLowerCase() === String(transaction).toLowerCase()) {
          score = 1.0;
        }
      }
    }
    
    if (score > bestScore) {
      bestScore = score;
      bestMatch = matchRef || row[0] || "Row " + (i + 1);
    }
  }
  
  if (bestScore >= threshold) {
    return bestMatch + " (" + Math.round(bestScore * 100) + "%)";
  }
  
  return "NO MATCH";
}

/**
 * Calculate match confidence between two transactions.
 * 
 * @param {range} transaction1 - First transaction (row with amount, date, description)
 * @param {range} transaction2 - Second transaction to compare
 * @return {number} Confidence score 0-100
 * @customfunction
 */
function CLEARLEDGR_CONFIDENCE(transaction1, transaction2) {
  if (!transaction1 || !transaction2) return 0;
  
  var score = 0;
  var weights = { amount: 40, date: 30, description: 20, reference: 10 };
  
  // Flatten if ranges
  var t1 = Array.isArray(transaction1[0]) ? transaction1[0] : transaction1;
  var t2 = Array.isArray(transaction2[0]) ? transaction2[0] : transaction2;
  
  // Amount matching (40 points)
  var amt1 = findAmount(t1);
  var amt2 = findAmount(t2);
  if (amt1 && amt2) {
    score += calculateAmountScore(amt1, amt2) * weights.amount;
  }
  
  // Date matching (30 points)
  var date1 = findDate(t1);
  var date2 = findDate(t2);
  if (date1 && date2) {
    score += calculateDateScore(date1, date2) * weights.date;
  }
  
  // Description matching (20 points)
  var desc1 = findDescription(t1);
  var desc2 = findDescription(t2);
  if (desc1 && desc2) {
    score += calculateTextScore(desc1, desc2) * weights.description;
  }
  
  // Reference matching (10 points)
  var ref1 = findReference(t1);
  var ref2 = findReference(t2);
  if (ref1 && ref2 && ref1.toLowerCase() === ref2.toLowerCase()) {
    score += weights.reference;
  }
  
  return Math.round(score);
}

// =============================================================================
// CATEGORIZATION FUNCTIONS
// =============================================================================

/**
 * Automatically categorize a transaction to a GL account.
 * 
 * @param {string} description - Transaction description or vendor name
 * @param {string} type - Category type: "expense", "revenue", "asset", "auto"
 * @return {string} Suggested GL account code and name
 * @customfunction
 */
function CLEARLEDGR_CATEGORIZE(description, type) {
  if (!description) return "";
  type = type || "auto";
  
  var desc = String(description).toLowerCase();
  
  // Expense categories
  var expenseRules = [
    { keywords: ["software", "subscription", "saas", "cloud", "aws", "azure", "google cloud"], code: "6000", name: "Software & Cloud Services" },
    { keywords: ["consulting", "legal", "accounting", "advisory", "professional"], code: "6100", name: "Professional Services" },
    { keywords: ["marketing", "advertising", "ads", "google ads", "facebook", "linkedin"], code: "6200", name: "Marketing & Advertising" },
    { keywords: ["office", "supplies", "equipment", "furniture"], code: "6300", name: "Office & Supplies" },
    { keywords: ["travel", "flight", "hotel", "uber", "lyft", "taxi"], code: "6400", name: "Travel & Transportation" },
    { keywords: ["meal", "food", "restaurant", "catering", "lunch", "dinner"], code: "6410", name: "Meals & Entertainment" },
    { keywords: ["utility", "electric", "water", "gas", "internet", "phone"], code: "6500", name: "Utilities & Telecom" },
    { keywords: ["insurance", "policy", "premium"], code: "6600", name: "Insurance" },
    { keywords: ["rent", "lease", "office space"], code: "6700", name: "Rent & Facilities" },
    { keywords: ["payroll", "salary", "wage", "bonus", "commission"], code: "6800", name: "Payroll & Compensation" },
    { keywords: ["bank", "fee", "charge", "processing", "stripe", "paypal"], code: "6900", name: "Bank & Processing Fees" },
  ];
  
  // Revenue categories
  var revenueRules = [
    { keywords: ["subscription", "recurring", "mrr", "arr"], code: "4100", name: "Subscription Revenue" },
    { keywords: ["service", "consulting", "professional"], code: "4200", name: "Service Revenue" },
    { keywords: ["product", "sale", "merchandise"], code: "4300", name: "Product Sales" },
    { keywords: ["interest", "dividend", "investment"], code: "4900", name: "Other Income" },
  ];
  
  var rules = type === "revenue" ? revenueRules : 
              type === "expense" ? expenseRules : 
              expenseRules.concat(revenueRules);
  
  for (var i = 0; i < rules.length; i++) {
    var rule = rules[i];
    for (var j = 0; j < rule.keywords.length; j++) {
      if (desc.indexOf(rule.keywords[j]) !== -1) {
        return rule.code + " - " + rule.name;
      }
    }
  }
  
  return type === "revenue" ? "4900 - Other Income" : "6990 - Other Expenses";
}

/**
 * Extract vendor name from transaction description.
 * 
 * @param {string} description - Raw transaction description
 * @return {string} Cleaned vendor name
 * @customfunction
 */
function CLEARLEDGR_VENDOR(description) {
  if (!description) return "";
  
  var desc = String(description);
  
  // Known vendor patterns
  var vendors = {
    "STRIPE": "Stripe",
    "PAYPAL": "PayPal",
    "AMZN": "Amazon",
    "AMAZON": "Amazon",
    "AWS": "Amazon Web Services",
    "GOOGLE": "Google",
    "MSFT": "Microsoft",
    "APPLE": "Apple",
    "UBER": "Uber",
    "LYFT": "Lyft",
    "ZOOM": "Zoom",
    "SLACK": "Slack",
    "DROPBOX": "Dropbox",
    "GITHUB": "GitHub",
    "DIGITAL OCEAN": "DigitalOcean",
    "HEROKU": "Heroku",
    "WISE": "Wise",
    "MERCURY": "Mercury",
    "BREX": "Brex",
    "RAMP": "Ramp",
  };
  
  var upper = desc.toUpperCase();
  for (var pattern in vendors) {
    if (upper.indexOf(pattern) !== -1) {
      return vendors[pattern];
    }
  }
  
  // Try to extract from common patterns
  // "Payment to VENDOR NAME" or "VENDOR NAME - Invoice"
  var paymentMatch = desc.match(/(?:payment to|from|payee:?)\s*([A-Za-z0-9\s&]+)/i);
  if (paymentMatch) return cleanVendorName(paymentMatch[1]);
  
  var invoiceMatch = desc.match(/^([A-Za-z0-9\s&]+?)(?:\s*[-|]\s*|invoice|payment|charge)/i);
  if (invoiceMatch) return cleanVendorName(invoiceMatch[1]);
  
  // Return first few meaningful words
  var words = desc.split(/\s+/).slice(0, 3).join(" ");
  return cleanVendorName(words);
}

function cleanVendorName(name) {
  return name
    .replace(/[^A-Za-z0-9\s&]/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .split(" ")
    .map(function(w) { return w.charAt(0).toUpperCase() + w.slice(1).toLowerCase(); })
    .join(" ");
}

// =============================================================================
// ANALYSIS FUNCTIONS
// =============================================================================

/**
 * Explain why two transactions match or don't match.
 * 
 * @param {range} transaction1 - First transaction
 * @param {range} transaction2 - Second transaction
 * @return {string} Human-readable explanation
 * @customfunction
 */
function CLEARLEDGR_EXPLAIN(transaction1, transaction2) {
  if (!transaction1 || !transaction2) return "Missing transaction data";
  
  var t1 = Array.isArray(transaction1[0]) ? transaction1[0] : transaction1;
  var t2 = Array.isArray(transaction2[0]) ? transaction2[0] : transaction2;
  
  var explanations = [];
  var confidence = 0;
  
  // Amount analysis
  var amt1 = findAmount(t1);
  var amt2 = findAmount(t2);
  if (amt1 && amt2) {
    var amtDiff = Math.abs(amt1 - amt2);
    var amtPct = amt1 > 0 ? (amtDiff / amt1 * 100) : 0;
    
    if (amtDiff === 0) {
      explanations.push("Amounts match exactly");
      confidence += 40;
    } else if (amtPct <= 3) {
      explanations.push("Amounts within 3% (likely fee: " + formatCurrency(amtDiff) + ")");
      confidence += 35;
    } else if (amtPct <= 10) {
      explanations.push("Amount variance of " + amtPct.toFixed(1) + "% (" + formatCurrency(amtDiff) + ")");
      confidence += 20;
    } else {
      explanations.push("Significant amount difference: " + formatCurrency(amtDiff));
      confidence += 5;
    }
  }
  
  // Date analysis
  var date1 = findDate(t1);
  var date2 = findDate(t2);
  if (date1 && date2) {
    var daysDiff = Math.abs((date1 - date2) / (1000 * 60 * 60 * 24));
    
    if (daysDiff === 0) {
      explanations.push("Same date");
      confidence += 30;
    } else if (daysDiff <= 3) {
      explanations.push("Dates " + Math.round(daysDiff) + " day(s) apart (normal settlement)");
      confidence += 25;
    } else if (daysDiff <= 7) {
      explanations.push("Dates " + Math.round(daysDiff) + " days apart");
      confidence += 15;
    } else {
      explanations.push("Dates " + Math.round(daysDiff) + " days apart (unusual)");
      confidence += 5;
    }
  }
  
  // Description analysis
  var desc1 = findDescription(t1);
  var desc2 = findDescription(t2);
  if (desc1 && desc2) {
    var textScore = calculateTextScore(desc1, desc2);
    if (textScore > 0.8) {
      explanations.push("Descriptions match closely");
      confidence += 20;
    } else if (textScore > 0.5) {
      explanations.push("Descriptions partially match");
      confidence += 10;
    } else {
      explanations.push("Descriptions differ");
    }
  }
  
  var verdict = confidence >= 70 ? "MATCH" : confidence >= 50 ? "POSSIBLE MATCH" : "NO MATCH";
  
  return verdict + " (" + confidence + "%): " + explanations.join(". ");
}

/**
 * Flag potential issues with a transaction.
 * 
 * @param {range} transaction - Transaction row to analyze
 * @param {range} historicalData - Historical transactions for comparison
 * @return {string} Warning flags or "OK"
 * @customfunction
 */
function CLEARLEDGR_FLAG(transaction, historicalData) {
  if (!transaction) return "";
  
  var t = Array.isArray(transaction[0]) ? transaction[0] : transaction;
  var flags = [];
  
  var amount = findAmount(t);
  var vendor = CLEARLEDGR_VENDOR(findDescription(t) || "");
  
  // Large amount flag
  if (amount && amount > 10000) {
    flags.push("LARGE AMOUNT");
  }
  
  // Round number flag (often manual entries)
  if (amount && amount % 1000 === 0 && amount > 1000) {
    flags.push("ROUND NUMBER");
  }
  
  // Weekend transaction flag
  var date = findDate(t);
  if (date) {
    var day = date.getDay();
    if (day === 0 || day === 6) {
      flags.push("WEEKEND");
    }
  }
  
  // Duplicate check against historical data
  if (historicalData && amount) {
    for (var i = 0; i < historicalData.length; i++) {
      var histAmt = findAmount(historicalData[i]);
      var histDate = findDate(historicalData[i]);
      
      if (histAmt === amount && histDate && date) {
        var daysDiff = Math.abs((date - histDate) / (1000 * 60 * 60 * 24));
        if (daysDiff <= 1) {
          flags.push("POSSIBLE DUPLICATE");
          break;
        }
      }
    }
  }
  
  // New vendor flag
  if (vendor && historicalData) {
    var vendorSeen = false;
    for (var i = 0; i < historicalData.length; i++) {
      var histVendor = CLEARLEDGR_VENDOR(findDescription(historicalData[i]) || "");
      if (histVendor.toLowerCase() === vendor.toLowerCase()) {
        vendorSeen = true;
        break;
      }
    }
    if (!vendorSeen) {
      flags.push("NEW VENDOR");
    }
  }
  
  return flags.length > 0 ? flags.join(", ") : "OK";
}

/**
 * Detect if a transaction is likely a fee or processing charge.
 * 
 * @param {range} transaction - Transaction to analyze
 * @param {range} relatedTransaction - Optional related transaction (e.g., the payout this fee is from)
 * @return {string} "FEE: [type]" or "NOT A FEE"
 * @customfunction
 */
function CLEARLEDGR_DETECT_FEE(transaction, relatedTransaction) {
  if (!transaction) return "";
  
  var t = Array.isArray(transaction[0]) ? transaction[0] : transaction;
  var amount = findAmount(t);
  var desc = findDescription(t) || "";
  
  // Check description for fee keywords
  var feeKeywords = ["fee", "charge", "commission", "processing", "service charge", "bank charge"];
  var descLower = desc.toLowerCase();
  
  for (var i = 0; i < feeKeywords.length; i++) {
    if (descLower.indexOf(feeKeywords[i]) !== -1) {
      return "FEE: " + feeKeywords[i].charAt(0).toUpperCase() + feeKeywords[i].slice(1);
    }
  }
  
  // Check if amount looks like a percentage fee
  if (relatedTransaction && amount) {
    var rt = Array.isArray(relatedTransaction[0]) ? relatedTransaction[0] : relatedTransaction;
    var relatedAmount = findAmount(rt);
    
    if (relatedAmount && relatedAmount > 0) {
      var feePercent = (amount / relatedAmount) * 100;
      
      // Common fee percentages: 2.9% (Stripe), 2.6% (PayPal), 1.5% (ACH)
      if (feePercent >= 1 && feePercent <= 5) {
        return "FEE: Processing (" + feePercent.toFixed(2) + "%)";
      }
    }
  }
  
  // Small amounts that look like fees
  if (amount && amount > 0 && amount < 100) {
    // Fixed fees: $0.30 Stripe, $0.25 PayPal
    if (amount === 0.30 || amount === 0.25 || amount === 0.29) {
      return "FEE: Fixed transaction fee";
    }
  }
  
  return "NOT A FEE";
}

// =============================================================================
// HELPER FUNCTIONS
// =============================================================================

function calculateAmountScore(amt1, amt2) {
  if (!amt1 || !amt2) return 0;
  var diff = Math.abs(amt1 - amt2);
  var maxAmt = Math.max(Math.abs(amt1), Math.abs(amt2));
  if (maxAmt === 0) return amt1 === amt2 ? 1 : 0;
  
  var pctDiff = diff / maxAmt;
  
  if (pctDiff === 0) return 1.0;
  if (pctDiff <= 0.01) return 0.95;  // 1% tolerance
  if (pctDiff <= 0.03) return 0.85;  // 3% (typical fee)
  if (pctDiff <= 0.05) return 0.70;  // 5%
  if (pctDiff <= 0.10) return 0.50;  // 10%
  return 0.2;
}

function calculateDateScore(date1, date2) {
  if (!date1 || !date2) return 0;
  var daysDiff = Math.abs((date1 - date2) / (1000 * 60 * 60 * 24));
  
  if (daysDiff === 0) return 1.0;
  if (daysDiff <= 1) return 0.95;
  if (daysDiff <= 3) return 0.80;  // Normal settlement
  if (daysDiff <= 7) return 0.50;
  if (daysDiff <= 14) return 0.30;
  return 0.1;
}

function calculateTextScore(text1, text2) {
  if (!text1 || !text2) return 0;
  
  var s1 = String(text1).toLowerCase().replace(/[^a-z0-9]/g, " ").trim();
  var s2 = String(text2).toLowerCase().replace(/[^a-z0-9]/g, " ").trim();
  
  if (s1 === s2) return 1.0;
  
  // Word overlap
  var words1 = s1.split(/\s+/).filter(function(w) { return w.length > 2; });
  var words2 = s2.split(/\s+/).filter(function(w) { return w.length > 2; });
  
  if (words1.length === 0 || words2.length === 0) return 0;
  
  var overlap = 0;
  for (var i = 0; i < words1.length; i++) {
    if (words2.indexOf(words1[i]) !== -1) overlap++;
  }
  
  return overlap / Math.max(words1.length, words2.length);
}

function findAmount(row) {
  if (!row) return null;
  for (var i = 0; i < row.length; i++) {
    var val = row[i];
    if (typeof val === "number" && !isNaN(val) && val !== 0) {
      return val;
    }
    if (typeof val === "string") {
      var parsed = parseFloat(val.replace(/[^0-9.-]/g, ""));
      if (!isNaN(parsed) && parsed !== 0) return parsed;
    }
  }
  return null;
}

function findDate(row) {
  if (!row) return null;
  for (var i = 0; i < row.length; i++) {
    var val = row[i];
    if (val instanceof Date) return val;
    if (typeof val === "string") {
      var parsed = new Date(val);
      if (!isNaN(parsed.getTime())) return parsed;
    }
  }
  return null;
}

function findDescription(row) {
  if (!row) return null;
  for (var i = 0; i < row.length; i++) {
    var val = row[i];
    if (typeof val === "string" && val.length > 10 && isNaN(parseFloat(val))) {
      return val;
    }
  }
  return null;
}

function findReference(row) {
  if (!row) return null;
  for (var i = 0; i < row.length; i++) {
    var val = row[i];
    if (typeof val === "string" && /^[A-Z0-9_-]{5,}$/i.test(val.trim())) {
      return val.trim();
    }
  }
  return null;
}

function formatCurrency(amount) {
  if (amount == null || isNaN(amount)) return "€0.00";
  return "€" + Math.abs(amount).toLocaleString("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
