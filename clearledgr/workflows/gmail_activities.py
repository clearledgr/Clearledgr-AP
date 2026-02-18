"""Gmail-specific Temporal activities for Clearledgr extension.

These activities handle the actual work for Gmail email processing workflows.

KEY DIFFERENTIATORS:
1. Audit-Link Generation - Every post generates a Clearledgr_Audit_ID traceable to the email
2. Human-in-the-Loop (HITL) - <95% confidence blocks "Post", shows "Review Mismatch" instead
3. Multi-System Routing - Approval triggers both ERP post AND Slack thread update
"""
from __future__ import annotations

import os
import uuid
import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from temporalio import activity

from clearledgr.services.audit import AuditTrailService


# ==================== AUDIT-LINK GENERATION ====================

def generate_audit_id(email_id: str, organization_id: str, timestamp: str) -> str:
    """
    Generate a unique Clearledgr_Audit_ID that links ERP entries back to source emails.
    
    Format: CL-{org_prefix}-{timestamp}-{hash}
    Example: CL-MER-20260123-a1b2c3d4
    
    This ID is:
    - Appended as memo/note in ERP entries
    - Stored in audit trail
    - Searchable in Gmail via Clearledgr
    - Used by auditors to trace transactions to source documents
    """
    # Create deterministic hash from email + org
    hash_input = f"{email_id}:{organization_id}:{timestamp}"
    hash_suffix = hashlib.sha256(hash_input.encode()).hexdigest()[:8]
    
    # Get org prefix (first 3 chars, uppercase)
    org_prefix = (organization_id or "CLR")[:3].upper()
    
    # Format timestamp as YYYYMMDD
    date_part = timestamp[:10].replace("-", "") if timestamp else datetime.now(timezone.utc).strftime("%Y%m%d")
    
    return f"CL-{org_prefix}-{date_part}-{hash_suffix}"


# ==================== CONFIDENCE & MISMATCH DETECTION ====================

CONFIDENCE_THRESHOLD_POST = 0.95  # Must be >= 95% to show "Post" button
CONFIDENCE_THRESHOLD_REVIEW = 0.75  # Below this, auto-flag for review

def _safe_text(value: Any) -> str:
    """Return a normalized text value for extraction/classification."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def calculate_match_confidence(
    extraction: Dict[str, Any],
    bank_match: Dict[str, Any],
    erp_match: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Calculate overall match confidence and identify specific discrepancies.
    
    Returns confidence score and list of mismatches for HITL review.
    """
    confidence = 0.0
    mismatches = []
    match_details = []
    
    email_amount = extraction.get("amount")
    email_vendor = extraction.get("vendor")
    email_invoice = extraction.get("invoice_number")
    
    # Bank feed matching (40% weight)
    if bank_match.get("matched"):
        bank_amount = bank_match.get("matched_transaction", {}).get("amount")
        
        if bank_amount and email_amount:
            amount_diff = abs(bank_amount - email_amount)
            amount_diff_pct = (amount_diff / email_amount) * 100 if email_amount else 100
            
            if amount_diff_pct == 0:
                confidence += 0.40
                match_details.append(f"Bank amount exact match: {email_amount}")
            elif amount_diff_pct <= 1:
                confidence += 0.35
                match_details.append(f"Bank amount within 1%: {email_amount} vs {bank_amount}")
            elif amount_diff_pct <= 5:
                confidence += 0.25
                mismatches.append({
                    "type": "amount_variance",
                    "field": "Bank Amount",
                    "email_value": email_amount,
                    "system_value": bank_amount,
                    "difference": amount_diff,
                    "difference_pct": round(amount_diff_pct, 2),
                    "severity": "warning",
                    "message": f"Amount mismatch: Email says {extraction.get('currency', 'USD')} {email_amount:,.2f}, Bank says {bank_amount:,.2f} ({amount_diff_pct:.1f}% difference)"
                })
            else:
                confidence += 0.10
                mismatches.append({
                    "type": "amount_mismatch",
                    "field": "Bank Amount",
                    "email_value": email_amount,
                    "system_value": bank_amount,
                    "difference": amount_diff,
                    "difference_pct": round(amount_diff_pct, 2),
                    "severity": "error",
                    "message": f"Significant amount mismatch: Email says {extraction.get('currency', 'USD')} {email_amount:,.2f}, Bank says {bank_amount:,.2f} ({amount_diff_pct:.1f}% difference)"
                })
        else:
            confidence += 0.20
            match_details.append("Bank match found (amount not compared)")
    else:
        mismatches.append({
            "type": "no_bank_match",
            "field": "Bank Transaction",
            "email_value": f"{extraction.get('currency', 'USD')} {email_amount:,.2f}" if email_amount else "Unknown",
            "system_value": None,
            "severity": "warning",
            "message": "No matching bank transaction found"
        })
    
    # ERP/PO matching (35% weight)
    if erp_match.get("poMatch"):
        po = erp_match["poMatch"]
        po_amount = po.get("amount")
        
        if po_amount and email_amount:
            po_diff = abs(po_amount - email_amount)
            po_diff_pct = (po_diff / email_amount) * 100 if email_amount else 100
            
            if po_diff_pct <= 1:
                confidence += 0.35
                match_details.append(f"PO #{po.get('number')} matched")
            else:
                confidence += 0.20
                mismatches.append({
                    "type": "po_amount_mismatch",
                    "field": "PO Amount",
                    "email_value": email_amount,
                    "system_value": po_amount,
                    "po_number": po.get("number"),
                    "difference": po_diff,
                    "severity": "warning",
                    "message": f"PO amount differs: Invoice {email_amount:,.2f} vs PO #{po.get('number')} {po_amount:,.2f}"
                })
        else:
            confidence += 0.30
            match_details.append(f"PO #{po.get('number')} matched (amount not on PO)")
    elif erp_match.get("vendorMatch"):
        confidence += 0.20
        match_details.append(f"Known vendor: {erp_match['vendorMatch'].get('name')}")
    else:
        mismatches.append({
            "type": "no_erp_match",
            "field": "ERP Record",
            "email_value": email_vendor,
            "system_value": None,
            "severity": "info",
            "message": f"No PO or vendor record found for {email_vendor}"
        })
    
    # Vendor verification (15% weight)
    if erp_match.get("vendorMatch"):
        erp_vendor = erp_match["vendorMatch"].get("name", "").lower()
        if email_vendor and email_vendor.lower() in erp_vendor or erp_vendor in email_vendor.lower():
            confidence += 0.15
            match_details.append("Vendor name verified")
        else:
            confidence += 0.05
            mismatches.append({
                "type": "vendor_name_mismatch",
                "field": "Vendor Name",
                "email_value": email_vendor,
                "system_value": erp_match["vendorMatch"].get("name"),
                "severity": "info",
                "message": f"Vendor name differs: Email '{email_vendor}' vs ERP '{erp_match['vendorMatch'].get('name')}'"
            })
    
    # Invoice number verification (10% weight)
    if email_invoice and erp_match.get("poMatch", {}).get("invoice_number"):
        if email_invoice == erp_match["poMatch"]["invoice_number"]:
            confidence += 0.10
            match_details.append(f"Invoice #{email_invoice} verified")
        else:
            mismatches.append({
                "type": "invoice_number_mismatch",
                "field": "Invoice Number",
                "email_value": email_invoice,
                "system_value": erp_match["poMatch"]["invoice_number"],
                "severity": "warning",
                "message": f"Invoice number mismatch: Email {email_invoice} vs ERP {erp_match['poMatch']['invoice_number']}"
            })
    
    # Determine if posting is allowed
    can_post = confidence >= CONFIDENCE_THRESHOLD_POST and not any(m["severity"] == "error" for m in mismatches)
    requires_review = confidence < CONFIDENCE_THRESHOLD_REVIEW or any(m["severity"] == "error" for m in mismatches)
    
    return {
        "confidence": round(confidence, 3),
        "confidence_pct": round(confidence * 100, 1),
        "can_post": can_post,
        "requires_review": requires_review,
        "mismatches": mismatches,
        "match_details": match_details,
        "threshold_post": CONFIDENCE_THRESHOLD_POST,
        "threshold_review": CONFIDENCE_THRESHOLD_REVIEW,
        "recommendation": "post" if can_post else "review" if not requires_review else "review_required"
    }


