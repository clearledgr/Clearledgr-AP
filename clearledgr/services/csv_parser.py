"""
CSV Parser Service for Clearledgr Reconciliation v1

Provides robust CSV parsing with column mapping and data normalization.
"""
import csv
import io
import re
from typing import List, Dict, Optional
from datetime import datetime
from dateutil import parser as date_parser
from clearledgr.services.errors import CSVParseError, EmptyDataError


def parse_csv(file_bytes: bytes, mapping: Dict[str, str]) -> List[Dict]:
    """
    Parse CSV bytes into a list of dicts using the provided mapping.
    
    Args:
        file_bytes: Raw CSV file bytes.
        mapping: Dict mapping CSV column names -> semantic field names.
                 Example: {"Transaction ID": "txn_id", "Date": "date", "Amount": "amount"}
    
    Returns:
        List[dict] where keys are semantic field names.
    
    Requirements:
        - Detect encoding (utf-8, latin-1) and decode safely.
        - Use Python's csv module or pandas.
        - Ignore CSV columns not in mapping.
        - For each row, build a dict with semantic field keys.
        - Skip completely empty rows.
        - Normalize dates and amounts using helper functions.
    """
    # Try to detect encoding
    encodings = ['utf-8', 'latin-1', 'iso-8859-1', 'cp1252']
    decoded_content = None
    
    for encoding in encodings:
        try:
            decoded_content = file_bytes.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    
    if decoded_content is None:
        raise CSVParseError(
            source="file",
            detail="Could not decode file. Supported encodings: UTF-8, Latin-1, ISO-8859-1, CP1252"
        )
    
    # Parse CSV
    csv_reader = csv.DictReader(io.StringIO(decoded_content))
    
    # Normalize mapping keys (strip whitespace, case-insensitive matching)
    normalized_mapping = {}
    for csv_col, semantic_field in mapping.items():
        normalized_mapping[csv_col.strip()] = semantic_field
    
    result = []
    
    for row in csv_reader:
        # Skip completely empty rows
        if not any(row.values()):
            continue
        
        # Build dict with semantic field keys
        parsed_row = {}
        for csv_col, semantic_field in normalized_mapping.items():
            # Try exact match first
            if csv_col in row:
                value = row[csv_col]
            else:
                # Try case-insensitive match
                value = None
                for key in row.keys():
                    if key.strip().lower() == csv_col.lower():
                        value = row[key]
                        break
                
                if value is None:
                    # Column not found, skip this field
                    continue
            
            # Normalize based on field name
            if 'date' in semantic_field.lower():
                parsed_row[semantic_field] = normalize_date(value) if value else None
            elif 'amount' in semantic_field.lower():
                parsed_row[semantic_field] = normalize_amount(value) if value else None
            else:
                # Keep as string, strip whitespace
                parsed_row[semantic_field] = value.strip() if value else None
        
        # Only add row if it has at least one field
        if parsed_row:
            result.append(parsed_row)
    
    return result


def normalize_date(date_str: str) -> Optional[str]:
    """
    Parse common date formats and return ISO YYYY-MM-DD.
    Handle at least:
    - YYYY-MM-DD
    - DD/MM/YYYY
    - MM/DD/YYYY
    - DD-MM-YYYY
    Return None or empty string if unparseable.
    """
    if not date_str or not isinstance(date_str, str):
        return None
    
    date_str = date_str.strip()
    if not date_str:
        return None
    
    # Try dateutil parser first (handles many formats)
    try:
        parsed_date = date_parser.parse(date_str, dayfirst=False)
        return parsed_date.strftime('%Y-%m-%d')
    except (ValueError, TypeError):
        pass
    
    # Try explicit format patterns
    formats = [
        '%Y-%m-%d',
        '%d/%m/%Y',
        '%m/%d/%Y',
        '%d-%m-%Y',
        '%Y/%m/%d',
        '%m-%d-%Y',
    ]
    
    for fmt in formats:
        try:
            parsed_date = datetime.strptime(date_str, fmt)
            return parsed_date.strftime('%Y-%m-%d')
        except ValueError:
            continue
    
    # If all parsing fails, return None
    return None


def normalize_amount(amount_str: str) -> float:
    """
    Parse amount strings to float.
    Behaviors:
        - Strip currency symbols ($, €, £) and whitespace.
        - Remove thousands separators (commas, spaces).
        - Handle negative amounts (leading '-', or parentheses like (100.00)).
        - Return 0.0 if unparseable.
    """
    if not amount_str or not isinstance(amount_str, str):
        return 0.0
    
    amount_str = amount_str.strip()
    if not amount_str:
        return 0.0
    
    # Check for parentheses notation (negative)
    is_negative = False
    if amount_str.startswith('(') and amount_str.endswith(')'):
        is_negative = True
        amount_str = amount_str[1:-1].strip()
    elif amount_str.startswith('-'):
        is_negative = True
        amount_str = amount_str[1:].strip()
    
    # Remove currency symbols
    amount_str = re.sub(r'[$€£¥]', '', amount_str)
    
    # Remove thousands separators (commas and spaces used as separators)
    # But be careful - some locales use comma as decimal separator
    # We'll assume standard format: comma for thousands, period for decimal
    # Remove commas that are likely thousands separators
    if '.' in amount_str:
        # Has decimal point, so comma is thousands separator
        amount_str = amount_str.replace(',', '')
    else:
        # No decimal point - could be comma as decimal or thousands
        # Try to detect: if last comma has 3 digits after, it's thousands
        parts = amount_str.split(',')
        if len(parts) > 1 and len(parts[-1]) == 3:
            # Likely thousands separator
            amount_str = amount_str.replace(',', '')
        elif len(parts) == 2:
            # Could be decimal separator, replace with period
            amount_str = amount_str.replace(',', '.')
        else:
            # Multiple commas, assume thousands
            amount_str = amount_str.replace(',', '')
    
    # Remove any remaining whitespace
    amount_str = amount_str.replace(' ', '')
    
    # Try to parse as float
    try:
        value = float(amount_str)
        return -value if is_negative else value
    except (ValueError, TypeError):
        return 0.0

