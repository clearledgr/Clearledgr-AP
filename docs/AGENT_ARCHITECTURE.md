# Clearledgr Agent Architecture

> Documentation of the AI agent implementation and improvements.
> 
> **Last Updated:** January 2026

---

## Overview

Clearledgr's agent is designed to autonomously process financial documents (invoices, receipts, statements) with minimal human intervention while maintaining high accuracy and auditability.

### Design Principles

1. **Autonomous by Default** - Process without human intervention when confident
2. **Human-in-the-Loop** - Escalate intelligently when uncertain
3. **Learn from Corrections** - Improve over time from user feedback
4. **Explainable Decisions** - Always show reasoning, not just results
5. **Audit-First** - Every action traceable to source

---

## Architecture Layers

```
┌─────────────────────────────────────────────────────────────────┐
│                        PERCEPTION LAYER                         │
│  Gmail Extension │ Slack Bot │ Gmail Pub/Sub (24/7)            │
│  Detects financial documents in user's workflow                 │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                        REASONING LAYER                          │
│  LLM Service (Claude) │ Chain-of-Thought │ Cross-Doc Analysis  │
│  Understands context, extracts data, makes decisions            │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                        MEMORY LAYER                             │
│  Learning Service │ Vendor Patterns │ Historical Context        │
│  Remembers preferences, patterns, and corrections               │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                        ACTION LAYER                             │
│  ERP Router │ Slack Notifications │ Gmail Labels                │
│  Executes decisions in external systems                         │
└─────────────────────────────────────────────────────────────────┘
```

---

## Component Details

### 1. Perception Layer

**Purpose:** Detect and ingest financial documents from user's workflow.

| Component | File | Description |
|-----------|------|-------------|
| Gmail Extension | `ui/gmail-extension/` | Detects invoices in browser |
| Gmail Pub/Sub | `clearledgr/services/gmail_pubsub.py` | 24/7 autonomous detection |
| Slack Bot | `ui/slack/app.py` | Expense requests, approvals |

**Status:**
- Gmail Extension - Working
- Gmail Pub/Sub - Code exists, not deployed
- Slack Bot - Working

### 2. Reasoning Layer

**Purpose:** Understand documents and make intelligent decisions.

| Component | File | Description |
|-----------|------|-------------|
| LLM Service | `clearledgr/services/llm_multimodal.py` | Claude Vision for PDFs |
| Classification | `clearledgr/workflows/gmail_activities.py` | Document type detection |
| Extraction | `clearledgr/workflows/gmail_activities.py` | Data extraction |
| Chain-of-Thought | `clearledgr/services/agent_reasoning.py` | Explicit reasoning (NEW) |

**Current Reasoning Flow:**
```python
# Old: Direct extraction
result = llm.extract_invoice(text, attachments)

# New: Chain-of-thought reasoning
result = agent.reason_about_invoice(
    text=text,
    attachments=attachments,
    context={
        "vendor_history": learning.get_vendor_history(vendor),
        "recent_invoices": db.get_recent_invoices(vendor, days=30),
    }
)
# Returns: extraction + reasoning + confidence_factors + recommendations
```

### 3. Memory Layer

**Purpose:** Remember patterns, preferences, and context across time.

| Component | File | Description |
|-----------|------|-------------|
| Learning Service | `clearledgr/services/learning.py` | Vendor→GL mappings |
| Vendor Patterns | `clearledgr/services/recurring_detection.py` | Subscription detection |
| Historical Context | `clearledgr/core/database.py` | Invoice history |

**Memory Types:**
- **Short-term:** Current session context
- **Long-term:** Vendor patterns, GL mappings, approval history
- **Episodic:** Specific invoice decisions and corrections

### 4. Action Layer

**Purpose:** Execute decisions in external systems.

| Component | File | Description |
|-----------|------|-------------|
| ERP Router | `clearledgr/integrations/erp_router.py` | QuickBooks/Xero/NetSuite |
| Slack Actions | `clearledgr/services/slack_api.py` | Notifications, approvals |
| Gmail Labels | `ui/gmail-extension/background.js` | Status tracking |

---

## Decision Framework

### Confidence Scoring

```
CONFIDENCE = (
    extraction_confidence * 0.30 +    # How sure is LLM about data?
    vendor_familiarity * 0.25 +       # Have we seen this vendor?
    pattern_match * 0.20 +            # Does this match expected pattern?
    amount_reasonableness * 0.15 +    # Is amount within expected range?
    document_quality * 0.10           # Is PDF/image clear?
)
```

### Decision Thresholds

| Confidence | Action | Explanation Required |
|------------|--------|---------------------|
| ≥ 95% | Auto-approve & post | Yes - show why confident |
| 75-94% | Send for approval | Yes - show uncertainty factors |
| < 75% | Flag for review | Yes - explain what's unclear |

### Explanation Template

Every decision includes:

