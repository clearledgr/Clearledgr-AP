"""Multi-modal LLM service with PDF/image invoice extraction.

Supports:
- Claude Vision for PDF and image invoice extraction
- Mistral as fallback for text-only
- Automatic model selection based on content type
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)


class MultiModalLLMService:
    """
    Multi-modal LLM service for invoice extraction.
    
    Uses Claude Vision for PDFs/images, with Mistral fallback for text-only.
    """
    
    def __init__(self) -> None:
        self.anthropic_key = os.getenv("ANTHROPIC_API_KEY")
        self.mistral_key = os.getenv("MISTRAL_API_KEY")
        self.primary = os.getenv("LLM_PRIMARY_PROVIDER", "anthropic").lower()
        self.timeout = int(os.getenv("LLM_TIMEOUT_SECONDS", "60"))  # Longer for vision
        
    @property
    def is_available(self) -> bool:
        """Check if any LLM provider is configured."""
        return bool(self.anthropic_key or self.mistral_key)

    def extract_invoice(
        self, 
        text: str, 
        attachments: Optional[List[Dict[str, Any]]] = None,
        include_line_items: bool = True,
    ) -> Dict[str, Any]:
        """
        Extract invoice data from email text and/or attachments.
        
        Supports:
        - PDF attachments (sent to Claude Vision)
        - Image attachments (PNG, JPEG - sent to Claude Vision)
        - Text-only extraction (uses any available LLM)
        
        Args:
            text: Email body text
            attachments: List of attachments with content_base64 and content_type
            include_line_items: Whether to extract line items (slower but more detailed)
            
        Returns:
            Extracted invoice data with confidence score
        """
        attachments = attachments or []
        
        # Separate visual attachments from text-only
        visual_attachments, text_attachments = self._categorize_attachments(attachments)
        
        # Build the extraction prompt
        prompt = self._build_invoice_extraction_prompt(
            text=text,
            text_attachments=text_attachments,
            has_visual_attachments=bool(visual_attachments),
            include_line_items=include_line_items,
        )
        
        # If we have visual attachments, must use Claude Vision
        if visual_attachments:
            if not self.anthropic_key:
                logger.warning("Visual attachments present but no Anthropic key - falling back to text extraction")
                return self.generate_json(prompt, [])
            return self._call_anthropic_vision(prompt, visual_attachments)
        
        # Text-only - use any available provider
        return self.generate_json(prompt, [])
    
    def extract_invoice_from_pdf(self, pdf_base64: str, filename: str = "invoice.pdf") -> Dict[str, Any]:
        """
        Extract invoice data directly from a PDF.
        
        Args:
            pdf_base64: Base64-encoded PDF content
            filename: Original filename for context
            
        Returns:
            Extracted invoice data
        """
        prompt = self._build_pdf_extraction_prompt(filename)
        
        if not self.anthropic_key:
            raise RuntimeError("PDF extraction requires Anthropic API key for Claude Vision")
        
        return self._call_anthropic_vision(prompt, [{
            "content_base64": pdf_base64,
            "content_type": "application/pdf",
            "filename": filename,
        }])
    
    def extract_invoice_from_image(self, image_base64: str, content_type: str = "image/png") -> Dict[str, Any]:
        """
        Extract invoice data from an image.
        
        Args:
            image_base64: Base64-encoded image
            content_type: MIME type (image/png, image/jpeg, etc.)
            
        Returns:
            Extracted invoice data
        """
        prompt = self._build_image_extraction_prompt()
        
        if not self.anthropic_key:
            raise RuntimeError("Image extraction requires Anthropic API key for Claude Vision")
        
        return self._call_anthropic_vision(prompt, [{
            "content_base64": image_base64,
            "content_type": content_type,
        }])

    def generate_json(self, prompt: str, attachments: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """Generate JSON response from LLM with provider fallback."""
        attachments = attachments or []
        providers = [self.primary, "anthropic", "mistral"]
        tried = set()
        last_error = None

        for provider in providers:
            if provider in tried:
                continue
            tried.add(provider)
            try:
                if provider == "anthropic":
                    if attachments:
                        return self._call_anthropic_vision(prompt, attachments)
                    return self._call_anthropic_text(prompt)
                if provider == "mistral":
                    return self._call_mistral(prompt)
            except Exception as exc:
                logger.warning(f"LLM provider {provider} failed: {exc}")
                last_error = exc
                continue

        if last_error:
            raise last_error
        raise RuntimeError("No LLM provider configured")
    
    def _categorize_attachments(
        self, 
        attachments: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Separate visual (PDF/image) from text-only attachments."""
        visual = []
        text_only = []
        
        for att in attachments:
            content_type = (att.get("content_type") or "").lower()
            filename = (att.get("filename") or att.get("name") or "").lower()
            
            # Visual: PDFs and images
            is_pdf = "pdf" in content_type or filename.endswith(".pdf")
            is_image = content_type.startswith("image/") or any(
                filename.endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".gif", ".webp"]
            )
            
            if (is_pdf or is_image) and att.get("content_base64"):
                visual.append(att)
            elif att.get("content_text"):
                text_only.append(att)
        
        return visual, text_only

    def _build_invoice_extraction_prompt(
        self,
        text: str,
        text_attachments: List[Dict[str, Any]],
        has_visual_attachments: bool,
        include_line_items: bool,
    ) -> str:
        """Build comprehensive invoice extraction prompt."""
        sections = []
        
        if text:
            sections.append(f"EMAIL CONTENT:\n{text}")
        
        for att in text_attachments:
            content = att.get("content_text", "")
            name = att.get("filename") or att.get("name") or "attachment"
            if content:
                sections.append(f"ATTACHMENT ({name}):\n{content}")
        
        content_text = "\n\n---\n\n".join(sections) if sections else ""
        
        visual_instruction = ""
        if has_visual_attachments:
            visual_instruction = """
I'm also providing PDF/image attachments. Please analyze these visually to extract invoice details.
Focus on the invoice document if multiple attachments are present.
"""
        
        line_items_instruction = ""
        if include_line_items:
            line_items_instruction = """
- line_items: Array of line items, each with:
  - description: Item description
  - quantity: Number of units (null if not specified)
  - unit_price: Price per unit (null if not specified)  
  - amount: Line total
"""
        
        return f"""You are an expert invoice data extraction system. Extract all relevant information from the provided invoice.

{visual_instruction}

{content_text if content_text else "Please analyze the attached invoice document."}

Extract and return a JSON object with these fields:

REQUIRED FIELDS:
- vendor: Company/person issuing the invoice (exact name from invoice)
- invoice_number: Invoice/reference number
- invoice_date: Date invoice was issued (ISO format YYYY-MM-DD)
- due_date: Payment due date (ISO format YYYY-MM-DD, null if not specified)
- total_amount: Total amount due (number, not string)
- currency: 3-letter currency code (USD, EUR, GBP, NGN, KES, ZAR, etc.)
{line_items_instruction}
ADDITIONAL FIELDS:
- subtotal: Amount before tax (null if not specified)
- tax_amount: Tax/VAT amount (null if not specified)
- tax_rate: Tax percentage (null if not specified)
- po_number: Purchase order reference (null if not specified)
- payment_terms: Net 30, Due on Receipt, etc. (null if not specified)
- bank_details: Bank account info for payment (null if not specified)
- vendor_address: Vendor's address (null if not specified)
- vendor_tax_id: VAT/Tax ID number (null if not specified)
- confidence: Your confidence in the extraction (0.0 to 1.0)

IMPORTANT:
- Extract EXACT values from the document - don't guess
- Use null for any field not clearly present in the invoice
- For amounts, extract the number only (no currency symbols)
- Confidence should reflect how clearly the data was visible/readable

Return ONLY valid JSON, no explanation."""
    
    def _build_pdf_extraction_prompt(self, filename: str) -> str:
        """Build prompt specifically for PDF invoice extraction."""
        return f"""You are an expert invoice data extraction system. 

I'm providing a PDF invoice document ({filename}). Please analyze it visually and extract all invoice information.

Extract and return a JSON object with these fields:

REQUIRED:
- vendor: Company name issuing the invoice
- invoice_number: Invoice/reference number  
- invoice_date: Invoice date (YYYY-MM-DD)
- due_date: Due date (YYYY-MM-DD or null)
- total_amount: Total due (number)
- currency: Currency code (USD, EUR, GBP, NGN, KES, ZAR, etc.)
- line_items: Array of items with description, quantity, unit_price, amount

OPTIONAL:
- subtotal: Pre-tax amount
- tax_amount: Tax/VAT amount
- tax_rate: Tax percentage
- po_number: PO reference
- payment_terms: Payment terms
- vendor_address: Vendor address
- vendor_tax_id: VAT/Tax ID
- confidence: 0.0-1.0

Return ONLY valid JSON."""
    
    def _build_image_extraction_prompt(self) -> str:
        """Build prompt for image invoice extraction."""
        return """You are an expert invoice data extraction system.

I'm providing an image of an invoice. Please analyze it and extract all visible information.

Return a JSON object with:
- vendor: Company name
- invoice_number: Invoice number
- invoice_date: Date (YYYY-MM-DD)
- due_date: Due date (YYYY-MM-DD or null)
- total_amount: Total (number)
- currency: Currency code
- line_items: Array of {description, quantity, unit_price, amount}
- subtotal, tax_amount, tax_rate, po_number, payment_terms (if visible)
- confidence: 0.0-1.0

Return ONLY valid JSON."""

    def _call_anthropic_vision(self, prompt: str, attachments: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Call Claude with vision capability for PDFs and images."""
        if not self.anthropic_key:
            raise RuntimeError("Anthropic key not configured")

        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": self.anthropic_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        # Build content blocks with text prompt first
        content_blocks: List[Dict[str, Any]] = []
        
        # Add visual attachments
        for attachment in attachments:
            base64_content = attachment.get("content_base64")
            if not base64_content:
                continue
                
            content_type = attachment.get("content_type") or "application/octet-stream"
            
            # Claude supports both images and PDFs directly
            if "pdf" in content_type.lower():
                content_blocks.append({
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": base64_content,
                    },
                })
            elif content_type.startswith("image/"):
                content_blocks.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": content_type,
                        "data": base64_content,
                    },
                })
        
        # Add text prompt after images
        content_blocks.append({"type": "text", "text": prompt})

        payload = {
            "model": os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
            "max_tokens": 2000,  # More tokens for detailed extraction
            "temperature": 0.1,  # Lower temp for accuracy
            "messages": [{"role": "user", "content": content_blocks}],
        }

        logger.info(f"Calling Claude Vision with {len(attachments)} attachments")
        response = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        text = _extract_message_text(data)
        result = _parse_llm_json(text)
        result["provider"] = "anthropic"
        result["method"] = "vision"
        logger.info(f"Claude Vision extraction complete: vendor={result.get('vendor')}, amount={result.get('total_amount')}")
        return result
    
    def _call_anthropic_text(self, prompt: str) -> Dict[str, Any]:
        """Call Claude for text-only prompts."""
        if not self.anthropic_key:
            raise RuntimeError("Anthropic key not configured")

        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": self.anthropic_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        payload = {
            "model": os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
            "max_tokens": 1500,
            "temperature": 0.2,
            "messages": [{"role": "user", "content": prompt}],
        }

        response = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        text = _extract_message_text(data)
        result = _parse_llm_json(text)
        result["provider"] = "anthropic"
        result["method"] = "text"
        return result

    def _call_mistral(self, prompt: str) -> Dict[str, Any]:
        """Call Mistral for text-only prompts."""
        if not self.mistral_key:
            raise RuntimeError("Mistral key not configured")

        url = "https://api.mistral.ai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.mistral_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": os.getenv("MISTRAL_MODEL", "mistral-large-latest"),
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 1500,
        }
        response = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        text = data["choices"][0]["message"]["content"]
        result = _parse_llm_json(text)
        result["provider"] = "mistral"
        result["method"] = "text"
        return result


def _extract_message_text(data: Dict[str, Any]) -> str:
    content = data.get("content", [])
    if isinstance(content, list):
        parts = [c.get("text", "") for c in content if isinstance(c, dict)]
        return "\n".join([p for p in parts if p])
    return str(content or "")


def _parse_llm_json(text: str) -> Dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        raise RuntimeError("LLM response was not valid JSON")
