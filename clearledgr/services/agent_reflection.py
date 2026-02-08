"""
Agent Self-Reflection Service

Enables the agent to check and validate its own work before finalizing decisions.
Implements a "think twice" pattern where the agent:
1. Makes initial extraction/decision
2. Reflects on potential issues
3. Self-corrects if needed
4. Proceeds with higher confidence

Architecture: Part of the REASONING LAYER
See: docs/AGENT_ARCHITECTURE.md

Changelog:
- 2026-01-23: Initial implementation
"""

import logging
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass

from clearledgr.services.llm_multimodal import MultiModalLLMService

logger = logging.getLogger(__name__)


@dataclass
class ReflectionResult:
    """Result of self-reflection."""
    original_extraction: Dict[str, Any]
    reflection_notes: List[str]
    corrections_made: List[Dict[str, Any]]
    final_extraction: Dict[str, Any]
    confidence_adjustment: float  # +/- adjustment to confidence
    issues_found: List[str]
    self_verified: bool


class AgentReflection:
    """
    Self-reflection capability for the agent.
    
    The agent "thinks twice" about its decisions:
    1. Initial extraction
    2. Reflection prompt: "What might I have gotten wrong?"
    3. Validation: Cross-check key fields
    4. Correction: Fix any issues found
    5. Confidence adjustment: Increase if validated, decrease if corrected
    
    Usage:
        reflection = AgentReflection()
        result = reflection.reflect_on_extraction(
            extraction={"vendor": "Stripe", "amount": 299.00},
            original_text="Your invoice for $299.00 from Stripe Inc..."
        )
        
        if result.corrections_made:
            print(f"Self-corrected: {result.corrections_made}")
    """
    
    def __init__(self):
        self.llm = MultiModalLLMService()
    
    def reflect_on_extraction(
        self,
        extraction: Dict[str, Any],
        original_text: str,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> ReflectionResult:
        """
        Reflect on an extraction and self-correct if needed.
        """
        reflection_notes = []
        corrections = []
        issues = []
        confidence_adj = 0.0
        
        # Step 1: Validate required fields
        field_issues = self._validate_required_fields(extraction)
        issues.extend(field_issues)
        if field_issues:
            confidence_adj -= 0.05 * len(field_issues)
        
        # Step 2: Cross-check amount against text
        amount_check = self._verify_amount(extraction, original_text)
        if amount_check["issue"]:
            issues.append(amount_check["issue"])
            if amount_check["correction"]:
                corrections.append(amount_check["correction"])
                confidence_adj -= 0.1
        else:
            reflection_notes.append("Amount verified against source text")
            confidence_adj += 0.05
        
        # Step 3: Verify vendor name
        vendor_check = self._verify_vendor(extraction, original_text)
        if vendor_check["issue"]:
            issues.append(vendor_check["issue"])
            if vendor_check["correction"]:
                corrections.append(vendor_check["correction"])
                confidence_adj -= 0.1
        else:
            reflection_notes.append("Vendor name verified")
            confidence_adj += 0.05
        
        # Step 4: Check for inconsistencies
        inconsistencies = self._check_inconsistencies(extraction)
        issues.extend(inconsistencies)
        if inconsistencies:
            confidence_adj -= 0.05 * len(inconsistencies)
        
        # Step 5: LLM reflection (if available and extraction is uncertain)
        if extraction.get("confidence", 1.0) < 0.9:
            llm_reflection = self._llm_reflect(extraction, original_text)
            if llm_reflection:
                reflection_notes.extend(llm_reflection.get("notes", []))
                if llm_reflection.get("corrections"):
                    corrections.extend(llm_reflection["corrections"])
                    confidence_adj -= 0.05
        
        # Apply corrections to create final extraction
        final_extraction = self._apply_corrections(extraction, corrections)
        
        # Determine if self-verified (no major issues)
        self_verified = len(issues) == 0 and len(corrections) == 0
        if self_verified:
            reflection_notes.append("Self-verification passed")
            confidence_adj += 0.1
        
        logger.info(
            f"Reflection complete: {len(issues)} issues, {len(corrections)} corrections, "
            f"confidence adjustment: {confidence_adj:+.2f}"
        )
        
        return ReflectionResult(
            original_extraction=extraction,
            reflection_notes=reflection_notes,
            corrections_made=corrections,
            final_extraction=final_extraction,
            confidence_adjustment=confidence_adj,
            issues_found=issues,
            self_verified=self_verified,
        )
    
    def _validate_required_fields(self, extraction: Dict[str, Any]) -> List[str]:
        """Validate that required fields are present and reasonable."""
        issues = []
        
        # Check vendor
        vendor = extraction.get("vendor")
        if not vendor or vendor == "Unknown":
            issues.append("Missing or unknown vendor")
        elif len(vendor) < 2:
            issues.append(f"Vendor name too short: '{vendor}'")
        
        # Check amount
        amount = extraction.get("total_amount") or extraction.get("amount")
        if amount is None:
            issues.append("Missing amount")
        elif amount <= 0:
            issues.append(f"Invalid amount: {amount}")
        elif amount > 1000000:
            issues.append(f"Unusually large amount: ${amount:,.2f}")
        
        # Check currency
        currency = extraction.get("currency", "USD")
        valid_currencies = ["USD", "EUR", "GBP", "CAD", "AUD", "CHF", "JPY", "NGN", "KES", "ZAR"]
        if currency not in valid_currencies:
            issues.append(f"Unusual currency: {currency}")
        
        return issues
    
    def _verify_amount(
        self,
        extraction: Dict[str, Any],
        original_text: str,
    ) -> Dict[str, Any]:
        """Cross-check extracted amount against source text."""
        import re
        
        extracted_amount = extraction.get("total_amount") or extraction.get("amount")
        if extracted_amount is None:
            return {"issue": None, "correction": None}
        
        # Find all amounts in the text
        amount_patterns = [
            r'\$[\d,]+\.?\d*',  # $1,234.56
            r'USD\s*[\d,]+\.?\d*',  # USD 1234.56
            r'[\d,]+\.?\d*\s*(?:USD|dollars)',  # 1234.56 USD
            r'(?:Total|Amount|Due|Balance)[:\s]*\$?[\d,]+\.?\d*',  # Total: $1234
        ]
        
        found_amounts = []
        for pattern in amount_patterns:
            matches = re.findall(pattern, original_text, re.IGNORECASE)
            for match in matches:
                # Extract numeric value
                numeric = re.sub(r'[^\d.]', '', match)
                try:
                    found_amounts.append(float(numeric))
                except:
                    pass
        
        if not found_amounts:
            return {"issue": "Could not verify amount - no amounts found in text", "correction": None}
        
        # Check if extracted amount matches any found amount
        for found in found_amounts:
            if abs(found - extracted_amount) < 0.01:  # Within 1 cent
                return {"issue": None, "correction": None}
        
        # Check if there's a significantly different amount that appears more prominent
        # (e.g., appears after "Total:" or is the largest)
        max_found = max(found_amounts)
        if abs(max_found - extracted_amount) > 1.0:
            return {
                "issue": f"Extracted ${extracted_amount:,.2f} but found ${max_found:,.2f} in text",
                "correction": {
                    "field": "total_amount",
                    "old_value": extracted_amount,
                    "new_value": max_found,
                    "reason": "Amount mismatch - using larger amount found in text"
                }
            }
        
        return {"issue": None, "correction": None}
    
    def _verify_vendor(
        self,
        extraction: Dict[str, Any],
        original_text: str,
    ) -> Dict[str, Any]:
        """Verify vendor name against source text."""
        vendor = extraction.get("vendor", "")
        if not vendor or vendor == "Unknown":
            return {"issue": "No vendor to verify", "correction": None}
        
        # Check if vendor name appears in text
        if vendor.lower() in original_text.lower():
            return {"issue": None, "correction": None}
        
        # Check for partial matches (company might be "Stripe Inc" but text says "Stripe")
        vendor_parts = vendor.split()
        for part in vendor_parts:
            if len(part) > 3 and part.lower() in original_text.lower():
                return {"issue": None, "correction": None}
        
        return {
            "issue": f"Vendor '{vendor}' not found in source text",
            "correction": None  # Don't auto-correct vendor - too risky
        }
    
    def _check_inconsistencies(self, extraction: Dict[str, Any]) -> List[str]:
        """Check for internal inconsistencies in the extraction."""
        issues = []
        
        # Check if line items sum to total
        line_items = extraction.get("line_items", [])
        total = extraction.get("total_amount") or extraction.get("amount")
        
        if line_items and total:
            line_sum = sum(item.get("amount", 0) for item in line_items)
            if line_sum > 0 and abs(line_sum - total) > 1.0:
                issues.append(f"Line items sum (${line_sum:,.2f}) doesn't match total (${total:,.2f})")
        
        # Check date consistency
        invoice_date = extraction.get("invoice_date")
        due_date = extraction.get("due_date")
        
        if invoice_date and due_date:
            try:
                from datetime import datetime
                inv_dt = datetime.strptime(invoice_date, "%Y-%m-%d")
                due_dt = datetime.strptime(due_date, "%Y-%m-%d")
                
                if due_dt < inv_dt:
                    issues.append(f"Due date ({due_date}) is before invoice date ({invoice_date})")
                
                days_diff = (due_dt - inv_dt).days
                if days_diff > 365:
                    issues.append(f"Due date is {days_diff} days after invoice date - unusually long")
            except:
                pass
        
        return issues
    
    def _llm_reflect(
        self,
        extraction: Dict[str, Any],
        original_text: str,
    ) -> Optional[Dict[str, Any]]:
        """Use LLM to reflect on the extraction."""
        try:
            prompt = f"""Review this invoice extraction for potential errors.

EXTRACTED DATA:
- Vendor: {extraction.get('vendor', 'Unknown')}
- Amount: {extraction.get('total_amount') or extraction.get('amount', 'Unknown')}
- Currency: {extraction.get('currency', 'USD')}
- Invoice #: {extraction.get('invoice_number', 'N/A')}
- Due Date: {extraction.get('due_date', 'N/A')}

ORIGINAL TEXT (first 1000 chars):
{original_text[:1000]}

TASK: Identify any potential errors in the extraction.
Return JSON:
{{
  "notes": ["observation 1", "observation 2"],
  "corrections": [
    {{"field": "field_name", "old_value": "x", "new_value": "y", "reason": "why"}}
  ],
  "confidence_assessment": "high|medium|low"
}}

Only include corrections if you're confident there's an error."""

            result = self.llm.generate_json(prompt)
            return result
            
        except Exception as e:
            logger.warning(f"LLM reflection failed: {e}")
            return None
    
    def _apply_corrections(
        self,
        extraction: Dict[str, Any],
        corrections: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Apply corrections to create final extraction."""
        final = extraction.copy()
        
        for correction in corrections:
            field = correction.get("field")
            new_value = correction.get("new_value")
            if field and new_value is not None:
                final[field] = new_value
                logger.info(f"Applied correction: {field} = {new_value}")
        
        return final


# Convenience function
def get_agent_reflection() -> AgentReflection:
    """Get an agent reflection instance."""
    return AgentReflection()