```json
{
  "decision": "auto_approved",
  "confidence": 0.96,
  "reasoning": {
    "summary": "Auto-approved: Known vendor with consistent pattern",
    "factors": [
      {"factor": "vendor_history", "score": 0.98, "detail": "14 previous invoices from Stripe"},
      {"factor": "amount_match", "score": 0.95, "detail": "Within 2% of typical $299"},
      {"factor": "timing", "score": 0.92, "detail": "Expected monthly invoice"}
    ],
    "risks": [],
    "alternatives_considered": ["manual_review"]
  }
}
```

---

## Improvement Roadmap

### Phase 1: Explanations (Current)
- [x] Add reasoning to extraction response
- [ ] Display reasoning in Slack messages
- [ ] Display reasoning in Gmail sidebar

### Phase 2: Chain-of-Thought
- [ ] Multi-step reasoning prompt
- [ ] Explicit confidence factors
- [ ] Self-verification step

### Phase 3: Cross-Invoice Reasoning
- [ ] Duplicate detection
- [ ] Anomaly detection (unusual amounts)
- [ ] Vendor pattern alerts

### Phase 4: Conversational Complete
- [x] Ask clarifying questions via Slack
- [x] Handle user responses
- [x] Update decision based on response

---

## File Reference

| File | Purpose | Layer |
|------|---------|-------|
| `clearledgr/services/agent_reasoning.py` | Chain-of-thought reasoning | Reasoning |
| `clearledgr/services/agent_reflection.py` | Self-checking/validation | Reasoning |
| `clearledgr/services/cross_invoice_analysis.py` | Duplicate/anomaly detection | Reasoning |
| `clearledgr/services/conversational_agent.py` | Clarifying questions | Reasoning |
| `clearledgr/services/natural_language_commands.py` | NLP command processing | Reasoning |
| `clearledgr/services/proactive_insights.py` | Spending insights/alerts | Reasoning |
| `clearledgr/services/cashflow_prediction.py` | AP forecasting | Reasoning |
| `clearledgr/services/llm_multimodal.py` | LLM calls, Vision | Reasoning |
| `clearledgr/services/vendor_intelligence.py` | Known vendor database | Reasoning |
| `clearledgr/services/policy_compliance.py` | Company policy checks | Reasoning |
| `clearledgr/services/priority_detection.py` | Urgency/priority scoring | Reasoning |
| `clearledgr/services/budget_awareness.py` | Budget tracking | Reasoning |
| `clearledgr/services/batch_intelligence.py` | Batch processing optimization | Reasoning |
| `clearledgr/services/learning.py` | Vendor→GL learning | Memory |
| `clearledgr/services/correction_learning.py` | Learn from user corrections | Memory |
| `clearledgr/services/recurring_detection.py` | Pattern detection | Memory |
| `clearledgr/services/audit_trail.py` | Complete decision history | Memory |
| `clearledgr/services/invoice_workflow.py` | Main orchestrator | Action |
| `clearledgr/integrations/erp_router.py` | ERP posting | Action |
| `ui/slack/app.py` | Slack interaction handlers | Action |

---

## Changelog

### 2026-01-23
- Created initial architecture documentation
- Defined four-layer architecture (Perception, Reasoning, Memory, Action)
- Documented decision framework and confidence scoring
- Outlined improvement roadmap
- Implemented `AgentReasoningService` with chain-of-thought reasoning
- Added `CrossInvoiceAnalyzer` for duplicate detection and anomaly alerts
- Updated Slack messages to show reasoning and risks
- Added reasoning fields to `InvoiceData` dataclass

### Files Changed (Phase 1-4)
- `clearledgr/services/agent_reasoning.py` - Chain-of-thought reasoning
- `clearledgr/services/cross_invoice_analysis.py` - Duplicate/anomaly detection
- `clearledgr/services/conversational_agent.py` - Clarifying questions via Slack
- `clearledgr/services/invoice_workflow.py` - Added reasoning to InvoiceData, Slack messages
- `ui/slack/app.py` - Clarifying question response handler

### Files Changed (Phase 5-9: Advanced Intelligence)
- `clearledgr/services/agent_reflection.py` - NEW: Self-checking, validates own output
- `clearledgr/services/proactive_insights.py` - NEW: Spending alerts, optimization suggestions
- `clearledgr/services/natural_language_commands.py` - NEW: "Approve all AWS under $500"
- `clearledgr/services/cashflow_prediction.py` - NEW: Forecast upcoming AP
- `clearledgr/services/correction_learning.py` - NEW: Learn from user corrections

### Files Changed (Phase 10-15: Enterprise Intelligence)
- `clearledgr/services/vendor_intelligence.py` - NEW: Know vendors before told
- `clearledgr/services/policy_compliance.py` - NEW: Auto-check against company policies
- `clearledgr/services/priority_detection.py` - NEW: Smart urgency/priority detection
- `clearledgr/services/audit_trail.py` - NEW: Complete explainable history
- `clearledgr/services/budget_awareness.py` - NEW: Real-time budget tracking
- `clearledgr/services/batch_intelligence.py` - NEW: Optimize bulk operations

---

*This document is the source of truth for agent architecture decisions.*
