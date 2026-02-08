/**
 * Clearledgr - Google Sheets Add-on (Thin Client)
 * 
 * This add-on connects to the Clearledgr backend.
 * All intelligence lives in the backend. Sheets just displays data.
 * 
 * Architecture:
 *   Gmail Extension → Backend ← Sheets Add-on
 *                        ↑
 *                   Slack App
 * 
 * All surfaces connect to the same backend = same data everywhere.
 */

// ==================== CONFIGURATION ====================

function getApiUrl() {
  const props = PropertiesService.getUserProperties();
  return props.getProperty('CLEARLEDGR_API_URL') || 'http://localhost:8000';
}

function setApiUrl(url) {
  PropertiesService.getUserProperties().setProperty('CLEARLEDGR_API_URL', url);
}

function getOrgId() {
  const props = PropertiesService.getUserProperties();
  return props.getProperty('CLEARLEDGR_ORG_ID') || 'default';
}

function setOrgId(orgId) {
  PropertiesService.getUserProperties().setProperty('CLEARLEDGR_ORG_ID', orgId);
}

function getUserEmail() {
  return Session.getActiveUser().getEmail();
}

// ==================== API CLIENT ====================

/**
 * Call the Clearledgr backend API.
 */
function api(endpoint, options) {
  options = options || {};
  const baseUrl = getApiUrl().replace(/\/+$/, '');
  const url = baseUrl + endpoint;
  
  const fetchOptions = {
    method: options.method || 'GET',
    headers: {
      'Content-Type': 'application/json',
      'X-Organization-ID': getOrgId(),
      'X-User-Email': getUserEmail(),
    },
    muteHttpExceptions: true,
  };
  
  if (options.body) {
    fetchOptions.payload = JSON.stringify(options.body);
  }
  
  try {
    const response = UrlFetchApp.fetch(url, fetchOptions);
    const code = response.getResponseCode();
    
    if (code >= 200 && code < 300) {
      return JSON.parse(response.getContentText());
    } else {
      console.error('API error:', code, response.getContentText());
      return null;
    }
  } catch (error) {
    console.error('API fetch error:', error);
    return null;
  }
}

// ==================== MENU & INITIALIZATION ====================

function onOpen(e) {
  const ui = SpreadsheetApp.getUi();
  
  ui.createMenu('Clearledgr')
    .addItem('Dashboard', 'showDashboard')
    .addItem('Run Reconciliation', 'runReconciliation')
    .addSeparator()
    .addItem('View Finance Emails', 'showFinanceEmails')
    .addItem('View Exceptions', 'showExceptions')
    .addItem('View Draft Entries', 'showDraftEntries')
    .addSeparator()
    .addItem('Ask Vita AI', 'showVitaChat')
    .addSeparator()
    .addItem('Settings', 'showSettings')
    .addItem('Refresh Data', 'refreshAllData')
    .addToUi();
  
  // Ensure sheets exist
  ensureSheets();
}

function onInstall(e) {
  onOpen(e);
}

// ==================== SHEET MANAGEMENT ====================

const SHEETS = {
  DASHBOARD: 'Dashboard',
  EMAILS: 'Finance Emails',
  EXCEPTIONS: 'Exceptions',
  DRAFTS: 'Draft Entries',
  TRANSACTIONS: 'Transactions',
  MATCHES: 'Matches',
};

const HEADERS = {
  DASHBOARD: ['Metric', 'Value', 'Updated'],
  EMAILS: ['ID', 'Subject', 'Sender', 'Type', 'Confidence', 'Status', 'Received'],
  EXCEPTIONS: ['ID', 'Type', 'Amount', 'Currency', 'Vendor', 'Priority', 'Status', 'Assigned To'],
  DRAFTS: ['ID', 'Description', 'Amount', 'Currency', 'Confidence', 'Status', 'Approved By'],
  TRANSACTIONS: ['ID', 'Amount', 'Currency', 'Date', 'Description', 'Source', 'Status'],
  MATCHES: ['ID', 'Gateway ID', 'Bank ID', 'Score', 'Confidence', 'Created'],
};

function ensureSheets() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  
  Object.keys(SHEETS).forEach(function(key) {
    const name = SHEETS[key];
    let sheet = ss.getSheetByName(name);
    
    if (!sheet) {
      sheet = ss.insertSheet(name);
      const headers = HEADERS[key];
      if (headers) {
        sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
        sheet.getRange(1, 1, 1, headers.length).setFontWeight('bold');
        sheet.setFrozenRows(1);
      }
    }
  });
}

// ==================== DATA DISPLAY ====================

