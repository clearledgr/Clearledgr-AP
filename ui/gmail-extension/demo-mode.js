// Clearledgr Demo Mode
// Pre-populated sample data for demonstrations
// This file provides mock data and responses when DEMO_MODE is enabled

const DEMO_MODE = true; // Toggle this for demo vs real mode

// ==================== DEMO DATA ====================

const DEMO_EMAILS = [
  {
    id: "demo-inv-001",
    type: "Invoice",
    subject: "Invoice #INV-2024-0892 from AWS",
    sender: "billing@amazon.com",
    snippet: "Your January 2026 AWS invoice is ready. Total amount: $12,847.32",
    date: "Jan 15",
    confidence: 0.97,
    hasAttachment: true,
    status: "pending",
    extracted: {
      vendor: "Amazon Web Services",
      invoiceNumber: "INV-2024-0892",
      amount: 12847.32,
      currency: "USD",
      dueDate: "2026-02-15",
      lineItems: [
        { description: "EC2 Instances", amount: 8234.50 },
        { description: "S3 Storage", amount: 2156.82 },
        { description: "RDS Database", amount: 1892.00 },
        { description: "Data Transfer", amount: 564.00 }
      ],
      suggestedCategory: "Cloud Infrastructure",
      suggestedGLCode: "6200 - Technology Expenses",
      confidence: 0.96
    }
  },
  {
    id: "demo-inv-002",
    type: "Invoice",
    subject: "Stripe Invoice - January 2026",
    sender: "receipts@stripe.com",
    snippet: "Invoice for payment processing fees. Amount due: $3,421.89",
    date: "Jan 14",
    confidence: 0.98,
    hasAttachment: true,
    status: "pending",
    extracted: {
      vendor: "Stripe Inc.",
      invoiceNumber: "STRIPE-89234",
      amount: 3421.89,
      currency: "USD",
      dueDate: "2026-01-28",
      lineItems: [
        { description: "Payment Processing (2.9% + $0.30)", amount: 3156.42 },
        { description: "Radar Fraud Protection", amount: 189.50 },
        { description: "Connect Platform Fees", amount: 75.97 }
      ],
      suggestedCategory: "Payment Processing",
      suggestedGLCode: "6150 - Merchant Fees",
      confidence: 0.98
    }
  },
  {
    id: "demo-stmt-001",
    type: "Bank Statement",
    subject: "Your January Statement is Ready - Chase Business",
    sender: "alerts@chase.com",
    snippet: "Your business checking account statement for January 2026 is now available.",
    date: "Jan 13",
    confidence: 0.99,
    hasAttachment: true,
    status: "pending",
    attachmentType: "PDF",
    statementData: {
      bank: "Chase Business",
      accountEnding: "4892",
      period: "January 1-31, 2026",
      openingBalance: 284532.67,
      closingBalance: 312847.23,
      totalDeposits: 156789.45,
      totalWithdrawals: 128474.89,
      transactionCount: 47
    }
  },
  {
    id: "demo-inv-003",
    type: "Invoice",
    subject: "HubSpot Invoice - Marketing Hub Professional",
    sender: "billing@hubspot.com",
    snippet: "Your monthly subscription invoice for HubSpot Marketing Hub",
    date: "Jan 12",
    confidence: 0.95,
    hasAttachment: true,
    status: "processed",
    extracted: {
      vendor: "HubSpot Inc.",
      invoiceNumber: "HS-2026-01-4521",
      amount: 890.00,
      currency: "USD",
      dueDate: "2026-01-26",
      lineItems: [
        { description: "Marketing Hub Professional", amount: 800.00 },
        { description: "Additional Contacts (5,000)", amount: 90.00 }
      ],
      suggestedCategory: "Marketing Software",
      suggestedGLCode: "6300 - Marketing Expenses",
      confidence: 0.94
    }
  },
  {
    id: "demo-pay-001",
    type: "Payment Confirmation",
    subject: "Payment Received - Invoice #4521",
    sender: "ar@acmecorp.com",
    snippet: "We have received your payment of $45,000.00. Thank you for your business.",
    date: "Jan 11",
    confidence: 0.92,
    hasAttachment: false,
    status: "processed",
    extracted: {
      type: "Payment Receipt",
      amount: 45000.00,
      currency: "USD",
      reference: "CHK-89234",
      payer: "Acme Corporation",
      matchedInvoice: "INV-2025-0234"
    }
  },
  {
    id: "demo-inv-004",
    type: "Invoice",
    subject: "Gusto Payroll Invoice - Pay Period 01/01-01/15",
    sender: "invoices@gusto.com",
    snippet: "Payroll processing invoice for 23 employees",
    date: "Jan 10",
    confidence: 0.96,
    hasAttachment: true,
    status: "pending",
    extracted: {
      vendor: "Gusto",
      invoiceNumber: "GUSTO-2026-0115",
      amount: 892.50,
      currency: "USD",
      dueDate: "2026-01-17",
      lineItems: [
        { description: "Payroll Processing (23 employees)", amount: 690.00 },
        { description: "Benefits Administration", amount: 115.00 },
        { description: "Tax Filing Service", amount: 87.50 }
      ],
      suggestedCategory: "Payroll Services",
      suggestedGLCode: "6100 - Payroll Expenses",
      confidence: 0.97
    }
  }
];

