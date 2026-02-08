"""
Tax Calculation Service

Handles tax calculations for AP:
- VAT/GST calculation
- Withholding tax
- Sales/Use tax
- Tax code mapping
"""

import logging
from datetime import datetime, date
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
import uuid
import re

logger = logging.getLogger(__name__)


class TaxType(Enum):
    """Types of taxes."""
    VAT = "vat"                    # Value Added Tax
    GST = "gst"                    # Goods and Services Tax
    SALES_TAX = "sales"            # US Sales Tax
    USE_TAX = "use"                # Use Tax
    WITHHOLDING = "withholding"    # Withholding Tax
    EXCISE = "excise"              # Excise Tax
    CUSTOMS = "customs"            # Customs/Import Duty
    NONE = "none"


class TaxStatus(Enum):
    """Status of tax calculation."""
    CALCULATED = "calculated"
    EXEMPT = "exempt"
    REVERSE_CHARGE = "reverse_charge"
    ZERO_RATED = "zero_rated"
    OUT_OF_SCOPE = "out_of_scope"


@dataclass
class TaxCode:
    """Tax code definition."""
    code: str
    name: str
    tax_type: TaxType
    rate: float  # As percentage (e.g., 20.0 for 20%)
    gl_account: str = ""
    description: str = ""
    is_recoverable: bool = True  # Can be claimed as input credit
    country: str = ""
    region: str = ""
    is_active: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "tax_type": self.tax_type.value,
            "rate": self.rate,
            "gl_account": self.gl_account,
            "description": self.description,
            "is_recoverable": self.is_recoverable,
            "country": self.country,
            "region": self.region,
            "is_active": self.is_active,
        }


@dataclass 
class TaxCalculation:
    """Result of tax calculation."""
    calculation_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    
    # Amounts
    net_amount: float = 0.0
    tax_amount: float = 0.0
    gross_amount: float = 0.0
    
    # Tax details
    tax_code: str = ""
    tax_rate: float = 0.0
    tax_type: TaxType = TaxType.NONE
    status: TaxStatus = TaxStatus.CALCULATED
    
    # For withholding
    withholding_amount: float = 0.0
    withholding_rate: float = 0.0
    
    # Multi-tax support
    tax_breakdown: List[Dict[str, Any]] = field(default_factory=list)
    
    # Metadata
    currency: str = "USD"
    calculation_date: date = field(default_factory=date.today)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "calculation_id": self.calculation_id,
            "net_amount": self.net_amount,
            "tax_amount": self.tax_amount,
            "gross_amount": self.gross_amount,
            "tax_code": self.tax_code,
            "tax_rate": self.tax_rate,
            "tax_type": self.tax_type.value,
            "status": self.status.value,
            "withholding_amount": self.withholding_amount,
            "withholding_rate": self.withholding_rate,
            "tax_breakdown": self.tax_breakdown,
            "currency": self.currency,
        }


@dataclass
class WithholdingTaxConfig:
    """Withholding tax configuration."""
    country: str
    vendor_type: str  # contractor, service, goods
    rate: float
    threshold: float = 0.0  # Minimum amount to withhold
    exemption_certificate_required: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "country": self.country,
            "vendor_type": self.vendor_type,
            "rate": self.rate,
            "threshold": self.threshold,
        }