/**
 * Refresh all data from backend and display in sheets.
 */
function refreshAllData() {
  const dashboard = api('/engine/dashboard?organization_id=' + getOrgId());
  
  if (!dashboard) {
    SpreadsheetApp.getUi().alert('Failed to connect to Clearledgr backend. Check settings.');
    return;
  }
  
  updateDashboardSheet(dashboard);
  updateEmailsSheet();
  updateExceptionsSheet();
  updateDraftsSheet();
  
  SpreadsheetApp.getActive().toast('Data refreshed from Clearledgr', 'Success', 3);
}

function updateDashboardSheet(dashboard) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheetByName(SHEETS.DASHBOARD);
  if (!sheet) return;
  
  const stats = dashboard.stats || {};
  const now = new Date().toISOString();
  
  const data = [
    ['Finance Emails', stats.email_count || 0, now],
    ['Pending Transactions', stats.pending_transactions || 0, now],
    ['Matched Transactions', stats.matched_transactions || 0, now],
    ['Open Exceptions', stats.open_exceptions || 0, now],
    ['Pending Drafts', stats.pending_drafts || 0, now],
    ['Match Rate', (stats.match_rate || 0) + '%', now],
  ];
  
  sheet.getRange(2, 1, data.length, 3).setValues(data);
}

function updateEmailsSheet() {
  const result = api('/engine/emails?organization_id=' + getOrgId() + '&limit=100');
  if (!result || !result.emails) return;
  
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheetByName(SHEETS.EMAILS);
  if (!sheet) return;
  
  // Clear existing data (keep headers)
  const lastRow = sheet.getLastRow();
  if (lastRow > 1) {
    sheet.getRange(2, 1, lastRow - 1, 7).clearContent();
  }
  
  const emails = result.emails;
  if (emails.length === 0) return;
  
  const data = emails.map(function(e) {
    return [
      e.id || e.gmail_id,
      e.subject,
      e.sender,
      e.email_type,
      Math.round((e.confidence || 0) * 100) + '%',
      e.status,
      e.received_at,
    ];
  });
  
  sheet.getRange(2, 1, data.length, 7).setValues(data);
}

function updateExceptionsSheet() {
  const result = api('/engine/exceptions?organization_id=' + getOrgId() + '&status=open&limit=100');
  if (!result || !result.exceptions) return;
  
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheetByName(SHEETS.EXCEPTIONS);
  if (!sheet) return;
  
  // Clear existing data
  const lastRow = sheet.getLastRow();
  if (lastRow > 1) {
    sheet.getRange(2, 1, lastRow - 1, 8).clearContent();
  }
  
  const exceptions = result.exceptions;
  if (exceptions.length === 0) return;
  
  const data = exceptions.map(function(exc) {
    return [
      exc.id,
      exc.type,
      exc.amount,
      exc.currency,
      exc.vendor,
      exc.priority,
      exc.status,
      exc.assigned_to,
    ];
  });
  
  sheet.getRange(2, 1, data.length, 8).setValues(data);
}

function updateDraftsSheet() {
  const result = api('/engine/drafts?organization_id=' + getOrgId() + '&status=pending&limit=100');
  if (!result || !result.drafts) return;
  
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheetByName(SHEETS.DRAFTS);
  if (!sheet) return;
  
  // Clear existing data
  const lastRow = sheet.getLastRow();
  if (lastRow > 1) {
    sheet.getRange(2, 1, lastRow - 1, 7).clearContent();
  }
  
  const drafts = result.drafts;
  if (drafts.length === 0) return;
  
  const data = drafts.map(function(d) {
    return [
      d.id,
      d.description,
      d.amount,
      d.currency,
      Math.round((d.confidence || 0) * 100) + '%',
      d.status,
      d.approved_by || '',
    ];
  });
  
  sheet.getRange(2, 1, data.length, 7).setValues(data);
}

// ==================== RECONCILIATION ====================

/**
 * Run reconciliation on the current sheet data.
 */