const DEMO_DASHBOARD = {
  email_count: 6,
  matched_count: 2,
  exception_count: 1,
  pending_count: 4,
  total_amount_processed: 62052.21,
  recent_activity: [
    {
      description: "Invoice from HubSpot auto-categorized",
      timestamp: new Date(Date.now() - 3600000).toISOString(),
      type: "categorization"
    },
    {
      description: "Payment from Acme Corp matched to INV-2025-0234",
      timestamp: new Date(Date.now() - 7200000).toISOString(),
      type: "match"
    },
    {
      description: "AWS invoice extracted - $12,847.32",
      timestamp: new Date(Date.now() - 10800000).toISOString(),
      type: "extraction"
    },
    {
      description: "Bank statement detected from Chase",
      timestamp: new Date(Date.now() - 14400000).toISOString(),
      type: "detection"
    }
  ]
};

const DEMO_RECONCILIATION_RESULT = {
  status: "success",
  parsed_count: 47,
  matches: [
    { bankTxn: "Wire from Acme Corp", amount: 45000.00, matchedTo: "INV-2025-0234", confidence: 0.98 },
    { bankTxn: "Stripe Payout", amount: 28456.78, matchedTo: "Stripe Settlement 01/10", confidence: 0.99 },
    { bankTxn: "AWS Payment", amount: -11234.56, matchedTo: "INV-2024-0845", confidence: 0.97 },
    { bankTxn: "Gusto Payroll", amount: -89234.12, matchedTo: "Payroll 01/01-01/15", confidence: 0.99 },
    // ... more matches
  ],
  exceptions: [
    { 
      bankTxn: "Unknown Transfer", 
      amount: 5234.00, 
      reason: "No matching invoice found",
      suggestion: "Possible customer prepayment - review AR"
    }
  ],
  auto_matched: 44,
  needs_review: 3,
  match_rate: 93.6
};

const DEMO_VITA_RESPONSES = {
  "reconciliation status": {
    text: "Here's your reconciliation status:\n\n**44 transactions** auto-matched (93.6% match rate)\n**3 items** need your review\n\nThe exceptions are:\n1. Unknown transfer of $5,234 - looks like a customer prepayment\n2. Duplicate charge from AWS - I've flagged for dispute\n3. Missing invoice for Zoom payment\n\nWant me to create journal entries for the matched items?",
    suggestions: ["Create journal entries", "Show exceptions", "Export to Sheets"]
  },
  "pending invoices": {
    text: "You have **4 pending invoices** totaling **$18,051.71**:\n\n1. **AWS** - $12,847.32 (due Feb 15)\n2. **Stripe** - $3,421.89 (due Jan 28)\n3. **Gusto** - $892.50 (due Jan 17) Due soon.\n4. **HubSpot** - $890.00 (due Jan 26)\n\nThe Gusto invoice is due in 2 days. Should I schedule the payment?",
    suggestions: ["Schedule Gusto payment", "Categorize all", "Post to QuickBooks"]
  },
  "post to quickbooks": {
    text: "I'll post these invoices to QuickBooks:\n\nPosting 4 invoices...\n\nAWS Invoice → Accounts Payable (6200)\nStripe Invoice → Accounts Payable (6150)\nGusto Invoice → Accounts Payable (6100)\nHubSpot Invoice → Accounts Payable (6300)\n\nAll 4 invoices posted successfully.\n\nTotal: $18,051.71 added to Accounts Payable",
    suggestions: ["View in QuickBooks", "Generate AP report", "Schedule payments"]
  },
  "default": {
    text: "I can help you with:\n\n• **Reconciliation** - Match bank transactions to invoices\n• **Invoice processing** - Extract and categorize invoices\n• **Journal entries** - Create and post entries to your ERP\n• **Reports** - Generate financial summaries\n\nWhat would you like to do?",
    suggestions: ["Show pending invoices", "Reconciliation status", "Process bank statement"]
  }
};