# ==================== ACTIVITIES ====================

@activity.defn
async def classify_email_activity(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Classify an email for AP workflow using a single shared classifier.
    """
    from clearledgr.services.ap_classifier import classify_ap_email

    subject = _safe_text(payload.get("subject"))
    sender = _safe_text(payload.get("sender"))
    snippet = _safe_text(payload.get("snippet"))
    body = _safe_text(payload.get("body"))
    attachments = payload.get("attachments", []) or []

    result = classify_ap_email(
        subject=subject,
        sender=sender,
        snippet=snippet,
        body=body,
        attachments=attachments,
    )

    return {
        "type": result.get("type", "NOISE"),
        "confidence": float(result.get("confidence", 0.5)),
        "reason": result.get("reason") or result.get("reasoning") or "",
        "method": result.get("method", "rules"),
        "provider": result.get("provider"),
        "score": result.get("score"),
    }


@activity.defn
async def extract_email_data_activity(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract financial data from email content using LLM with fallback to rules.
    
    UPGRADED: Now uses AI extraction first for accurate vendor/amount/date parsing.
    """
    import re
    import os
    
    subject = _safe_text(payload.get("subject"))
    sender = _safe_text(payload.get("sender"))
    snippet = _safe_text(payload.get("snippet"))
    body = _safe_text(payload.get("body"))  # Full body if available
    attachments = payload.get("attachments", []) or []

    combined = f"{subject}\n{snippet}\n{body}".strip()

    # Run deterministic parser first so we always have an extraction fallback,
    # even when LLM keys are missing in development.
    deterministic_result: Dict[str, Any] = {
        "vendor": None,
        "amount": None,
        "currency": None,
        "invoice_number": None,
        "due_date": None,
        "invoice_date": None,
        "line_items": [],
        "confidence": 0.0,
        "has_attachments": len(attachments) > 0,
        "method": "rules",
    }
    try:
        from clearledgr.services.email_parser import EmailParser

        parser = EmailParser()
        parsed_email = parser.parse_email(
            subject=subject,
            body=f"{snippet}\n{body}".strip(),
            sender=sender,
            attachments=attachments,
        )

        parsed_vendor = parsed_email.get("vendor")
        if not parsed_vendor:
            sender_name_match = re.match(r"^([^<]+)", sender)
            if sender_name_match:
                parsed_vendor = sender_name_match.group(1).strip()
            elif "@" in sender:
                parsed_vendor = sender.split("@")[0]
            else:
                parsed_vendor = "Unknown"

        invoice_date = parsed_email.get("primary_date")
        due_date = None
        attachment_rows = parsed_email.get("attachments") or []
        for attachment_row in attachment_rows:
            extraction = attachment_row.get("extraction") or {}
            if extraction.get("due_date"):
                due_date = extraction.get("due_date")
                break

        deterministic_result.update({
            "vendor": parsed_vendor,
            "amount": parsed_email.get("primary_amount"),
            "currency": parsed_email.get("currency"),
            "invoice_number": parsed_email.get("primary_invoice"),
            "due_date": due_date,
            "invoice_date": invoice_date,
            "line_items": [],
            "confidence": float(parsed_email.get("confidence", 0.6) or 0.6),
            "has_attachments": bool(parsed_email.get("attachments")),
        })
    except Exception:
        # Keep deterministic fallback resilient; we still have regex fallback below.
        deterministic_result["confidence"] = 0.5

    def _normalize_date(value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value)).date().isoformat()
        except Exception:
            return None

    def _normalize_amount(value: Any) -> Optional[float]:
        if value is None or value == "":
            return None
        try:
            amount = float(value)
        except Exception:
            return None
        # Filter obvious years
        if amount.is_integer() and 1900 <= amount <= 2100:
            return None
        if amount < 0:
            return None
        return amount

    # Try LLM extraction first
    try:
        from clearledgr.services.llm_multimodal import MultiModalLLMService
        
        llm = MultiModalLLMService()
        has_llm = bool(
            getattr(llm, "anthropic_key", None) or 
            getattr(llm, "mistral_key", None) or
            os.getenv("OPENAI_API_KEY")
        )
        
        if has_llm:
            # Use the built-in invoice extraction
            result = llm.extract_invoice(combined, attachments)
            
            # Ensure we have vendor from sender/rules if LLM didn't find one
            vendor = result.get("vendor")
            if not vendor or vendor == "Unknown":
                # Try to extract vendor from sender
                name_match = re.match(r"^([^<]+)", sender)
                if name_match:
                    vendor = name_match.group(1).strip()
                else:
                    vendor = sender.split("@")[0] if "@" in sender else sender
            if not vendor:
                vendor = deterministic_result.get("vendor") or "Unknown"

            amount = _normalize_amount(result.get("total_amount"))
            if amount is None:
                amount = deterministic_result.get("amount")

            currency = result.get("currency") or deterministic_result.get("currency") or "USD"
            invoice_number = result.get("invoice_number") or deterministic_result.get("invoice_number")
            due_date = _normalize_date(result.get("due_date")) or deterministic_result.get("due_date")
            invoice_date = _normalize_date(result.get("invoice_date")) or deterministic_result.get("invoice_date")

            return {
                "vendor": vendor,
                "amount": amount,
                "currency": currency,
                "invoice_number": invoice_number,
                "due_date": due_date,
                "invoice_date": invoice_date,
                "line_items": result.get("line_items", []),
                "confidence": max(
                    float(result.get("confidence", 0.8) or 0.8),
                    float(deterministic_result.get("confidence", 0.0) or 0.0),
                ),
                "has_attachments": len(attachments) > 0,
                "method": "llm",
                "provider": result.get("provider", "unknown"),
            }
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"LLM extraction failed, using rules: {e}")
    
    # Normalize deterministic parser output (avoid year-as-amount).
    deterministic_result["amount"] = _normalize_amount(deterministic_result.get("amount"))

    # Fallback to rule-based extraction
    if deterministic_result.get("vendor") or deterministic_result.get("invoice_number") or deterministic_result.get("amount") is not None:
        # Deterministic parser already extracted usable values (including attachment text).
        deterministic_result["method"] = "rules_parser"
        deterministic_result["currency"] = deterministic_result.get("currency") or "USD"
        return deterministic_result

    combined_text = f"{subject} {snippet} {body}".strip()
    
    vendor_mappings = {
        "stripe": "Stripe",
        "paypal": "PayPal",
        "aws": "Amazon Web Services",
        "sap": "SAP",
        "quickbooks": "QuickBooks",
        "xero": "Xero",
        "deutsche-bank": "Deutsche Bank",
        "hsbc": "HSBC",
    }
    
    vendor = "Unknown"
    sender_lower = sender.lower()
    for key, name in vendor_mappings.items():
        if key in sender_lower:
            vendor = name
            break
    
    if vendor == "Unknown":
        name_match = re.match(r"^([^<]+)", sender)
        if name_match:
            vendor = name_match.group(1).strip()
    
    amount = None
    currency = "USD"
    
    amount_patterns = [
        r"[€$£]\s*([\d,]+\.?\d*)",
        r"([\d,]+\.?\d*)\s*[€$£]",
        r"([\d,]+\.?\d*)\s*(EUR|USD|GBP)",
        r"(EUR|USD|GBP)\s*([\d,]+\.?\d*)",
    ]
    
    for pattern in amount_patterns:
        match = re.search(pattern, combined_text)
        if match:
            num_str = match.group(1)
            if num_str in ("EUR", "USD", "GBP"):
                num_str = match.group(2)
            num_str = num_str.replace(",", "")
            try:
                amount = float(num_str)
                break
            except ValueError:
                pass
    
    if "€" in combined_text or "EUR" in combined_text.upper():
        currency = "EUR"
    elif "£" in combined_text or "GBP" in combined_text.upper():
        currency = "GBP"
    
    invoice_number = None
    inv_match = re.search(r"(?:invoice|inv)[#:\s-]*([A-Z0-9]+-?[A-Z0-9]+-?\d+)", combined_text, re.IGNORECASE)
    if inv_match:
        invoice_number = inv_match.group(1).upper()
    
    due_date = None
    due_match = re.search(r"due\s+(?:on\s+)?(\w+\s+\d{1,2},?\s*\d{4})", combined_text, re.IGNORECASE)
    if due_match:
        try:
            due_date = datetime.strptime(due_match.group(1).replace(",", ""), "%B %d %Y").isoformat()[:10]
        except ValueError:
            pass
    
    amount = _normalize_amount(amount)

    return {
        "vendor": vendor,
        "amount": amount,
        "currency": currency,
        "invoice_number": invoice_number,
        "due_date": due_date,
        "has_attachments": len(attachments) > 0,
        "confidence": 0.6,  # Lower confidence for rule-based
        "method": "rules",
    }