class TaxCalculationService:
    """
    Service for tax calculations.
    """
    
    # Standard tax codes
    DEFAULT_TAX_CODES = {
        # VAT rates (EU)
        "VAT-STD-UK": TaxCode("VAT-STD-UK", "UK VAT Standard", TaxType.VAT, 20.0, "2200", country="GB"),
        "VAT-RED-UK": TaxCode("VAT-RED-UK", "UK VAT Reduced", TaxType.VAT, 5.0, "2200", country="GB"),
        "VAT-ZERO-UK": TaxCode("VAT-ZERO-UK", "UK VAT Zero Rated", TaxType.VAT, 0.0, "2200", country="GB"),
        "VAT-STD-DE": TaxCode("VAT-STD-DE", "Germany VAT Standard", TaxType.VAT, 19.0, "2200", country="DE"),
        "VAT-RED-DE": TaxCode("VAT-RED-DE", "Germany VAT Reduced", TaxType.VAT, 7.0, "2200", country="DE"),
        "VAT-STD-FR": TaxCode("VAT-STD-FR", "France VAT Standard", TaxType.VAT, 20.0, "2200", country="FR"),
        
        # GST rates
        "GST-STD-AU": TaxCode("GST-STD-AU", "Australia GST", TaxType.GST, 10.0, "2200", country="AU"),
        "GST-STD-NZ": TaxCode("GST-STD-NZ", "New Zealand GST", TaxType.GST, 15.0, "2200", country="NZ"),
        "GST-STD-IN": TaxCode("GST-STD-IN", "India GST Standard", TaxType.GST, 18.0, "2200", country="IN"),
        "GST-STD-SG": TaxCode("GST-STD-SG", "Singapore GST", TaxType.GST, 9.0, "2200", country="SG"),
        "GST-STD-CA": TaxCode("GST-STD-CA", "Canada GST", TaxType.GST, 5.0, "2200", country="CA"),
        
        # US Sales Tax (representative)
        "SALES-CA": TaxCode("SALES-CA", "California Sales Tax", TaxType.SALES_TAX, 7.25, "2200", country="US", region="CA"),
        "SALES-NY": TaxCode("SALES-NY", "New York Sales Tax", TaxType.SALES_TAX, 8.0, "2200", country="US", region="NY"),
        "SALES-TX": TaxCode("SALES-TX", "Texas Sales Tax", TaxType.SALES_TAX, 6.25, "2200", country="US", region="TX"),
        
        # Exempt/Zero
        "EXEMPT": TaxCode("EXEMPT", "Tax Exempt", TaxType.NONE, 0.0, ""),
        "ZERO": TaxCode("ZERO", "Zero Rated", TaxType.VAT, 0.0, "2200"),
        "RC": TaxCode("RC", "Reverse Charge", TaxType.VAT, 0.0, "2200"),
    }
    
    # Withholding tax rates by country
    WITHHOLDING_RATES = {
        "US": [
            WithholdingTaxConfig("US", "contractor", 24.0, threshold=600),  # Backup withholding
        ],
        "IN": [
            WithholdingTaxConfig("IN", "contractor", 10.0),
            WithholdingTaxConfig("IN", "service", 2.0),
        ],
        "NG": [
            WithholdingTaxConfig("NG", "contractor", 5.0),
            WithholdingTaxConfig("NG", "service", 5.0),
        ],
        "KE": [
            WithholdingTaxConfig("KE", "service", 5.0),
        ],
    }
    
    def __init__(self, organization_id: str = "default", default_country: str = "US"):
        self.organization_id = organization_id
        self.default_country = default_country
        self._tax_codes: Dict[str, TaxCode] = dict(self.DEFAULT_TAX_CODES)
        self._vendor_exemptions: Dict[str, str] = {}  # vendor_id -> exemption_certificate
    
    def create_tax_code(
        self,
        code: str,
        name: str,
        tax_type: TaxType,
        rate: float,
        gl_account: str = "",
        country: str = "",
        **kwargs
    ) -> TaxCode:
        """Create a custom tax code."""
        tax_code = TaxCode(
            code=code,
            name=name,
            tax_type=tax_type,
            rate=rate,
            gl_account=gl_account,
            country=country,
            **kwargs
        )
        self._tax_codes[code] = tax_code
        logger.info(f"Created tax code: {code}")
        return tax_code
    
    def get_tax_code(self, code: str) -> Optional[TaxCode]:
        """Get a tax code by code."""
        return self._tax_codes.get(code)
    
    def calculate_tax(
        self,
        net_amount: float,
        tax_code: str = "",
        country: str = "",
        vendor_id: str = "",
        vendor_type: str = "",
        is_service: bool = False,
    ) -> TaxCalculation:
        """
        Calculate tax for an invoice amount.
        """
        calc = TaxCalculation(
            net_amount=net_amount,
            currency="USD",
        )
        
        # Check for vendor exemption
        if vendor_id and vendor_id in self._vendor_exemptions:
            calc.status = TaxStatus.EXEMPT
            calc.gross_amount = net_amount
            calc.tax_amount = 0
            return calc
        
        # Get tax code
        tc = None
        if tax_code:
            tc = self._tax_codes.get(tax_code)
        elif country:
            tc = self._get_default_tax_code_for_country(country)
        
        if not tc or tc.rate == 0:
            calc.status = TaxStatus.ZERO_RATED if tc else TaxStatus.OUT_OF_SCOPE
            calc.gross_amount = net_amount
            calc.tax_amount = 0
            return calc
        
        # Calculate tax
        calc.tax_code = tc.code
        calc.tax_rate = tc.rate
        calc.tax_type = tc.tax_type
        calc.tax_amount = round(net_amount * (tc.rate / 100), 2)
        calc.gross_amount = round(net_amount + calc.tax_amount, 2)
        calc.status = TaxStatus.CALCULATED
        
        # Add to breakdown
        calc.tax_breakdown.append({
            "code": tc.code,
            "name": tc.name,
            "rate": tc.rate,
            "amount": calc.tax_amount,
            "gl_account": tc.gl_account,
        })
        
        # Check for withholding tax
        if vendor_type or is_service:
            withholding = self._calculate_withholding(
                net_amount, 
                country or self.default_country,
                vendor_type or ("service" if is_service else "goods"),
            )
            if withholding > 0:
                calc.withholding_amount = withholding
                calc.withholding_rate = self._get_withholding_rate(
                    country or self.default_country, 
                    vendor_type or "service"
                )
        
        return calc
    
    def _get_default_tax_code_for_country(self, country: str) -> Optional[TaxCode]:
        """Get default tax code for a country."""
        country = country.upper()
        
        # Try standard rate first
        for code, tc in self._tax_codes.items():
            if tc.country == country and "STD" in code:
                return tc
        
        # Any rate for country
        for code, tc in self._tax_codes.items():
            if tc.country == country:
                return tc
        
        return None
    
    def calculate_vat(
        self,
        net_amount: float,
        country: str,
        is_reverse_charge: bool = False,
    ) -> TaxCalculation:
        """
        Calculate VAT specifically.
        """
        if is_reverse_charge:
            calc = TaxCalculation(
                net_amount=net_amount,
                gross_amount=net_amount,
                tax_amount=0,
                status=TaxStatus.REVERSE_CHARGE,
                tax_type=TaxType.VAT,
            )
            return calc
        
        return self.calculate_tax(net_amount, country=country)
    
    def calculate_gst(
        self,
        net_amount: float,
        country: str,
    ) -> TaxCalculation:
        """Calculate GST specifically."""
        return self.calculate_tax(net_amount, country=country)
    
    def _calculate_withholding(
        self,
        amount: float,
        country: str,
        vendor_type: str,
    ) -> float:
        """Calculate withholding tax amount."""
        configs = self.WITHHOLDING_RATES.get(country.upper(), [])
        
        for config in configs:
            if config.vendor_type == vendor_type:
                if amount >= config.threshold:
                    return round(amount * (config.rate / 100), 2)
        
        return 0.0
    
    def _get_withholding_rate(self, country: str, vendor_type: str) -> float:
        """Get withholding tax rate."""
        configs = self.WITHHOLDING_RATES.get(country.upper(), [])
        
        for config in configs:
            if config.vendor_type == vendor_type:
                return config.rate
        
        return 0.0
    
    def extract_tax_from_invoice(
        self,
        invoice_text: str,
        total_amount: float,
    ) -> TaxCalculation:
        """
        Extract tax information from invoice text.
        """
        calc = TaxCalculation()
        text_lower = invoice_text.lower()
        
        # Try to find VAT/GST amount
        vat_patterns = [
            r'vat[:\s]*\$?([\d,]+\.?\d*)',
            r'gst[:\s]*\$?([\d,]+\.?\d*)',
            r'tax[:\s]*\$?([\d,]+\.?\d*)',
            r'sales\s*tax[:\s]*\$?([\d,]+\.?\d*)',
        ]
        
        for pattern in vat_patterns:
            match = re.search(pattern, text_lower)
            if match:
                try:
                    tax_amount = float(match.group(1).replace(',', ''))
                    calc.tax_amount = tax_amount
                    calc.net_amount = total_amount - tax_amount
                    calc.gross_amount = total_amount
                    calc.status = TaxStatus.CALCULATED
                    break
                except ValueError:
                    continue
        
        # Try to find VAT rate
        rate_patterns = [
            r'(\d+(?:\.\d+)?)\s*%\s*vat',
            r'vat\s*@?\s*(\d+(?:\.\d+)?)\s*%',
            r'gst\s*@?\s*(\d+(?:\.\d+)?)\s*%',
        ]
        
        for pattern in rate_patterns:
            match = re.search(pattern, text_lower)
            if match:
                try:
                    calc.tax_rate = float(match.group(1))
                    break
                except ValueError:
                    continue
        
        # Determine tax type from keywords
        if 'vat' in text_lower:
            calc.tax_type = TaxType.VAT
        elif 'gst' in text_lower:
            calc.tax_type = TaxType.GST
        elif 'sales tax' in text_lower:
            calc.tax_type = TaxType.SALES_TAX
        
        # If no tax found, calculate from total
        if calc.tax_amount == 0 and total_amount > 0:
            calc.net_amount = total_amount
            calc.gross_amount = total_amount
            calc.status = TaxStatus.OUT_OF_SCOPE
        
        return calc
    
    def register_vendor_exemption(
        self,
        vendor_id: str,
        exemption_certificate: str,
    ):
        """Register a vendor's tax exemption."""
        self._vendor_exemptions[vendor_id] = exemption_certificate
        logger.info(f"Registered tax exemption for vendor {vendor_id}")
    
    def remove_vendor_exemption(self, vendor_id: str):
        """Remove vendor tax exemption."""
        if vendor_id in self._vendor_exemptions:
            del self._vendor_exemptions[vendor_id]
    
    def get_tax_codes_for_country(self, country: str) -> List[TaxCode]:
        """Get all tax codes for a country."""
        return [tc for tc in self._tax_codes.values() if tc.country == country.upper()]
    
    def get_all_tax_codes(self) -> List[TaxCode]:
        """Get all tax codes."""
        return list(self._tax_codes.values())
    
    def get_summary(self) -> Dict[str, Any]:
        """Get tax service summary."""
        return {
            "total_tax_codes": len(self._tax_codes),
            "by_type": {
                tt.value: len([tc for tc in self._tax_codes.values() if tc.tax_type == tt])
                for tt in TaxType
            },
            "countries_supported": len(set(tc.country for tc in self._tax_codes.values() if tc.country)),
            "vendor_exemptions": len(self._vendor_exemptions),
            "default_country": self.default_country,
        }


# Singleton instance cache
_instances: Dict[str, TaxCalculationService] = {}


def get_tax_calculation_service(
    organization_id: str = "default",
    default_country: str = "US",
) -> TaxCalculationService:
    """Get or create tax calculation service for organization."""
    if organization_id not in _instances:
        _instances[organization_id] = TaxCalculationService(organization_id, default_country)
    return _instances[organization_id]
