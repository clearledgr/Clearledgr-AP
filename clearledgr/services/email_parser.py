"""
Clearledgr Email Parser Service

Parses finance-related emails and attachments:
- Invoice extraction from PDFs (with table support via pdfplumber)
- OCR for scanned documents and images (pytesseract)
- Payment confirmation parsing
- Bank statement detection
- Vendor context extraction with fuzzy matching
"""

import re
import json
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime
from decimal import Decimal
import base64
import io
import logging

logger = logging.getLogger(__name__)

# Optional imports for enhanced extraction
try:
    import pytesseract
    from PIL import Image
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False
    logger.warning("pytesseract not available - OCR disabled. Install with: pip install pytesseract pillow")

try:
    import pdfplumber
    PDFPLUMBER_AVAILABLE = True
except ImportError:
    PDFPLUMBER_AVAILABLE = False
    logger.warning("pdfplumber not available - table extraction disabled. Install with: pip install pdfplumber")

try:
    import docx
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False
    logger.warning("python-docx not available - DOCX parsing disabled. Install with: pip install python-docx")

try:
    from rapidfuzz import fuzz, process
    FUZZY_AVAILABLE = True
except ImportError:
    FUZZY_AVAILABLE = False
    logger.warning("rapidfuzz not available - fuzzy matching disabled. Install with: pip install rapidfuzz")


# Common vendor names for fuzzy matching
KNOWN_VENDORS = [
    "Amazon", "Amazon Web Services", "AWS", "Microsoft", "Google", "Apple",
    "Stripe", "PayPal", "Shopify", "Salesforce", "HubSpot", "Slack", "Zoom",
    "Adobe", "Atlassian", "Dropbox", "GitHub", "Notion", "Figma", "Canva",
    "QuickBooks", "Xero", "FreshBooks", "Wave", "Gusto", "Deel", "Remote",
    "Office Depot", "Staples", "FedEx", "UPS", "DHL", "USPS",
    "Uber", "Lyft", "Delta", "United", "American Airlines", "Southwest",
    "Hilton", "Marriott", "Airbnb", "WeWork", "Regus",
    "Verizon", "AT&T", "T-Mobile", "Comcast", "Spectrum",
    "PG&E", "ConEd", "Duke Energy", "National Grid",
    "Bank of America", "Chase", "Wells Fargo", "Citi", "Capital One",
]


