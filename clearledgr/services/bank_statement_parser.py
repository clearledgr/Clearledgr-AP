"""
Bank Statement Parser

Extracts transactions from bank statement PDFs and CSVs.
Supports multiple formats common in Europe and Africa.

This is the entry point for data into Clearledgr.
"""

import csv
import re
import io
from typing import List, Dict, Any, Optional
from datetime import datetime
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class ParsedTransaction:
    """A transaction extracted from a bank statement."""
    date: str  # ISO format: YYYY-MM-DD
    amount: float
    currency: str
    description: str
    reference: Optional[str] = None
    balance: Optional[float] = None
    transaction_type: Optional[str] = None  # debit/credit
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "date": self.date,
            "amount": self.amount,
            "currency": self.currency,
            "description": self.description,
            "reference": self.reference,
            "balance": self.balance,
            "transaction_type": self.transaction_type,
        }


class BankStatementParser:
    """
    Parses bank statement files into structured transactions.
    
    Supports:
    - CSV files (various formats)
    - PDF files (via text extraction)
    - Multiple date formats (EU: DD/MM/YYYY, US: MM/DD/YYYY)
    - Multiple currencies (EUR, GBP, NGN, KES, ZAR, USD)
    """
    
    # Common date patterns
    DATE_PATTERNS = [
        (r"\d{4}-\d{2}-\d{2}", "%Y-%m-%d"),  # ISO: 2026-01-15
        (r"\d{2}/\d{2}/\d{4}", "%d/%m/%Y"),  # EU: 15/01/2026
        (r"\d{2}-\d{2}-\d{4}", "%d-%m-%Y"),  # EU alt: 15-01-2026
        (r"\d{2}\.\d{2}\.\d{4}", "%d.%m.%Y"),  # DE: 15.01.2026
        (r"\d{1,2} \w{3} \d{4}", "%d %b %Y"),  # 15 Jan 2026
    ]
    
    # Amount patterns (handles European comma decimals)
    AMOUNT_PATTERNS = [
        r"[-+]?\d{1,3}(?:[,\.]\d{3})*(?:[,\.]\d{2})?",  # 1,234.56 or 1.234,56
    ]
    
    # Currency symbols and codes
    CURRENCIES = {
        "€": "EUR", "EUR": "EUR",
        "£": "GBP", "GBP": "GBP",
        "$": "USD", "USD": "USD",
        "₦": "NGN", "NGN": "NGN",  # Nigerian Naira
        "KSh": "KES", "KES": "KES",  # Kenyan Shilling
        "R": "ZAR", "ZAR": "ZAR",  # South African Rand
        "GH₵": "GHS", "GHS": "GHS",  # Ghanaian Cedi
        "CFA": "XOF", "XOF": "XOF",  # West African CFA
    }
    
    def __init__(self, default_currency: str = "EUR"):
        self.default_currency = default_currency
    
    def parse_csv(self, content: str, filename: str = "") -> List[ParsedTransaction]:
        """
        Parse CSV bank statement.
        
        Auto-detects column mapping based on headers.
        """
        transactions = []
        
        # Try to detect dialect
        try:
            dialect = csv.Sniffer().sniff(content[:2048])
        except csv.Error:
            dialect = csv.excel
        
        reader = csv.reader(io.StringIO(content), dialect)
        rows = list(reader)
        
        if not rows:
            return []
        
        # Find header row (usually first or second row)
        header_row = 0
        headers = [h.lower().strip() for h in rows[0]]
        
        if not self._looks_like_header(headers):
            if len(rows) > 1:
                headers = [h.lower().strip() for h in rows[1]]
                header_row = 1
        
        # Map columns
        col_map = self._detect_columns(headers)
        logger.info(f"Detected columns: {col_map}")
        
        if col_map.get("date") is None or col_map.get("amount") is None:
            logger.warning("Could not detect required columns (date, amount)")
            return []
        
        # Parse data rows
        for row in rows[header_row + 1:]:
            if len(row) <= max(col_map.values()):
                continue
            
            try:
                tx = self._parse_row(row, col_map)
                if tx:
                    transactions.append(tx)
            except Exception as e:
                logger.warning(f"Failed to parse row: {row}, error: {e}")
                continue
        
        logger.info(f"Parsed {len(transactions)} transactions from CSV")
        return transactions
    
    def parse_pdf_text(self, text: str) -> List[ParsedTransaction]:
        """
        Parse transactions from PDF text content.
        
        Looks for patterns like:
        15/01/2026  STRIPE TRANSFER    1,500.00    10,500.00
        """
        transactions = []
        lines = text.split("\n")
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Try to extract date
            date_str = None
            date_format = None
            for pattern, fmt in self.DATE_PATTERNS:
                match = re.search(pattern, line)
                if match:
                    date_str = match.group()
                    date_format = fmt
                    break
            
            if not date_str:
                continue
            
            # Try to extract amounts
            amounts = re.findall(r"[-+]?\d{1,3}(?:[,\.]\d{3})*(?:[,\.]\d{2})", line)
            if not amounts:
                continue
            
            # Parse date
            try:
                date_obj = datetime.strptime(date_str, date_format)
                date_iso = date_obj.strftime("%Y-%m-%d")
            except ValueError:
                continue
            
            # Parse amount (first amount is usually transaction, second is balance)
            amount = self._parse_amount(amounts[0])
            balance = self._parse_amount(amounts[1]) if len(amounts) > 1 else None
            
            # Extract description (everything between date and first amount)
            desc_start = line.find(date_str) + len(date_str)
            desc_end = line.find(amounts[0])
            description = line[desc_start:desc_end].strip()
            
            # Clean up description
            description = re.sub(r"\s+", " ", description)
            
            if description and amount != 0:
                transactions.append(ParsedTransaction(
                    date=date_iso,
                    amount=amount,
                    currency=self._detect_currency(line),
                    description=description,
                    balance=balance,
                ))
        
        logger.info(f"Parsed {len(transactions)} transactions from PDF text")
        return transactions
    
    def _looks_like_header(self, row: List[str]) -> bool:
        """Check if row looks like a header row."""
        header_words = ["date", "amount", "description", "balance", "reference", 
                       "credit", "debit", "value", "transaction", "details",
                       "datum", "betrag", "beschreibung",  # German
                       "montant", "libellé",  # French
                       ]
        matches = sum(1 for cell in row if any(w in cell for w in header_words))
        return matches >= 2
    
    def _detect_columns(self, headers: List[str]) -> Dict[str, int]:
        """Detect which column contains what data."""
        col_map = {}
        
        for i, header in enumerate(headers):
            h = header.lower().strip()
            
            # Date column
            if h in ["date", "datum", "value date", "booking", "transaction date", "post date"]:
                col_map["date"] = i
            elif "date" in h and "date" not in col_map:
                col_map["date"] = i
            
            # Amount column
            elif h in ["amount", "betrag", "montant", "sum", "value"]:
                col_map["amount"] = i
            elif "amount" in h and "amount" not in col_map:
                col_map["amount"] = i
            
            # Credit/Debit columns (some banks split these)
            elif any(w in header for w in ["credit", "haben", "crédit"]):
                col_map["credit"] = i
            elif any(w in header for w in ["debit", "soll", "débit"]):
                col_map["debit"] = i
            
            # Description
            elif any(w in header for w in ["description", "details", "narrative", 
                                           "beschreibung", "libellé", "reference"]):
                col_map["description"] = i
            
            # Balance
            elif any(w in header for w in ["balance", "saldo", "solde"]):
                col_map["balance"] = i
            
            # Reference
            elif any(w in header for w in ["ref", "reference", "id"]):
                col_map["reference"] = i
        
        return col_map
    
    def _parse_row(self, row: List[str], col_map: Dict[str, int]) -> Optional[ParsedTransaction]:
        """Parse a single CSV row into a transaction."""
        # Get date
        date_str = row[col_map["date"]].strip()
        date_iso = self._parse_date(date_str)
        if not date_iso:
            return None
        
        # Get amount
        if "amount" in col_map:
            amount = self._parse_amount(row[col_map["amount"]])
        elif "credit" in col_map and "debit" in col_map:
            credit = self._parse_amount(row[col_map["credit"]]) or 0
            debit = self._parse_amount(row[col_map["debit"]]) or 0
            amount = credit - debit
        else:
            return None
        
        if amount == 0:
            return None
        
        # Get description
        description = ""
        if "description" in col_map:
            description = row[col_map["description"]].strip()
        
        # Get balance
        balance = None
        if "balance" in col_map:
            balance = self._parse_amount(row[col_map["balance"]])
        
        # Get reference
        reference = None
        if "reference" in col_map:
            reference = row[col_map["reference"]].strip()
        
        return ParsedTransaction(
            date=date_iso,
            amount=amount,
            currency=self.default_currency,
            description=description,
            balance=balance,
            reference=reference,
            transaction_type="credit" if amount > 0 else "debit",
        )
    
    def _parse_date(self, date_str: str) -> Optional[str]:
        """Parse date string to ISO format."""
        date_str = date_str.strip()
        
        for pattern, fmt in self.DATE_PATTERNS:
            if re.match(pattern, date_str):
                try:
                    date_obj = datetime.strptime(date_str, fmt)
                    return date_obj.strftime("%Y-%m-%d")
                except ValueError:
                    continue
        
        return None
    
    def _parse_amount(self, amount_str: str) -> Optional[float]:
        """Parse amount string to float, handling EU and US formats."""
        if not amount_str:
            return None
        
        amount_str = amount_str.strip()
        
        # Remove currency symbols
        for symbol in self.CURRENCIES.keys():
            amount_str = amount_str.replace(symbol, "")
        
        amount_str = amount_str.strip()
        
        if not amount_str:
            return None
        
        # Detect format: EU uses comma as decimal (1.234,56), US uses period (1,234.56)
        # If last separator is comma and followed by 2 digits, it's EU format
        if re.search(r",\d{2}$", amount_str):
            # EU format: 1.234,56 -> 1234.56
            amount_str = amount_str.replace(".", "").replace(",", ".")
        else:
            # US format: 1,234.56 -> 1234.56
            amount_str = amount_str.replace(",", "")
        
        try:
            return float(amount_str)
        except ValueError:
            return None
    
    def _detect_currency(self, text: str) -> str:
        """Detect currency from text."""
        for symbol, code in self.CURRENCIES.items():
            if symbol in text:
                return code
        return self.default_currency


# Convenience function
def parse_bank_statement(
    content: str,
    file_type: str = "csv",
    currency: str = "EUR",
) -> List[Dict[str, Any]]:
    """
    Parse a bank statement and return transactions.
    
    Args:
        content: File content (CSV text or PDF text)
        file_type: "csv" or "pdf"
        currency: Default currency code
    
    Returns:
        List of transaction dictionaries
    """
    parser = BankStatementParser(default_currency=currency)
    
    if file_type.lower() == "csv":
        transactions = parser.parse_csv(content)
    elif file_type.lower() == "pdf":
        transactions = parser.parse_pdf_text(content)
    else:
        raise ValueError(f"Unsupported file type: {file_type}")
    
    return [tx.to_dict() for tx in transactions]