function runReconciliation() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const ui = SpreadsheetApp.getUi();
  
  // Get gateway transactions from sheet
  const gatewaySheet = ss.getSheetByName('Gateway_Transactions') || ss.getSheetByName('Gateway');
  const bankSheet = ss.getSheetByName('Bank_Transactions') || ss.getSheetByName('Bank');
  
  if (!gatewaySheet || !bankSheet) {
    ui.alert('Please create sheets named "Gateway_Transactions" and "Bank_Transactions" with your transaction data.');
    return;
  }
  
  const gatewayData = getTransactionsFromSheet(gatewaySheet);
  const bankData = getTransactionsFromSheet(bankSheet);
  
  if (gatewayData.length === 0 || bankData.length === 0) {
    ui.alert('No transaction data found. Please add transactions to the Gateway and Bank sheets.');
    return;
  }
  
  // Call backend to run reconciliation
  const result = api('/engine/reconcile', {
    method: 'POST',
    body: {
      organization_id: getOrgId(),
      gateway_transactions: gatewayData,
      bank_transactions: bankData,
    },
  });
  
  if (!result) {
    ui.alert('Reconciliation failed. Check backend connection.');
    return;
  }
  
  // Refresh data to show results
  refreshAllData();
  
  const msg = 'Reconciliation complete!\n\n' +
    'Matches: ' + (result.result?.matches || 0) + '\n' +
    'Exceptions: ' + (result.result?.exceptions || 0) + '\n' +
    'Match Rate: ' + Math.round(result.result?.match_rate || 0) + '%';
  
  ui.alert('Reconciliation Complete', msg, ui.ButtonSet.OK);
}

function getTransactionsFromSheet(sheet) {
  const data = sheet.getDataRange().getValues();
  if (data.length < 2) return [];
  
  const headers = data[0].map(function(h) { return String(h).toLowerCase().trim(); });
  const rows = data.slice(1);
  
  // Find column indices
  const amountIdx = headers.indexOf('amount');
  const dateIdx = headers.indexOf('date');
  const descIdx = headers.indexOf('description') !== -1 ? headers.indexOf('description') : headers.indexOf('desc');
  const refIdx = headers.indexOf('reference') !== -1 ? headers.indexOf('reference') : headers.indexOf('ref');
  
  if (amountIdx === -1 || dateIdx === -1) {
    return [];
  }
  
  return rows.filter(function(row) {
    return row[amountIdx] !== '' && row[amountIdx] !== null;
  }).map(function(row) {
    return {
      amount: parseFloat(row[amountIdx]) || 0,
      date: row[dateIdx] ? new Date(row[dateIdx]).toISOString().split('T')[0] : '',
      description: row[descIdx] || '',
      reference: row[refIdx] || '',
      currency: 'EUR',
    };
  });
}

// ==================== ACTIONS ====================

/**
 * Approve selected draft entries.
 */
function approveDrafts() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheetByName(SHEETS.DRAFTS);
  if (!sheet) return;
  
  const selection = sheet.getActiveRange();
  if (!selection) {
    SpreadsheetApp.getUi().alert('Please select rows to approve.');
    return;
  }
  
  const startRow = selection.getRow();
  const numRows = selection.getNumRows();
  
  let approved = 0;
  for (let i = 0; i < numRows; i++) {
    const row = startRow + i;
    if (row === 1) continue; // Skip header
    
    const draftId = sheet.getRange(row, 1).getValue();
    if (!draftId) continue;
    
    const result = api('/engine/drafts/approve', {
      method: 'POST',
      body: {
        draft_id: String(draftId),
        organization_id: getOrgId(),
        user_id: getUserEmail(),
      },
    });
    
    if (result && result.status === 'success') {
      approved++;
    }
  }
  
  refreshAllData();
  SpreadsheetApp.getActive().toast('Approved ' + approved + ' entries', 'Done', 3);
}

/**
 * Resolve selected exceptions.
 */
function resolveExceptions() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheetByName(SHEETS.EXCEPTIONS);
  if (!sheet) return;
  
  const selection = sheet.getActiveRange();
  if (!selection) {
    SpreadsheetApp.getUi().alert('Please select rows to resolve.');
    return;
  }
  
  const startRow = selection.getRow();
  const numRows = selection.getNumRows();
  
  let resolved = 0;
  for (let i = 0; i < numRows; i++) {
    const row = startRow + i;
    if (row === 1) continue;
    
    const exceptionId = sheet.getRange(row, 1).getValue();
    if (!exceptionId) continue;
    
    const result = api('/engine/exceptions/resolve', {
      method: 'POST',
      body: {
        exception_id: String(exceptionId),
        organization_id: getOrgId(),
        user_id: getUserEmail(),
        resolution_notes: 'Resolved via Sheets',
      },
    });
    
    if (result && result.status === 'success') {
      resolved++;
    }
  }
  
  refreshAllData();
  SpreadsheetApp.getActive().toast('Resolved ' + resolved + ' exceptions', 'Done', 3);
}

/**
 * Process a finance email (send to reconciliation queue).
 */