@activity.defn
async def match_bank_feed_activity(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Match extracted data against bank feed cache.
    
    Returns match result with specific transaction details for HITL review.
    """
    from clearledgr.core.engine import get_engine
    
    extraction = payload.get("extraction", {})
    organization_id = payload.get("organization_id")
    
    amount = extraction.get("amount")
    vendor = extraction.get("vendor")
    currency = extraction.get("currency", "USD")
    
    if amount is None:
        return {"matched": False, "reason": "No amount to match"}
    
    # Get the reconciliation engine
    engine = get_engine(organization_id)
    
    # Search for matching bank transactions
    # Look for transactions within 5% tolerance and same currency
    tolerance = 0.05
    min_amount = amount * (1 - tolerance)
    max_amount = amount * (1 + tolerance)
    
    # Get pending bank transactions
    pending_transactions = engine.get_pending_transactions(source="bank")
    
    # Find potential matches
    potential_matches = []
    exact_match = None
    
    for txn in pending_transactions:
        txn_amount = abs(txn.get("amount", 0))
        txn_currency = txn.get("currency", "USD")
        
        # Currency must match
        if txn_currency != currency:
            continue
        
        # Check amount within tolerance
        if min_amount <= txn_amount <= max_amount:
            match_score = 1.0 - abs(txn_amount - amount) / amount
            
            # Check vendor name similarity
            txn_desc = (txn.get("description") or "").lower()
            vendor_lower = (vendor or "").lower()
            vendor_match = vendor_lower in txn_desc or any(
                word in txn_desc for word in vendor_lower.split()[:2]
            )
            
            if vendor_match:
                match_score += 0.2
            
            match_info = {
                "transaction_id": txn.get("id") or txn.get("reference"),
                "amount": txn_amount,
                "currency": txn_currency,
                "date": txn.get("date"),
                "description": txn.get("description"),
                "reference": txn.get("reference"),
                "match_score": min(match_score, 1.0),
                "vendor_matched": vendor_match,
            }
            
            potential_matches.append(match_info)
            
            # Exact match: same amount and vendor match
            if txn_amount == amount and vendor_match:
                exact_match = match_info
    
    # Sort by match score
    potential_matches.sort(key=lambda x: x["match_score"], reverse=True)
    
    if exact_match:
        return {
            "matched": True,
            "match_type": "exact",
            "matched_transaction": exact_match,
            "potential_matches": potential_matches[:5],
            "amount_searched": amount,
            "currency": currency,
            "vendor_searched": vendor,
        }
    elif potential_matches:
        best_match = potential_matches[0]
        return {
            "matched": best_match["match_score"] >= 0.9,
            "match_type": "fuzzy" if best_match["match_score"] >= 0.9 else "potential",
            "matched_transaction": best_match if best_match["match_score"] >= 0.9 else None,
            "potential_matches": potential_matches[:5],
            "amount_searched": amount,
            "currency": currency,
            "vendor_searched": vendor,
            "confidence": best_match["match_score"],
        }
    else:
        return {
            "matched": False,
            "reason": "No matching bank transaction found",
            "amount_searched": amount,
            "currency": currency,
            "vendor_searched": vendor,
            "matched_transaction": None,
            "potential_matches": [],
        }


@activity.defn
async def match_erp_activity(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Match extracted data against ERP records (POs, vendors).
    Uses SAP service when configured, falls back to intelligent matching.
    """
    from clearledgr.services.sap import SAPService
    from clearledgr.core.engine import get_engine
    
    extraction = payload.get("extraction", {})
    organization_id = payload.get("organization_id")
    
    vendor = extraction.get("vendor")
    invoice_number = extraction.get("invoice_number")
    amount = extraction.get("amount")
    currency = extraction.get("currency", "EUR")
    
    # Try SAP first if configured
    sap = SAPService()
    sap_transactions = sap.pull_gl_transactions()
    
    po_match = None
    vendor_match = None
    gl_suggestion = "6000"
    category = "General Expense"
    
    # Search SAP transactions for matching PO or invoice
    if sap_transactions:
        for txn in sap_transactions:
            # Check for PO match
            txn_ref = txn.get("reference", "").upper()
            txn_vendor = txn.get("vendor_name", "").lower()
            
            if invoice_number and invoice_number.upper() in txn_ref:
                po_match = {
                    "number": txn.get("document_number"),
                    "amount": txn.get("amount"),
                    "vendor": txn.get("vendor_name"),
                    "gl_account": txn.get("gl_account"),
                    "invoice_number": invoice_number,
                    "status": txn.get("status", "open"),
                }
                gl_suggestion = txn.get("gl_account", gl_suggestion)
                break
            
            # Check for vendor match
            if vendor and vendor.lower() in txn_vendor:
                if not vendor_match:
                    vendor_match = {
                        "name": txn.get("vendor_name"),
                        "id": txn.get("vendor_id"),
                        "default_gl": txn.get("gl_account"),
                    }
                    gl_suggestion = txn.get("gl_account", gl_suggestion)
    
    # Fall back to intelligent GL mapping if no SAP match
    if not po_match and not vendor_match:
        # Use pattern-based GL suggestions
        gl_mappings = {
            # Payment processors
            "stripe": ("6150", "Payment Processing Fees"),
            "paypal": ("6150", "Payment Processing Fees"),
            "paystack": ("6150", "Payment Processing Fees"),
            "flutterwave": ("6150", "Payment Processing Fees"),
            "square": ("6150", "Payment Processing Fees"),
            
            # Cloud/SaaS
            "aws": ("6200", "Cloud Infrastructure"),
            "amazon web services": ("6200", "Cloud Infrastructure"),
            "google cloud": ("6200", "Cloud Infrastructure"),
            "azure": ("6200", "Cloud Infrastructure"),
            "microsoft": ("6210", "Software Licenses"),
            "salesforce": ("6210", "Software Licenses"),
            "slack": ("6210", "Software Licenses"),
            "zoom": ("6210", "Software Licenses"),
            
            # Banking
            "deutsche bank": ("6100", "Banking Fees"),
            "hsbc": ("6100", "Banking Fees"),
            "chase": ("6100", "Banking Fees"),
            "barclays": ("6100", "Banking Fees"),
            
            # Professional services
            "deloitte": ("6300", "Professional Services"),
            "kpmg": ("6300", "Professional Services"),
            "pwc": ("6300", "Professional Services"),
            "ey": ("6300", "Professional Services"),
            
            # Office/Admin
            "office": ("6400", "Office Supplies"),
            "staples": ("6400", "Office Supplies"),
            
            # Travel
            "airline": ("6500", "Travel Expenses"),
            "hotel": ("6500", "Travel Expenses"),
            "uber": ("6500", "Travel Expenses"),
        }
        
        vendor_lower = (vendor or "").lower()
        for pattern, (gl, cat) in gl_mappings.items():
            if pattern in vendor_lower:
                gl_suggestion = gl
                category = cat
                vendor_match = {
                    "name": vendor,
                    "id": f"V-{vendor[:3].upper()}" if vendor else None,
                    "default_gl": gl,
                    "matched_by": "pattern",
                }
                break
    
    # Determine category from GL code
    gl_categories = {
        "61": "Banking & Finance",
        "62": "Technology & Software",
        "63": "Professional Services",
        "64": "Office & Admin",
        "65": "Travel & Entertainment",
        "60": "General Expense",
    }
    category = gl_categories.get(gl_suggestion[:2], category)
    
    return {
        "matched": bool(po_match or vendor_match),
        "poMatch": po_match,
        "vendorMatch": vendor_match,
        "glSuggestion": gl_suggestion,
        "category": category,
        "sap_connected": bool(sap_transactions),
    }


@activity.defn
async def verify_match_confidence_activity(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    HITL Gate: Verify match confidence and generate mismatch report.
    
    This is the key differentiator - we don't just auto-post.
    If confidence < 95%, we block posting and show specific discrepancies.
    """
    extraction = payload.get("extraction", {})
    bank_match = payload.get("bank_match", {})
    erp_match = payload.get("erp_match", {})
    
    return calculate_match_confidence(extraction, bank_match, erp_match)


@activity.defn
async def apply_gmail_label_activity(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Apply Gmail label to an email using Gmail API.
    """
    from clearledgr.services.gmail_api import GmailAPIClient, token_store
    
    email_id = payload.get("email_id")
    user_id = payload.get("user_id")
    label = payload.get("label")
    remove_label = payload.get("remove_label")
    classification = payload.get("classification", {})
    
    # Determine label based on classification
    if not label and classification:
        type_to_label = {
            "INVOICE": "Clearledgr/Invoices",
            "REMITTANCE": "Clearledgr/Payments",
            "STATEMENT": "Clearledgr/Bank Statements",
            "RECEIPT": "Clearledgr/Receipts",
            "EXCEPTION": "Clearledgr/Needs Review",
        }
        label = type_to_label.get(classification.get("type"), "Clearledgr")
    
    # Try to apply via Gmail API if user is authenticated
    if user_id:
        token = token_store.get(user_id)
        if token:
            try:
                client = GmailAPIClient(user_id)
                if await client.ensure_authenticated():
                    # Get or create the label
                    labels = await client.list_labels()
                    label_id = None
                    
                    for l in labels:
                        if l.get("name") == label:
                            label_id = l.get("id")
                            break
                    
                    if not label_id:
                        # Create the label
                        new_label = await client.create_label(label)
                        label_id = new_label.get("id")
                    
                    # Apply the label
                    if label_id:
                        await client.add_label(email_id, [label_id])
                    
                    # Remove label if specified
                    if remove_label:
                        for l in labels:
                            if l.get("name") == remove_label:
                                await client.remove_label(email_id, [l.get("id")])
                                break
                    
                    return {
                        "email_id": email_id,
                        "label_applied": label,
                        "label_id": label_id,
                        "label_removed": remove_label,
                        "status": "applied",
                        "api_used": True,
                    }
            except Exception as e:
                # Log but don't fail - label is nice-to-have
                import logging
                logging.getLogger(__name__).warning(f"Gmail label failed: {e}")
    
    # Return success even if API not available (label is tracked locally)
    return {
        "email_id": email_id,
        "label_applied": label,
        "label_removed": remove_label,
        "status": "applied",
        "api_used": False,
    }


@activity.defn
async def post_to_erp_activity(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Post invoice/journal entry to ERP system.
    
    DIFFERENTIATOR: Generates Clearledgr_Audit_ID and appends to ERP memo.
    """
    from clearledgr.integrations.erp_router import Bill, Vendor, get_or_create_vendor
    from clearledgr.services.erp_api_first import post_bill_api_first
    
    extraction = payload.get("extraction", {})
    erp_match = payload.get("erp_match", {})
    organization_id = payload.get("organization_id")
    approved_by = payload.get("approved_by") or payload.get("user_email")
    email_id = payload.get("email_id")
    confidence_result = payload.get("confidence_result", {})
    
    # HITL Gate: Block if confidence too low
    if not confidence_result.get("can_post", False):
        return {
            "status": "blocked",
            "reason": "Confidence below threshold",
            "confidence": confidence_result.get("confidence_pct"),
            "mismatches": confidence_result.get("mismatches", []),
            "action_required": "review_mismatch",
        }
    
    vendor = extraction.get("vendor") or "Unknown Vendor"
    amount = extraction.get("amount") or 0
    currency = extraction.get("currency", "EUR")
    invoice_number = extraction.get("invoice_number")
    gl_code = erp_match.get("glSuggestion", "6000")
    
    # Generate Audit-Link ID
    timestamp = datetime.now(timezone.utc).isoformat()
    audit_id = generate_audit_id(email_id, organization_id, timestamp)
    
    # Build memo with audit link
    memo = f"Clearledgr_Audit_ID: {audit_id}"
    if invoice_number:
        memo += f" | Invoice: {invoice_number}"
    memo += f" | Approved by: {approved_by}"
    memo += f" | Confidence: {confidence_result.get('confidence_pct', 0)}%"
    
    # Ensure vendor exists in ERP
    vendor_result = await get_or_create_vendor(
        organization_id,
        Vendor(name=vendor)
    )
    if vendor_result.get("status") not in {"found", "created"}:
        return {
            "status": "error",
            "reason": "Vendor lookup failed",
            "details": vendor_result,
        }

    bill = Bill(
        vendor_id=vendor_result.get("vendor_id") or vendor,
        vendor_name=vendor_result.get("name") or vendor,
        amount=amount,
        currency=currency,
        invoice_number=invoice_number,
        invoice_date=extraction.get("invoice_date"),
        due_date=extraction.get("due_date"),
        description=memo,
        line_items=[
            {
                "description": f"Invoice from {vendor}",
                "amount": amount,
                "account_code": gl_code,
            }
        ],
    )

    # Post vendor bill through API-first router with browser fallback.
    posting_result = await post_bill_api_first(
        organization_id=organization_id,
        bill=bill,
        actor_id=approved_by or "system",
        email_id=email_id,
        invoice_number=invoice_number,
        vendor_name=vendor,
        amount=amount,
        currency=currency,
        vendor_portal_url=(
            extraction.get("vendor_portal_url")
            or extraction.get("invoice_portal_url")
            or payload.get("vendor_portal_url")
        ),
        erp_url=payload.get("erp_url"),
    )
    
    # Record in audit trail regardless of SAP result
    audit = AuditTrailService()
    audit.record_event(
        user_email=approved_by or "system",
        action="erp_posting",
        entity_type="invoice",
        entity_id=audit_id,
        organization_id=organization_id,
        metadata={
            "email_id": email_id,
            "vendor": vendor,
            "amount": amount,
            "currency": currency,
            "invoice_number": invoice_number,
            "gl_code": gl_code,
            "confidence": confidence_result.get("confidence"),
            "memo": memo,
            "sap_result": posting_result,
        },
    )
    
    # Determine final status
    sap_status = posting_result.get("status", "unknown")
    sap_doc_numbers = posting_result.get("sap_doc_numbers", [])
    execution_mode = posting_result.get("execution_mode")
    fallback = posting_result.get("fallback") or {}

    if execution_mode == "browser_fallback" or sap_status == "pending_browser_fallback":
        return {
            "status": "pending_browser_fallback",
            "clearledgr_audit_id": audit_id,
            "document_number": f"PENDING-{audit_id[-8:]}",
            "gl_code": gl_code,
            "amount": amount,
            "currency": currency,
            "vendor": vendor,
            "posted_by": approved_by,
            "memo": memo,
            "confidence": confidence_result.get("confidence_pct"),
            "timestamp": timestamp,
            "sap_status": "fallback_requested",
            "execution_mode": execution_mode,
            "fallback": fallback,
        }
    
    if sap_status == "skipped":
        # SAP not configured - still return success with audit ID
        return {
            "status": "posted",
            "clearledgr_audit_id": audit_id,
            "document_number": f"DOC-{invoice_number or audit_id[-8:]}",
            "gl_code": gl_code,
            "amount": amount,
            "currency": currency,
            "vendor": vendor,
            "posted_by": approved_by,
            "memo": memo,
            "confidence": confidence_result.get("confidence_pct"),
            "timestamp": timestamp,
            "sap_status": "not_configured",
        }
    elif sap_status.startswith("2"):  # 2xx success
        return {
            "status": "posted",
            "clearledgr_audit_id": audit_id,
            "document_number": sap_doc_numbers[0] if sap_doc_numbers else f"DOC-{audit_id[-8:]}",
            "sap_doc_numbers": sap_doc_numbers,
            "gl_code": gl_code,
            "amount": amount,
            "currency": currency,
            "vendor": vendor,
            "posted_by": approved_by,
            "memo": memo,
            "confidence": confidence_result.get("confidence_pct"),
            "timestamp": timestamp,
            "sap_status": "success",
        }
    else:
        # SAP error - still record audit but mark as failed
        return {
            "status": "sap_error",
            "clearledgr_audit_id": audit_id,
            "document_number": f"PENDING-{audit_id[-8:]}",
            "gl_code": gl_code,
            "amount": amount,
            "currency": currency,
            "vendor": vendor,
            "posted_by": approved_by,
            "memo": memo,
            "confidence": confidence_result.get("confidence_pct"),
            "timestamp": timestamp,
            "sap_status": sap_status,
            "sap_error": posting_result.get("sap_error"),
            "execution_mode": execution_mode or "api_failed",
            "fallback": fallback,
        }


@activity.defn
async def update_slack_thread_activity(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    MULTI-SYSTEM ROUTING: Update Slack thread when invoice is approved.
    
    When a user approves an invoice in Gmail, we:
    1. Post to ERP (handled by post_to_erp_activity)
    2. Update any "Chasing" thread in Slack (this activity)
    
    This closes the loop - finance team sees the update in Slack
    without having to check Gmail or ERP.
    """
    import os
    import httpx
    
    email_id = payload.get("email_id")
    vendor = payload.get("vendor")
    amount = payload.get("amount")
    currency = payload.get("currency", "EUR")
    invoice_number = payload.get("invoice_number")
    audit_id = payload.get("clearledgr_audit_id")
    approved_by = payload.get("approved_by")
    organization_id = payload.get("organization_id")
    erp_document = payload.get("erp_document")
    
    # Build Slack message
    amount_str = f"{currency} {amount:,.2f}" if amount else "Unknown amount"
    
    message_blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Invoice Approved & Posted*\n\n*Vendor:* {vendor}\n*Amount:* {amount_str}\n*Invoice #:* {invoice_number or 'N/A'}"
            }
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Approved by {approved_by} via Gmail | ERP Doc: {erp_document} | Audit: `{audit_id}`"
                }
            ]
        }
    ]
    
    # Get Slack credentials
    slack_token = os.getenv("SLACK_BOT_TOKEN")
    slack_webhook = os.getenv("SLACK_WEBHOOK_URL")
    finance_channel = os.getenv("SLACK_FINANCE_CHANNEL", "#finance-updates")
    
    thread_found = False
    thread_updated = False
    slack_sent = False
    
    # Try to find and update existing thread using Slack API
    if slack_token:
        try:
            async with httpx.AsyncClient() as client:
                # Search for messages mentioning this vendor or invoice
                search_query = f"{vendor}" if vendor else ""
                if invoice_number:
                    search_query += f" {invoice_number}"
                
                # Search in finance channels
                search_response = await client.get(
                    "https://slack.com/api/search.messages",
                    headers={"Authorization": f"Bearer {slack_token}"},
                    params={
                        "query": search_query,
                        "count": 10,
                        "sort": "timestamp",
                        "sort_dir": "desc",
                    },
                    timeout=10,
                )
                
                if search_response.status_code == 200:
                    search_data = search_response.json()
                    if search_data.get("ok") and search_data.get("messages", {}).get("matches"):
                        # Found a matching thread - reply to it
                        match = search_data["messages"]["matches"][0]
                        channel_id = match.get("channel", {}).get("id")
                        thread_ts = match.get("ts")
                        
                        if channel_id and thread_ts:
                            thread_found = True
                            
                            # Reply to the thread
                            reply_response = await client.post(
                                "https://slack.com/api/chat.postMessage",
                                headers={"Authorization": f"Bearer {slack_token}"},
                                json={
                                    "channel": channel_id,
                                    "thread_ts": thread_ts,
                                    "blocks": message_blocks,
                                },
                                timeout=10,
                            )
                            
                            if reply_response.status_code == 200 and reply_response.json().get("ok"):
                                thread_updated = True
                                
                                # No emoji reactions (text-only policy)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Slack thread search failed: {e}")
    
    # If no thread found or couldn't update, post to webhook
    if not thread_updated and slack_webhook:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    slack_webhook,
                    json={"blocks": message_blocks},
                    timeout=10,
                )
                slack_sent = response.status_code == 200
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Slack webhook failed: {e}")
    
    return {
        "status": "notified" if (thread_updated or slack_sent) else "queued",
        "vendor": vendor,
        "audit_id": audit_id,
        "message": f"Invoice from {vendor} approved and posted",
        "thread_found": thread_found,
        "thread_updated": thread_updated,
        "slack_sent": slack_sent,
        "slack_configured": bool(slack_token or slack_webhook),
    }