const DEMO_ERP_POST_RESULT = {
  status: "success",
  erp: "quickbooks",
  entries_created: 4,
  total_amount: 18051.71,
  journal_ids: ["JE-2026-0089", "JE-2026-0090", "JE-2026-0091", "JE-2026-0092"],
  timestamp: new Date().toISOString()
};

// ==================== DEMO FUNCTIONS ====================

function getDemoEmails() {
  return DEMO_EMAILS;
}

function getDemoDashboard() {
  return DEMO_DASHBOARD;
}

function getDemoEmailById(id) {
  return DEMO_EMAILS.find(e => e.id === id);
}

function getDemoVitaResponse(message) {
  const lowerMessage = message.toLowerCase();
  
  if (lowerMessage.includes("reconcil") || lowerMessage.includes("match")) {
    return DEMO_VITA_RESPONSES["reconciliation status"];
  }
  if (lowerMessage.includes("pending") || lowerMessage.includes("invoice")) {
    return DEMO_VITA_RESPONSES["pending invoices"];
  }
  if (lowerMessage.includes("quickbooks") || lowerMessage.includes("post") || lowerMessage.includes("erp")) {
    return DEMO_VITA_RESPONSES["post to quickbooks"];
  }
  
  return DEMO_VITA_RESPONSES["default"];
}

function getDemoReconciliationResult() {
  return DEMO_RECONCILIATION_RESULT;
}

function getDemoERPPostResult() {
  return DEMO_ERP_POST_RESULT;
}

// Process email simulation with realistic delay
async function simulateProcessEmail(emailId) {
  const email = getDemoEmailById(emailId);
  if (!email) return { status: "error", message: "Email not found" };
  
  // Simulate processing time
  await new Promise(resolve => setTimeout(resolve, 1500));
  
  return {
    status: "success",
    emailId: emailId,
    extracted: email.extracted,
    action: "categorized",
    suggestedGLCode: email.extracted?.suggestedGLCode,
    confidence: email.extracted?.confidence || 0.95
  };
}

// Simulate bank statement processing
async function simulateProcessStatement() {
  // Simulate longer processing for statement
  await new Promise(resolve => setTimeout(resolve, 2500));
  
  return getDemoReconciliationResult();
}

// Simulate ERP posting
async function simulateERPPost(invoiceIds) {
  await new Promise(resolve => setTimeout(resolve, 2000));
  
  return getDemoERPPostResult();
}

// Export for use in other files
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    DEMO_MODE,
    DEMO_EMAILS,
    DEMO_DASHBOARD,
    getDemoEmails,
    getDemoDashboard,
    getDemoEmailById,
    getDemoVitaResponse,
    getDemoReconciliationResult,
    getDemoERPPostResult,
    simulateProcessEmail,
    simulateProcessStatement,
    simulateERPPost
  };
}

// Make available globally in browser
if (typeof window !== 'undefined') {
  window.ClearledgrDemo = {
    DEMO_MODE,
    DEMO_EMAILS,
    DEMO_DASHBOARD,
    getDemoEmails,
    getDemoDashboard,
    getDemoEmailById,
    getDemoVitaResponse,
    getDemoReconciliationResult,
    getDemoERPPostResult,
    simulateProcessEmail,
    simulateProcessStatement,
    simulateERPPost
  };
}