function processFinanceEmail(emailId) {
  const result = api('/engine/emails/process', {
    method: 'POST',
    body: {
      email_id: String(emailId),
      organization_id: getOrgId(),
      user_id: getUserEmail(),
    },
  });
  
  if (result && result.status === 'success') {
    SpreadsheetApp.getActive().toast('Email processed', 'Done', 3);
    refreshAllData();
    return true;
  }
  return false;
}

// ==================== SIDEBARS ====================

function showDashboard() {
  const html = HtmlService.createHtmlOutputFromFile('dashboard')
    .setTitle('Clearledgr Dashboard')
    .setWidth(400);
  SpreadsheetApp.getUi().showSidebar(html);
}

function showFinanceEmails() {
  updateEmailsSheet();
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheetByName(SHEETS.EMAILS);
  if (sheet) {
    ss.setActiveSheet(sheet);
  }
}

function showExceptions() {
  updateExceptionsSheet();
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheetByName(SHEETS.EXCEPTIONS);
  if (sheet) {
    ss.setActiveSheet(sheet);
  }
}

function showDraftEntries() {
  updateDraftsSheet();
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheetByName(SHEETS.DRAFTS);
  if (sheet) {
    ss.setActiveSheet(sheet);
  }
}

function showVitaChat() {
  const html = HtmlService.createHtmlOutputFromFile('vita-chat')
    .setTitle('Vita AI')
    .setWidth(400);
  SpreadsheetApp.getUi().showSidebar(html);
}

function showSettings() {
  const html = HtmlService.createHtmlOutputFromFile('settings')
    .setTitle('Settings')
    .setWidth(400);
  SpreadsheetApp.getUi().showSidebar(html);
}

// ==================== VITA AI CHAT ====================

/**
 * Send a message to Vita AI.
 */
function sendVitaMessage(message) {
  const result = api('/chat/message', {
    method: 'POST',
    body: {
      text: message,
      user_id: getUserEmail(),
      channel: 'sheets',
      metadata: {
        organization_id: getOrgId(),
        spreadsheet_id: SpreadsheetApp.getActiveSpreadsheet().getId(),
      },
    },
  });
  
  if (result) {
    return {
      text: result.text || 'I could not process that request.',
      suggestions: result.suggestions || [],
    };
  }
  
  return {
    text: 'Failed to connect to Vita AI. Please check your settings.',
    suggestions: [],
  };
}

// ==================== SETTINGS HELPERS ====================

function getSettings() {
  return {
    apiUrl: getApiUrl(),
    orgId: getOrgId(),
    userEmail: getUserEmail(),
  };
}

function saveSettings(apiUrl, orgId) {
  if (apiUrl) setApiUrl(apiUrl);
  if (orgId) setOrgId(orgId);
  return { success: true };
}

// ==================== TRIGGERS ====================

/**
 * Set up time-based triggers for autonomous operation.
 */
function setupTriggers() {
  // Remove existing triggers
  const triggers = ScriptApp.getProjectTriggers();
  triggers.forEach(function(trigger) {
    if (trigger.getHandlerFunction() === 'scheduledReconciliation') {
      ScriptApp.deleteTrigger(trigger);
    }
  });
  
  // Create daily trigger at 9am
  ScriptApp.newTrigger('scheduledReconciliation')
    .timeBased()
    .everyDays(1)
    .atHour(9)
    .create();
  
  SpreadsheetApp.getActive().toast('Daily reconciliation scheduled for 9am', 'Triggers Set', 3);
}

function scheduledReconciliation() {
  // Run reconciliation automatically
  runReconciliation();
  
  // Send Slack notification
  notifySlack('Daily reconciliation completed');
}

function notifySlack(message) {
  const props = PropertiesService.getUserProperties();
  const webhookUrl = props.getProperty('SLACK_WEBHOOK_URL');
  
  if (!webhookUrl) return;
  
  try {
    UrlFetchApp.fetch(webhookUrl, {
      method: 'POST',
      payload: JSON.stringify({
        text: '[Clearledgr] ' + message,
      }),
    });
  } catch (e) {
    console.error('Slack notification failed:', e);
  }
}

// ==================== CUSTOM FUNCTIONS ====================

/**
 * Calculate match confidence between two transactions.
 * @param {Range} tx1 First transaction [amount, date, description, reference]
 * @param {Range} tx2 Second transaction [amount, date, description, reference]
 * @return {number} Confidence score (0-100)
 * @customfunction
 */