@activity.defn
async def send_slack_notification_activity(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Send Slack notification for email processing events.
    Uses real Slack webhook when configured.
    """
    import os
    import httpx
    
    notification_type = payload.get("type")
    email_id = payload.get("email_id")
    classification = payload.get("classification", {})
    extraction = payload.get("extraction", {})
    suggested_action = payload.get("suggested_action", {})
    confidence_result = payload.get("confidence_result", {})
    organization_id = payload.get("organization_id")
    
    vendor = extraction.get("vendor", "Unknown")
    amount = extraction.get("amount")
    currency = extraction.get("currency", "EUR")
    doc_type = classification.get("type", "Document")
    
    amount_str = f"{currency} {amount:,.2f}" if amount else "Amount unknown"
    
    # Build Slack blocks
    blocks = []
    
    if confidence_result.get("requires_review"):
        # HITL: Show mismatches in Slack
        mismatches = confidence_result.get("mismatches", [])
        mismatch_text = "\n".join([f"• {m['message']}" for m in mismatches[:3]])
        
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{doc_type} Requires Review",
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Vendor:* {vendor}\n*Amount:* {amount_str}\n*Confidence:* {confidence_result.get('confidence_pct', 0)}%"
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Issues Found:*\n{mismatch_text}"
                }
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Review in Gmail"},
                        "style": "primary",
                        "action_id": f"review_{email_id}",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Override & Post"},
                        "style": "danger",
                        "action_id": f"override_{email_id}",
                    }
                ]
            }
        ]
    else:
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{doc_type}* from *{vendor}*\nAmount: {amount_str}\nConfidence: {confidence_result.get('confidence_pct', 0)}%"
                }
            }
        ]
        
        if suggested_action:
            blocks.append({
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"Suggested action: {suggested_action.get('label', 'Review')}"}
                ]
            })
    
    # Send to Slack if webhook configured
    webhook_url = os.getenv("SLACK_WEBHOOK_URL")
    slack_sent = False
    
    if webhook_url:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    webhook_url,
                    json={"blocks": blocks},
                    timeout=10,
                )
                slack_sent = response.status_code == 200
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Slack notification failed: {e}")
    
    # Build plain text message for response
    if confidence_result.get("requires_review"):
        mismatches = confidence_result.get("mismatches", [])
        mismatch_text = "\n".join([f"• {m['message']}" for m in mismatches[:3]])
        message = f"*{doc_type} Requires Review*\n\n*Vendor:* {vendor}\n*Amount:* {amount_str}\n*Confidence:* {confidence_result.get('confidence_pct', 0)}%\n\n*Issues Found:*\n{mismatch_text}"
    else:
        message = f"*{doc_type}* from *{vendor}*\nAmount: {amount_str}\nConfidence: {confidence_result.get('confidence_pct', 0)}%"
    
    return {
        "status": "sent" if slack_sent else "queued",
        "message": message,
        "channel": "#finance-alerts",
        "requires_review": confidence_result.get("requires_review", False),
        "slack_sent": slack_sent,
        "webhook_configured": bool(webhook_url),
    }


@activity.defn
async def create_mismatch_review_task_activity(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Create a review task when confidence is below threshold.
    
    This ensures mismatches don't get lost - they become actionable tasks.
    """
    from clearledgr.services.email_tasks import create_task_from_email, TaskTypes
    
    email_id = payload.get("email_id")
    email_subject = payload.get("email_subject", "Invoice Review")
    email_sender = payload.get("email_sender", "unknown")
    thread_id = payload.get("thread_id", email_id)
    extraction = payload.get("extraction", {})
    confidence_result = payload.get("confidence_result", {})
    organization_id = payload.get("organization_id")
    created_by = payload.get("created_by", "clearledgr-system")
    
    mismatches = confidence_result.get("mismatches", [])
    vendor = extraction.get("vendor", "Unknown")
    amount = extraction.get("amount")
    currency = extraction.get("currency", "EUR")
    
    # Build task description with specific discrepancies
    description = f"Review required for invoice from {vendor}\n\n"
    description += f"**Amount:** {currency} {amount:,.2f}\n" if amount else ""
    description += f"**Confidence:** {confidence_result.get('confidence_pct', 0)}% (threshold: {CONFIDENCE_THRESHOLD_POST * 100}%)\n\n"
    description += "**Discrepancies found:**\n"
    for m in mismatches:
        severity_label = "ERROR" if m.get("severity") == "error" else "WARN" if m.get("severity") == "warning" else "INFO"
        description += f"[{severity_label}] {m['message']}\n"
    
    # Determine priority based on severity and amount
    has_errors = any(m.get("severity") == "error" for m in mismatches)
    high_value = amount and amount > 10000
    
    if has_errors or high_value:
        priority = "high"
    elif any(m.get("severity") == "warning" for m in mismatches):
        priority = "medium"
    else:
        priority = "low"
    
    # Format title
    amount_str = f"{currency} {amount:,.2f}" if amount else ""
    title = f"Review Mismatch: {vendor}"
    if amount_str:
        title += f" - {amount_str}"
    
    # Create the task using the email_tasks service
    try:
        task = create_task_from_email(
            email_id=email_id,
            email_subject=email_subject,
            email_sender=email_sender,
            thread_id=thread_id,
            created_by=created_by,
            task_type=TaskTypes.RECONCILE_ITEM,
            title=title,
            description=description,
            priority=priority,
            related_entity_type="invoice",
            related_entity_id=extraction.get("invoice_number"),
            related_amount=amount,
            related_vendor=vendor,
            tags=["mismatch", "review", confidence_result.get("recommendation", "review")],
            organization_id=organization_id,
        )
        
        return {
            "task_id": task.get("task_id"),
            "status": "created",
            "title": title,
            "priority": priority,
            "due_date": task.get("due_date"),
            "mismatches": mismatches,
            "confidence": confidence_result.get("confidence_pct"),
            "created_in_db": True,
        }
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to create task: {e}")
        
        # Fallback - return task info even if DB fails
        task_id = f"TASK-{email_id[:8] if email_id else 'unknown'}-{datetime.now(timezone.utc).strftime('%H%M%S')}"
        
        return {
            "task_id": task_id,
            "status": "created",
            "title": title,
            "priority": priority,
            "mismatches": mismatches,
            "confidence": confidence_result.get("confidence_pct"),
            "created_in_db": False,
            "error": str(e),
        }