class EmailParser:
    """
    Parses email content and attachments to extract financial data.
    """
    
    # Comprehensive patterns for financial emails (international support)
    AMOUNT_PATTERNS = [
        # Currency symbols with amounts
        r'(?:€|EUR)\s*([\d\s.,]+)',  # EUR format
        r'(?:\$|USD)\s*([\d\s.,]+)',  # USD format
        r'(?:£|GBP)\s*([\d\s.,]+)',  # GBP format
        r'(?:₦|NGN)\s*([\d\s.,]+)',  # Nigerian Naira
        r'(?:\bZAR\b|\bR(?=\s*\d))\s*([\d\s.,]+)',  # South African Rand
        r'(?:KES|KSh)\s*([\d\s.,]+)',  # Kenyan Shilling
        r'(?:¥|JPY|CNY)\s*([\d\s.,]+)',  # Japanese Yen / Chinese Yuan
        r'(?:₹|INR)\s*([\d\s.,]+)',  # Indian Rupee
        r'(?:CHF)\s*([\d\s.,]+)',  # Swiss Franc
        r'(?:AUD|A\$)\s*([\d\s.,]+)',  # Australian Dollar
        r'(?:CAD|C\$)\s*([\d\s.,]+)',  # Canadian Dollar
        r'(?:SEK|kr)\s*([\d\s.,]+)',  # Swedish Krona
        r'(?:NOK)\s*([\d\s.,]+)',  # Norwegian Krone
        r'(?:DKK)\s*([\d\s.,]+)',  # Danish Krone
        r'(?:PLN|zł)\s*([\d\s.,]+)',  # Polish Zloty
        r'(?:BRL|R\$)\s*([\d\s.,]+)',  # Brazilian Real
        r'(?:MXN)\s*([\d\s.,]+)',  # Mexican Peso
        r'(?:AED)\s*([\d\s.,]+)',  # UAE Dirham
        r'(?:SAR)\s*([\d\s.,]+)',  # Saudi Riyal
        # Amount labels (more comprehensive)
        r'Total\s*(?:Amount|Due|Payable)?[:\s]+(?:€|\$|£|₦|R|KES|¥|₹)?\s*([\d\s.,]+)',
        r'Amount\s*(?:Due|Payable)?[:\s]+(?:€|\$|£|₦|R|KES|¥|₹)?\s*([\d\s.,]+)',
        r'Net\s*(?:Amount|Total)?[:\s]+(?:€|\$|£|₦|R|KES|¥|₹)?\s*([\d\s.,]+)',
        r'Grand\s+Total[:\s]+(?:€|\$|£|₦|R|KES|¥|₹)?\s*([\d\s.,]+)',
        r'Balance\s*(?:Due)?[:\s]+(?:€|\$|£|₦|R|KES|¥|₹)?\s*([\d\s.,]+)',
        r'Subtotal[:\s]+(?:€|\$|£|₦|R|KES|¥|₹)?\s*([\d\s.,]+)',
        r'Invoice\s+Total[:\s]+(?:€|\$|£|₦|R|KES|¥|₹)?\s*([\d\s.,]+)',
        r'Pay\s+This\s+Amount[:\s]+(?:€|\$|£|₦|R|KES|¥|₹)?\s*([\d\s.,]+)',
    ]
    
    INVOICE_PATTERNS = [
        r'Invoice\s*(?:Number|No\.?|#)[:\s]*([A-Z0-9][\w\-/]{2,})',
        r'Invoice[:\s#-]+([A-Z0-9][A-Z0-9\-/]{3,})',
        r'INV[:\-\s#]*([A-Z0-9][\w\-]{2,})',
        r'Bill\s*(?:Number|No\.?|#)[:\s]*([A-Z0-9][\w\-/]{2,})',
        r'Reference\s*(?:Number|No\.?|#)[:\s]*([A-Z0-9][\w\-/]{2,})',
        r'Order\s*(?:Number|No\.?|#)[:\s]*([A-Z0-9][\w\-/]{2,})',
        r'PO\s*(?:Number|No\.?|#)[:\s]*([A-Z0-9][\w\-/]{2,})',
        r'Receipt\s*(?:Number|No\.?|#)[:\s]*([A-Z0-9][\w\-/]{2,})',
        r'Transaction\s*(?:ID|Number|No\.?|#)[:\s]*([A-Z0-9][\w\-/]{2,})',
        r'Doc(?:ument)?\s*(?:Number|No\.?|#)[:\s]*([A-Z0-9][\w\-/]{2,})',
    ]
    
    DATE_PATTERNS = [
        # ISO format
        r'(\d{4}-\d{2}-\d{2})',
        r'(\d{4}/\d{2}/\d{2})',
        # European formats (DD/MM/YYYY, DD-MM-YYYY, DD.MM.YYYY)
        r'(\d{1,2}/\d{1,2}/\d{4})',
        r'(\d{1,2}-\d{1,2}-\d{4})',
        r'(\d{1,2}\.\d{1,2}\.\d{4})',
        # US format (MM/DD/YYYY)
        r'(\d{1,2}/\d{1,2}/\d{2,4})',
        # Written dates
        r'(\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{4})',
        r'((?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},?\s+\d{4})',
        # With labels
        r'(?:Due|Date|Invoice\s+Date|Issue\s+Date)[:\s]+(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4})',
        r'(?:Due|Date|Invoice\s+Date|Issue\s+Date)[:\s]+(\d{1,2}\s+\w+\s+\d{4})',
    ]
    
    PAYMENT_REQUEST_KEYWORDS = [
        'payment request', 'please pay', 'requesting payment',
        'reimburse', 'reimbursement', 'expense report',
        'wire to', 'transfer to', 'pay to', 'contractor payment'
    ]
    
    INVOICE_KEYWORDS = [
        'invoice', 'bill', 'amount due', 'balance due',
        'total due', 'payable', 'payment terms', 'due date', 'invoice number'
    ]
    
    def __init__(self):
        self.supported_currencies = [
            'EUR', 'USD', 'GBP', 'NGN', 'ZAR', 'KES',
            'JPY', 'CNY', 'INR', 'CHF', 'AUD', 'CAD',
            'SEK', 'NOK', 'DKK', 'PLN', 'BRL', 'MXN',
            'AED', 'SAR', 'SGD', 'HKD', 'NZD', 'THB',
        ]
        self.known_vendors = KNOWN_VENDORS.copy()
    
    def parse_email(
        self,
        subject: str,
        body: str,
        sender: str,
        attachments: List[Dict] = None
    ) -> Dict[str, Any]:
        """
        Parse an email and extract financial data.
        
        Args:
            subject: Email subject line
            body: Email body text
            sender: Sender email address
            attachments: List of attachments [{name, content_type, content_base64}]
            
        Returns:
            Parsed email data with extracted fields
        """
        attachments = attachments or []
        
        # Determine email type
        email_type = self._classify_email(subject, body)
        
        # Extract vendor from sender
        vendor = self._extract_vendor(sender)
        
        # Extract amounts
        amounts = self._extract_amounts(subject + " " + body)
        
        # Extract invoice numbers
        invoice_numbers = self._extract_invoice_numbers(subject + " " + body)
        
        # Extract dates
        dates = self._extract_dates(subject + " " + body)
        
        # Parse attachments
        parsed_attachments = []
        for attachment in attachments:
            parsed = self._parse_attachment(attachment)
            if parsed:
                parsed_attachments.append(parsed)

            attachment_text = attachment.get("content_text")
            if attachment_text:
                parsed_text = self.parse_invoice_text(attachment_text)
                if parsed_text:
                    attachment_name = attachment.get("name") or attachment.get("filename")
                    attachment_type = attachment.get("content_type") or attachment.get("mime_type")
                    parsed_attachments.append({
                        "name": attachment_name,
                        "type": parsed_text.get("type") or (parsed.get("type") if parsed else "document"),
                        "content_type": attachment_type,
                        "parsed": True,
                        "extraction": parsed_text
                    })

                    if parsed_text.get("amount"):
                        parsed_amount = parsed_text.get("amount")
                        if isinstance(parsed_amount, dict):
                            amounts = [parsed_amount]
                        else:
                            amounts = [{
                                "value": parsed_amount,
                                "raw": str(parsed_amount),
                                "currency": parsed_text.get("currency") or self._detect_currency(attachment_text),
                            }]
                    if parsed_text.get("invoice_number"):
                        invoice_numbers = [parsed_text.get("invoice_number")]
                    if parsed_text.get("date"):
                        dates = [parsed_text.get("date")]
                    if not vendor and parsed_text.get("vendor"):
                        vendor = parsed_text.get("vendor")
        
        # Merge attachment data with email data
        if parsed_attachments:
            # Use attachment data if more complete
            for att in parsed_attachments:
                if att.get('amounts') and not amounts:
                    amounts = att['amounts']
                if att.get('invoice_numbers') and not invoice_numbers:
                    invoice_numbers = att['invoice_numbers']

        if invoice_numbers and amounts:
            amounts = self._filter_amounts_against_invoice_numbers(amounts, invoice_numbers)
        
        primary_amount = None
        primary_currency = None
        if amounts:
            if isinstance(amounts[0], dict):
                primary_amount = amounts[0].get("value")
                primary_currency = amounts[0].get("currency")
            else:
                primary_amount = amounts[0]

        return {
            "email_type": email_type,
            "vendor": vendor,
            "sender": sender,
            "subject": subject,
            "amounts": amounts,
            "primary_amount": primary_amount,
            "invoice_numbers": invoice_numbers,
            "primary_invoice": invoice_numbers[0] if invoice_numbers else None,
            "dates": dates,
            "primary_date": dates[0] if dates else None,
            "attachments": parsed_attachments,
            "has_invoice_attachment": any(a.get('type') == 'invoice' for a in parsed_attachments),
            "has_statement_attachment": any(a.get('type') == 'statement' for a in parsed_attachments),
            "confidence": self._calculate_confidence(email_type, amounts, invoice_numbers),
            "currency": primary_currency,
            "parsed_at": datetime.utcnow().isoformat()
        }
    
    def parse_invoice_text(self, text: str) -> Dict[str, Any]:
        """
        Parse invoice text (from PDF extraction or OCR).
        
        Args:
            text: Extracted text from invoice
            
        Returns:
            Parsed invoice data
        """
        amounts = self._extract_amounts(text)
        invoice_numbers = self._extract_invoice_numbers(text)
        dates = self._extract_dates(text)
        
        # Try to extract line items
        line_items = self._extract_line_items(text)
        
        # Extract vendor name (usually at top of invoice)
        vendor = self._extract_vendor_from_text(text)
        
        return {
            "type": "invoice",
            "vendor": vendor,
            "invoice_number": invoice_numbers[0] if invoice_numbers else None,
            "amount": amounts[0] if amounts else None,
            "all_amounts": amounts,
            "date": dates[0] if dates else None,
            "due_date": self._extract_due_date(text),
            "line_items": line_items,
            "currency": self._detect_currency(text),
            "parsed_at": datetime.utcnow().isoformat()
        }
    
    def parse_payment_confirmation(self, text: str) -> Dict[str, Any]:
        """
        Parse payment confirmation email.
        
        Args:
            text: Email body text
            
        Returns:
            Parsed payment data
        """
        amounts = self._extract_amounts(text)
        
        # Extract transaction ID
        txn_patterns = [
            r'Transaction\s*(?:ID|#|Number)?[:\s]+([A-Z0-9\-_]+)',
            r'Reference[:\s]+([A-Z0-9\-_]+)',
            r'Confirmation[:\s]+([A-Z0-9\-_]+)',
            r'TXN[:\-\s]*([A-Z0-9\-]+)',
        ]
        
        txn_id = None
        for pattern in txn_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                txn_id = match.group(1)
                break
        
        # Extract payer/payee
        payer = self._extract_party(text, 'from')
        payee = self._extract_party(text, 'to')
        
        return {
            "type": "payment_confirmation",
            "transaction_id": txn_id,
            "amount": amounts[0] if amounts else None,
            "currency": self._detect_currency(text),
            "payer": payer,
            "payee": payee,
            "date": self._extract_dates(text)[0] if self._extract_dates(text) else None,
            "status": "completed",
            "parsed_at": datetime.utcnow().isoformat()
        }
    
    def _classify_email(self, subject: str, body: str) -> str:
        """Classify email type based on content."""
        text = (subject + " " + body).lower()

        if any(kw in text for kw in self.INVOICE_KEYWORDS):
            return "invoice"

        if any(kw in text for kw in self.PAYMENT_REQUEST_KEYWORDS):
            return "payment_request"

        return "general"
    
    def _extract_vendor(self, sender: str) -> str:
        """Extract vendor name from sender email with fuzzy matching."""
        if '@' in sender:
            domain = sender.split('@')[1]
            # Remove common TLDs and clean up
            name = domain.split('.')[0]
            capitalized = name.title()
            
            # Try fuzzy match against known vendors
            if FUZZY_AVAILABLE and self.known_vendors:
                match = process.extractOne(
                    capitalized, 
                    self.known_vendors,
                    scorer=fuzz.ratio,
                    score_cutoff=70
                )
                if match:
                    return match[0]  # Return standardized vendor name
            
            return capitalized
        return sender
    
    def _extract_vendor_from_text(self, text: str) -> Optional[str]:
        """Extract vendor name from invoice text with fuzzy matching."""
        candidates = []
        
        # Look for company name patterns at start of text
        lines = text.split('\n')[:15]  # First 15 lines
        
        for line in lines:
            line = line.strip()
            # Skip short lines or lines with numbers
            if len(line) < 3 or len(line) > 60:
                continue
            if re.search(r'\d{4}', line):  # Skip lines with years/dates
                continue
            if any(kw in line.lower() for kw in ['invoice', 'bill', 'date', 'to:', 'from:', 'page', 'total']):
                continue
            # Likely a company name
            if line and line[0].isupper():
                candidates.append(line)
        
        if not candidates:
            return None
        
        # Try fuzzy match against known vendors
        if FUZZY_AVAILABLE and self.known_vendors:
            for candidate in candidates:
                match = process.extractOne(
                    candidate,
                    self.known_vendors,
                    scorer=fuzz.partial_ratio,
                    score_cutoff=75
                )
                if match:
                    return match[0]  # Return standardized vendor name
        
        # Return first candidate if no fuzzy match
        return candidates[0] if candidates else None
    
    def add_known_vendor(self, vendor_name: str):
        """Add a vendor to the known vendors list for fuzzy matching."""
        if vendor_name and vendor_name not in self.known_vendors:
            self.known_vendors.append(vendor_name)
    
    def _extract_amounts(self, text: str) -> List[Dict[str, Any]]:
        """Extract monetary amounts from text."""
        amounts = []

        for pattern in self.AMOUNT_PATTERNS:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                raw = match.group(1) if match.groups() else match.group(0)
                value = self._parse_amount_value(raw)
                # Keep legitimate zero-value invoices (e.g., $0.00 credit/settled cycles).
                if value is None or value < 0:
                    continue

                context = text[max(0, match.start() - 40):match.end() + 40].lower()
                score = 0
                if re.search(r"(total|amount\s+due|balance\s+due|total\s+due|invoice\s+total|grand\s+total|amount\s+payable|pay\s+this\s+amount)", context):
                    score += 3
                if re.search(r"(subtotal|tax|vat|gst|discount|shipping|fee)", context):
                    score -= 1
                if re.search(r"(usd|eur|gbp|\\$|€|£)", match.group(0), re.IGNORECASE):
                    score += 1

                amounts.append({
                    "value": value,
                    "raw": raw,
                    "currency": self._detect_currency(text),
                    "score": score
                })

        # Remove duplicates, keep the highest-scoring candidate for each value
        unique_map: Dict[float, Dict[str, Any]] = {}
        for a in amounts:
            value = a["value"]
            if value not in unique_map or a.get("score", 0) > unique_map[value].get("score", 0):
                unique_map[value] = a

        unique = list(unique_map.values())
        # Prefer labeled totals, then larger values
        return sorted(unique, key=lambda x: (x.get("score", 0), x["value"]), reverse=True)

    def _parse_amount_value(self, raw: str) -> Optional[float]:
        """
        Parse amount value with international format support.
        Handles: 1,234.56 | 1.234,56 | 1 234,56 | 1234.56
        """
        if raw is None:
            return None
        
        # Remove currency symbols and whitespace
        cleaned = str(raw).strip()
        cleaned = re.sub(r'[€$£₦₹¥฿]', '', cleaned)
        cleaned = cleaned.replace(' ', '').replace('\u00a0', '')  # Remove nbsp
        # Amount patterns can capture trailing punctuation (e.g., "40.23.").
        # Trim non-numeric boundary characters so decimal parsing is stable.
        cleaned = cleaned.strip(".,;:-_()[]{}")
        
        if not cleaned:
            return None

        has_comma = ',' in cleaned
        has_dot = '.' in cleaned
        normalized = cleaned

        if has_comma and has_dot:
            # Determine format based on position
            # 1,234.56 vs 1.234,56
            if cleaned.rfind(',') > cleaned.rfind('.'):
                # European: 1.234,56 -> 1234.56
                normalized = cleaned.replace('.', '').replace(',', '.')
            else:
                # US: 1,234.56 -> 1234.56
                normalized = cleaned.replace(',', '')
        elif has_comma and not has_dot:
            parts = cleaned.split(',')
            # Check if this is decimal (1234,56) or thousand separator (1,234,567)
            if len(parts) == 2 and len(parts[1]) <= 2:
                # Decimal: 1234,56 -> 1234.56
                normalized = parts[0] + '.' + parts[1]
            else:
                # Thousand separator: 1,234,567 -> 1234567
                normalized = cleaned.replace(',', '')
        elif has_dot:
            # Could be decimal or thousand separator
            parts = cleaned.split('.')
            if len(parts) == 2 and len(parts[1]) <= 2:
                # Decimal: 1234.56 (keep as is)
                normalized = cleaned
            elif len(parts) > 2:
                # Thousand separator: 1.234.567 -> 1234567
                normalized = cleaned.replace('.', '')
            else:
                normalized = cleaned
        else:
            normalized = cleaned

        try:
            value = float(normalized)
            # Validate amount is reasonable (not negative, not astronomical)
            if value < 0:
                return None
            if value > 100000000:  # 100 million cap
                return None

            # Filter obvious years (e.g., 2024, 2025) unless currency symbols present.
            raw_str = str(raw)
            has_currency = bool(re.search(r"(USD|EUR|GBP|\\$|€|£)", raw_str, re.IGNORECASE))
            if not has_currency and value.is_integer() and 1900 <= value <= 2100:
                return None

            return value
        except ValueError:
            return None

    def _filter_amounts_against_invoice_numbers(
        self,
        amounts: List[Dict[str, Any]],
        invoice_numbers: List[str]
    ) -> List[Dict[str, Any]]:
        invoice_digits = {
            re.sub(r'\D', '', str(number))
            for number in invoice_numbers
            if number and re.sub(r'\D', '', str(number))
        }
        if not invoice_digits:
            return amounts

        filtered = []
        for amount in amounts:
            raw = amount.get('raw') if isinstance(amount, dict) else amount
            digits = re.sub(r'\D', '', str(raw))
            trimmed = digits.rstrip('0') if digits else digits
            if digits:
                if digits in invoice_digits or trimmed in invoice_digits:
                    continue
                # If the amount digits contain the invoice digits (or vice versa), skip.
                if any(len(inv) >= 4 and (inv in digits or digits in inv) for inv in invoice_digits):
                    continue
            filtered.append(amount)
        return filtered
    
    def _extract_invoice_numbers(self, text: str) -> List[str]:
        """Extract invoice numbers from text."""
        numbers = []
        
        for pattern in self.INVOICE_PATTERNS:
            matches = re.findall(pattern, text, re.IGNORECASE)
            numbers.extend(matches)
        
        # Remove duplicates while preserving order
        banned_tokens = {
            "invoice", "number", "no", "total", "amount", "due",
            "date", "billing", "domain", "summary", "subtotal", "vat"
        }
        seen = set()
        unique = []
        for n in numbers:
            token = str(n).strip().strip(":#.- ")
            if not token:
                continue
            lowered = token.lower()
            if lowered in banned_tokens:
                continue
            # Invoice identifiers should have at least one digit.
            if not re.search(r"\d", token):
                continue
            if token not in seen:
                seen.add(token)
                unique.append(token)
        
        return unique
    
    def _extract_dates(self, text: str) -> List[str]:
        """Extract and validate dates from text."""
        dates = []
        
        for pattern in self.DATE_PATTERNS:
            matches = re.findall(pattern, text, re.IGNORECASE)
            dates.extend(matches)
        
        # Normalize to ISO format with validation
        normalized = []
        date_formats = [
            '%Y-%m-%d',
            '%Y/%m/%d',
            '%d/%m/%Y',
            '%m/%d/%Y',
            '%d-%m-%Y',
            '%m-%d-%Y',
            '%d.%m.%Y',
            '%d %B %Y',
            '%d %b %Y',
            '%B %d, %Y',
            '%B %d %Y',
            '%b %d, %Y',
            '%b %d %Y',
        ]
        
        for d in dates:
            d = d.strip()
            parsed_date = None
            
            for fmt in date_formats:
                try:
                    parsed_date = datetime.strptime(d, fmt)
                    break
                except ValueError:
                    continue
            
            if parsed_date:
                # Validate date is reasonable (not too far in past or future)
                if self._validate_date(parsed_date):
                    normalized.append(parsed_date.strftime('%Y-%m-%d'))
        
        ordered_unique: List[str] = []
        seen = set()
        for date_value in normalized:
            if date_value in seen:
                continue
            seen.add(date_value)
            ordered_unique.append(date_value)

        return ordered_unique
    
    def _validate_date(self, date: datetime) -> bool:
        """Validate that a date is reasonable for a financial document."""
        now = datetime.now()
        
        # Date shouldn't be more than 2 years in the past
        min_date = datetime(now.year - 2, 1, 1)
        
        # Date shouldn't be more than 1 year in the future
        max_date = datetime(now.year + 1, 12, 31)
        
        return min_date <= date <= max_date
    
    def _extract_due_date(self, text: str) -> Optional[str]:
        """Extract due date specifically."""
        patterns = [
            r'Due\s*(?:Date)?[:\s]+(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})',
            r'Payment\s+Due[:\s]+(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})',
            r'Due\s+by[:\s]+(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                dates = self._extract_dates(match.group(1))
                if dates:
                    return dates[0]
        
        return None
    
    def _extract_line_items(self, text: str) -> List[Dict[str, Any]]:
        """
        Extract line items from invoice text.
        Handles both structured tables and free-form text.
        """
        items = []
        
        # Check if text contains table markers (from pdfplumber extraction)
        if '|' in text:
            items.extend(self._extract_line_items_from_table(text))
        
        # Also try regex-based extraction
        items.extend(self._extract_line_items_regex(text))
        
        # Deduplicate by description similarity
        unique_items = []
        seen_descs = set()
        for item in items:
            desc_lower = item['description'].lower()[:30]
            if desc_lower not in seen_descs:
                seen_descs.add(desc_lower)
                unique_items.append(item)
        
        return unique_items[:30]  # Limit to 30 items
    
    def _extract_line_items_from_table(self, text: str) -> List[Dict[str, Any]]:
        """Extract line items from table-formatted text."""
        items = []
        
        # Process lines that contain pipe separators (table rows)
        for line in text.split('\n'):
            if '|' not in line:
                continue
            
            cells = [c.strip() for c in line.split('|')]
            
            # Skip header rows
            if any(h in line.lower() for h in ['description', 'item', 'qty', 'quantity', 'price', 'amount', 'total']):
                continue
            
            # Try to identify description and amount columns
            description = None
            amount = None
            quantity = None
            unit_price = None
            
            for cell in cells:
                if not cell:
                    continue
                
                # Check if cell is a number/amount
                amount_match = re.search(r'^[\$€£₦]?\s*([\d,]+\.?\d*)\s*$', cell)
                if amount_match:
                    val = float(amount_match.group(1).replace(',', ''))
                    if val > 0:
                        if unit_price is None and val < 10000:
                            unit_price = val
                        elif amount is None:
                            amount = val
                        elif val > amount:
                            unit_price = amount
                            amount = val
                elif len(cell) > 3 and not cell.isdigit():
                    # Likely a description
                    if description is None or len(cell) > len(description):
                        description = cell
            
            if description and amount and amount > 0:
                item = {
                    "description": description[:100],
                    "amount": amount
                }
                if quantity:
                    item["quantity"] = quantity
                if unit_price:
                    item["unit_price"] = unit_price
                items.append(item)
        
        return items
    
    def _extract_line_items_regex(self, text: str) -> List[Dict[str, Any]]:
        """Extract line items using regex patterns."""
        items = []
        
        # Pattern for line items: description followed by amount
        patterns = [
            # Description ... Amount
            r'^(.{10,60}?)\s+([\d,]+\.\d{2})\s*$',
            # Quantity x Description @ Price = Amount
            r'^(\d+)\s*[xX×]\s*(.{5,50}?)\s*[@at]\s*[\$€£]?\s*([\d,]+\.?\d*)\s*=?\s*[\$€£]?\s*([\d,]+\.?\d*)',
            # Description (Price)
            r'^([A-Z][^0-9]{5,40})\s+[\$€£]?\s*([\d,]+\.\d{2})',
        ]
        
        lines = text.split('\n')
        for line in lines:
            line = line.strip()
            if not line or len(line) < 10:
                continue
            
            # Skip obvious non-item lines
            if any(kw in line.lower() for kw in ['total', 'subtotal', 'tax', 'shipping', 'discount', 'invoice', 'date', 'due']):
                continue
            
            for pattern in patterns:
                match = re.search(pattern, line, re.IGNORECASE)
                if match:
                    groups = match.groups()
                    if len(groups) >= 2:
                        desc = groups[0].strip() if isinstance(groups[0], str) else str(groups[0])
                        amount_str = groups[-1] if len(groups[-1]) > 0 else groups[-2]
                        
                        try:
                            amount = float(str(amount_str).replace(',', ''))
                            if amount > 0 and len(desc) > 3:
                                items.append({
                                    "description": desc[:100],
                                    "amount": amount
                                })
                                break
                        except (ValueError, TypeError):
                            continue
        
        return items
    
    def _extract_party(self, text: str, direction: str) -> Optional[str]:
        """Extract payer or payee from text."""
        patterns = [
            rf'{direction}[:\s]+([A-Za-z\s]+)',
            rf'(?:Paid|Payment)\s+{direction}[:\s]+([A-Za-z\s]+)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        
        return None
    
    def _detect_currency(self, text: str) -> str:
        """Detect currency from text with comprehensive symbol/code support."""
        text_lower = text.lower()

        # Avoid false positives such as "... for 1 Dec 2025 ...".
        if re.search(r"\bR\s*\d", text):
            return "ZAR"
        
        # Check for currency symbols and codes
        currency_indicators = [
            ('€', 'EUR'),
            ('eur', 'EUR'),
            ('£', 'GBP'),
            ('gbp', 'GBP'),
            ('₦', 'NGN'),
            ('ngn', 'NGN'),
            ('naira', 'NGN'),
            ('zar', 'ZAR'),
            ('rand', 'ZAR'),
            ('kes', 'KES'),
            ('ksh', 'KES'),
            ('shilling', 'KES'),
            ('¥', 'JPY'),
            ('jpy', 'JPY'),
            ('yen', 'JPY'),
            ('cny', 'CNY'),
            ('rmb', 'CNY'),
            ('yuan', 'CNY'),
            ('₹', 'INR'),
            ('inr', 'INR'),
            ('rupee', 'INR'),
            ('rs.', 'INR'),
            ('chf', 'CHF'),
            ('franc', 'CHF'),
            ('a$', 'AUD'),
            ('aud', 'AUD'),
            ('c$', 'CAD'),
            ('cad', 'CAD'),
            ('sek', 'SEK'),
            ('kr', 'SEK'),  # Could be SEK/NOK/DKK
            ('nok', 'NOK'),
            ('dkk', 'DKK'),
            ('pln', 'PLN'),
            ('zł', 'PLN'),
            ('zloty', 'PLN'),
            ('r$', 'BRL'),
            ('brl', 'BRL'),
            ('real', 'BRL'),
            ('mxn', 'MXN'),
            ('peso', 'MXN'),
            ('aed', 'AED'),
            ('dirham', 'AED'),
            ('sar', 'SAR'),
            ('riyal', 'SAR'),
            ('sgd', 'SGD'),
            ('hkd', 'HKD'),
            ('hk$', 'HKD'),
            ('nzd', 'NZD'),
            ('nz$', 'NZD'),
            ('thb', 'THB'),
            ('baht', 'THB'),
            ('฿', 'THB'),
            ('$', 'USD'),
            ('usd', 'USD'),
            ('dollar', 'USD'),
        ]
        
        for indicator, currency in currency_indicators:
            if indicator in text_lower:
                return currency
        
        return 'USD'  # Default to USD as most common
    
    def _parse_attachment(self, attachment: Dict) -> Optional[Dict[str, Any]]:
        """Parse an email attachment."""
        name = (attachment.get('name') or attachment.get('filename') or '').lower()
        content_type = attachment.get('content_type') or attachment.get('mime_type') or ''
        content_base64 = attachment.get('content_base64')
        content_text = attachment.get('content_text')
        
        # Determine attachment type
        if 'pdf' in content_type or name.endswith('.pdf'):
            parsed_text = None
            if content_text:
                parsed_text = content_text
            elif content_base64:
                parsed_text = self._extract_pdf_text(content_base64)

            parsed_invoice = None
            if parsed_text:
                parsed_invoice = self.parse_invoice_text(parsed_text)

            return {
                "name": attachment.get('name') or attachment.get('filename'),
                "type": "invoice" if 'invoice' in name else "document",
                "content_type": "application/pdf",
                "requires_ocr": False if parsed_text else True,
                "parsed": bool(parsed_text),
                "content_text": parsed_text,
                "extraction": parsed_invoice
            }
        elif 'word' in content_type or name.endswith('.docx'):
            parsed_text = None
            if content_text:
                parsed_text = content_text
            elif content_base64:
                parsed_text = self._extract_docx_text(content_base64)

            parsed_invoice = None
            if parsed_text:
                parsed_invoice = self.parse_invoice_text(parsed_text)

            return {
                "name": attachment.get('name') or attachment.get('filename'),
                "type": "invoice" if 'invoice' in name else "document",
                "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "requires_ocr": False,
                "parsed": bool(parsed_text),
                "content_text": parsed_text,
                "extraction": parsed_invoice
            }
        elif 'csv' in content_type or name.endswith('.csv'):
            return {
                "name": attachment.get('name') or attachment.get('filename'),
                "type": "statement" if 'statement' in name else "data",
                "content_type": "text/csv",
                "requires_ocr": False,
                "parsed": False
            }
        elif 'excel' in content_type or name.endswith(('.xlsx', '.xls')):
            return {
                "name": attachment.get('name') or attachment.get('filename'),
                "type": "spreadsheet",
                "content_type": "application/excel",
                "requires_ocr": False,
                "parsed": False
            }
        elif 'image' in content_type or name.endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp', '.tiff', '.bmp')):
            # Attempt OCR extraction
            ocr_text = None
            parsed_invoice = None
            
            if content_base64 and OCR_AVAILABLE:
                ocr_text = self._extract_image_text_ocr(content_base64)
                if ocr_text:
                    parsed_invoice = self.parse_invoice_text(ocr_text)
            
            return {
                "name": attachment.get('name') or attachment.get('filename'),
                "type": "invoice" if 'invoice' in name else "document",
                "content_type": content_type,
                "requires_ocr": not bool(ocr_text),
                "parsed": bool(ocr_text),
                "content_text": ocr_text,
                "extraction": parsed_invoice
            }
        
        return None
    
    def _extract_image_text_ocr(self, content_base64: str) -> Optional[str]:
        """
        Extract text from an image using OCR (pytesseract).
        
        Args:
            content_base64: Base64-encoded image content
            
        Returns:
            Extracted text or None if OCR fails
        """
        if not OCR_AVAILABLE:
            logger.warning("OCR not available - pytesseract not installed")
            return None
        
        try:
            # Decode base64 image
            image_data = base64.b64decode(content_base64)
            image = Image.open(io.BytesIO(image_data))
            
            # Convert to RGB if necessary (for PNG with transparency)
            if image.mode in ('RGBA', 'LA', 'P'):
                background = Image.new('RGB', image.size, (255, 255, 255))
                if image.mode == 'P':
                    image = image.convert('RGBA')
                background.paste(image, mask=image.split()[-1] if 'A' in image.mode else None)
                image = background
            
            # Preprocess image for better OCR
            # Convert to grayscale
            if image.mode != 'L':
                gray = image.convert('L')
            else:
                gray = image
            
            # Increase contrast
            from PIL import ImageEnhance
            enhancer = ImageEnhance.Contrast(gray)
            enhanced = enhancer.enhance(1.5)
            
            # Run OCR with custom config for invoices
            custom_config = r'--oem 3 --psm 6'
            text = pytesseract.image_to_string(enhanced, config=custom_config)
            
            if text and len(text.strip()) > 20:  # Minimum viable text
                logger.info(f"OCR extracted {len(text)} characters from image")
                return text.strip()
            
            return None
        except Exception as e:
            logger.warning(f"OCR extraction failed: {e}")
            return None

    def _extract_docx_text(self, content_base64: str) -> Optional[str]:
        """
        Extract text from a DOCX attachment.
        """
        if not DOCX_AVAILABLE:
            return None

        try:
            raw = base64.b64decode(content_base64)
            document = docx.Document(io.BytesIO(raw))
            paragraphs = [p.text for p in document.paragraphs if p.text]
            text = "\n".join(paragraphs).strip()
            return text or None
        except Exception as e:
            logger.warning(f"DOCX extraction failed: {e}")
            return None
    
    def extract_from_image(self, image_path: str) -> Optional[Dict[str, Any]]:
        """
        Extract invoice data from an image file.
        
        Args:
            image_path: Path to the image file
            
        Returns:
            Parsed invoice data or None
        """
        if not OCR_AVAILABLE:
            return None
        
        try:
            with open(image_path, 'rb') as f:
                content = base64.b64encode(f.read()).decode()
            
            text = self._extract_image_text_ocr(content)
            if text:
                return self.parse_invoice_text(text)
            
            return None
        except Exception as e:
            logger.warning(f"Failed to extract from image {image_path}: {e}")
            return None

    def _extract_pdf_text(self, content_base64: str, max_pages: int = None) -> Optional[str]:
        """
        Extract text from a base64-encoded PDF attachment.
        Uses pdfplumber for better table extraction if available,
        falls back to PyPDF2.
        
        Args:
            content_base64: Base64-encoded PDF content
            max_pages: Maximum pages to process (None = all pages)
        """
        try:
            data = base64.b64decode(content_base64)
        except Exception as e:
            logger.warning(f"Failed to decode PDF base64: {e}")
            return None
        
        # Try pdfplumber first (better table extraction)
        if PDFPLUMBER_AVAILABLE:
            try:
                text = self._extract_with_pdfplumber(data, max_pages)
                if text:
                    return text
            except Exception as e:
                logger.warning(f"pdfplumber extraction failed: {e}")
        
        # Fall back to PyPDF2
        try:
            import PyPDF2
            reader = PyPDF2.PdfReader(io.BytesIO(data))
            total_pages = len(reader.pages)
            pages_to_read = total_pages if max_pages is None else min(total_pages, max_pages)
            
            text_parts = []
            for i in range(pages_to_read):
                page = reader.pages[i]
                extracted = page.extract_text() or ""
                text_parts.append(extracted)
            
            text = "\n".join(text_parts).strip()
            return text or None
        except Exception as e:
            logger.warning(f"PyPDF2 extraction failed: {e}")
            return None
    
    def _extract_with_pdfplumber(self, pdf_data: bytes, max_pages: int = None) -> Optional[str]:
        """Extract text and tables from PDF using pdfplumber."""
        if not PDFPLUMBER_AVAILABLE:
            return None
        
        try:
            with pdfplumber.open(io.BytesIO(pdf_data)) as pdf:
                total_pages = len(pdf.pages)
                pages_to_read = total_pages if max_pages is None else min(total_pages, max_pages)
                
                all_text = []
                
                for i in range(pages_to_read):
                    page = pdf.pages[i]
                    
                    # Extract tables first
                    tables = page.extract_tables()
                    table_text = []
                    for table in tables:
                        if table:
                            for row in table:
                                if row:
                                    row_text = " | ".join(str(cell or '') for cell in row)
                                    table_text.append(row_text)
                    
                    # Extract regular text
                    page_text = page.extract_text() or ""
                    
                    # Combine table text and page text
                    if table_text:
                        all_text.append(f"--- Page {i+1} Tables ---")
                        all_text.extend(table_text)
                    all_text.append(f"--- Page {i+1} Text ---")
                    all_text.append(page_text)
                
                return "\n".join(all_text).strip() or None
        except Exception as e:
            logger.warning(f"pdfplumber failed: {e}")
            return None
    
    def _calculate_confidence(
        self,
        email_type: str,
        amounts: List[Dict],
        invoice_numbers: List[str]
    ) -> float:
        """Calculate confidence score for parsed data."""
        score = 0.0
        
        # Base score for AP email type
        if email_type in ['invoice', 'payment_request']:
            score += 0.3
        
        # Score for extracted amounts
        if amounts:
            score += 0.3
            if len(amounts) == 1:  # Single clear amount
                score += 0.1
        
        # Score for invoice numbers
        if invoice_numbers:
            score += 0.2
            if len(invoice_numbers) == 1:  # Single clear invoice
                score += 0.1
        
        return min(score, 1.0)


# Convenience functions

def parse_email(
    subject: str,
    body: str,
    sender: str,
    attachments: List[Dict] = None
) -> Dict[str, Any]:
    """Parse an email and extract financial data."""
    parser = EmailParser()
    return parser.parse_email(subject, body, sender, attachments)


def parse_invoice_text(text: str) -> Dict[str, Any]:
    """Parse invoice text."""
    parser = EmailParser()
    return parser.parse_invoice_text(text)


def parse_payment_confirmation(text: str) -> Dict[str, Any]:
    """Parse payment confirmation."""
    parser = EmailParser()
    return parser.parse_payment_confirmation(text)


def get_parser_capabilities() -> Dict[str, Any]:
    """
    Get the current capabilities of the email parser.
    Useful for checking what features are available.
    """
    return {
        "ocr_available": OCR_AVAILABLE,
        "table_extraction_available": PDFPLUMBER_AVAILABLE,
        "fuzzy_matching_available": FUZZY_AVAILABLE,
        "supported_image_formats": [".png", ".jpg", ".jpeg", ".gif", ".webp", ".tiff", ".bmp"] if OCR_AVAILABLE else [],
        "supported_document_formats": [".pdf", ".csv", ".xlsx", ".xls"],
        "supported_currencies": EmailParser().supported_currencies,
        "known_vendors_count": len(KNOWN_VENDORS),
        "recommendations": _get_recommendations()
    }


def _get_recommendations() -> List[str]:
    """Get recommendations for improving parser capabilities."""
    recommendations = []
    
    if not OCR_AVAILABLE:
        recommendations.append("Install pytesseract and pillow for OCR support: pip install pytesseract pillow")
    
    if not PDFPLUMBER_AVAILABLE:
        recommendations.append("Install pdfplumber for better PDF table extraction: pip install pdfplumber")
    
    if not FUZZY_AVAILABLE:
        recommendations.append("Install rapidfuzz for fuzzy vendor matching: pip install rapidfuzz")
    
    return recommendations


def extract_from_image_file(image_path: str) -> Optional[Dict[str, Any]]:
    """
    Extract invoice data from an image file using OCR.
    
    Args:
        image_path: Path to the image file
        
    Returns:
        Parsed invoice data or None if OCR is not available
    """
    parser = EmailParser()
    return parser.extract_from_image(image_path)