function CLEARLEDGR_CONFIDENCE(tx1, tx2) {
  if (!tx1 || !tx2) return 0;
  
  const a1 = Array.isArray(tx1[0]) ? tx1[0] : tx1;
  const a2 = Array.isArray(tx2[0]) ? tx2[0] : tx2;
  
  if (a1.length < 2 || a2.length < 2) return 0;
  
  let score = 0;
  
  // Amount match (40 points)
  const amt1 = parseFloat(a1[0]) || 0;
  const amt2 = parseFloat(a2[0]) || 0;
  if (amt1 === amt2) {
    score += 40;
  } else if (Math.abs(amt1 - amt2) / Math.max(amt1, amt2) <= 0.01) {
    score += 30;
  } else if (Math.abs(amt1 - amt2) / Math.max(amt1, amt2) <= 0.05) {
    score += 20;
  }
  
  // Date match (30 points)
  const date1 = new Date(a1[1]);
  const date2 = new Date(a2[1]);
  const daysDiff = Math.abs((date1 - date2) / (1000 * 60 * 60 * 24));
  if (daysDiff === 0) {
    score += 30;
  } else if (daysDiff <= 1) {
    score += 25;
  } else if (daysDiff <= 3) {
    score += 15;
  } else if (daysDiff <= 7) {
    score += 5;
  }
  
  // Description similarity (20 points)
  if (a1.length > 2 && a2.length > 2) {
    const desc1 = String(a1[2] || '').toLowerCase();
    const desc2 = String(a2[2] || '').toLowerCase();
    if (desc1 === desc2) {
      score += 20;
    } else if (desc1.includes(desc2) || desc2.includes(desc1)) {
      score += 15;
    } else {
      // Simple word overlap
      const words1 = desc1.split(/\s+/);
      const words2 = desc2.split(/\s+/);
      const overlap = words1.filter(function(w) { return words2.includes(w); }).length;
      const maxWords = Math.max(words1.length, words2.length);
      if (maxWords > 0) {
        score += Math.round((overlap / maxWords) * 20);
      }
    }
  }
  
  // Reference match (10 points)
  if (a1.length > 3 && a2.length > 3) {
    const ref1 = String(a1[3] || '').toLowerCase();
    const ref2 = String(a2[3] || '').toLowerCase();
    if (ref1 && ref2 && ref1 === ref2) {
      score += 10;
    } else if (ref1 && ref2 && (ref1.includes(ref2) || ref2.includes(ref1))) {
      score += 5;
    }
  }
  
  return Math.min(score, 100);
}

/**
 * Categorize a transaction description to a GL account.
 * @param {string} description Transaction description
 * @return {string} Suggested GL account code
 * @customfunction
 */
function CLEARLEDGR_CATEGORIZE(description) {
  if (!description) return '';
  
  const desc = String(description).toLowerCase();
  
  // Simple keyword-based categorization (backend would use AI)
  const categories = {
    'bank_fees': ['fee', 'charge', 'interest', 'service charge'],
    'revenue': ['payment', 'received', 'deposit', 'income', 'sale'],
    'refund': ['refund', 'return', 'credit', 'reversal'],
    'payroll': ['salary', 'wage', 'payroll', 'bonus'],
    'utilities': ['electric', 'water', 'gas', 'utility', 'internet'],
    'rent': ['rent', 'lease', 'property'],
    'supplies': ['office', 'supply', 'supplies', 'equipment'],
  };
  
  for (var category in categories) {
    var keywords = categories[category];
    for (var i = 0; i < keywords.length; i++) {
      if (desc.includes(keywords[i])) {
        return category.toUpperCase();
      }
    }
  }
  
  return 'UNCATEGORIZED';
}

/**
 * Extract vendor name from a transaction description.
 * @param {string} description Transaction description
 * @return {string} Extracted vendor name
 * @customfunction
 */
function CLEARLEDGR_VENDOR(description) {
  if (!description) return '';
  
  const desc = String(description);
  
  // Common patterns to remove
  const patterns = [
    /payment from /i,
    /payment to /i,
    /transfer from /i,
    /transfer to /i,
    /\d{4}[-\/]\d{2}[-\/]\d{2}/g, // dates
    /\$[\d,]+\.?\d*/g, // currency amounts
    /EUR[\d,]+\.?\d*/g,
    /ref[:#]?\s*\w+/gi, // reference numbers
    /id[:#]?\s*\w+/gi,
  ];
  
  var cleaned = desc;
  patterns.forEach(function(p) {
    cleaned = cleaned.replace(p, '');
  });
  
  // Clean up and return
  cleaned = cleaned.replace(/\s+/g, ' ').trim();
  
  // Capitalize first letter of each word
  return cleaned.split(' ').map(function(w) {
    return w.charAt(0).toUpperCase() + w.slice(1).toLowerCase();
  }).join(' ');
}
