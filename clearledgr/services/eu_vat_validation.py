"""
EU VAT Number Validation Service

Validates EU VAT numbers using:
1. Format validation (regex per country)
2. VIES API validation (EU's official validation service)
3. Caching to reduce API calls

Supports all 27 EU member states + UK (for historical data).
"""

import logging
import re
import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
import httpx

logger = logging.getLogger(__name__)


class VATValidationStatus(Enum):
    """VAT validation status."""
    VALID = "valid"
    INVALID = "invalid"
    FORMAT_ERROR = "format_error"
    SERVICE_UNAVAILABLE = "service_unavailable"
    NOT_CHECKED = "not_checked"


@dataclass
class VATValidationResult:
    """Result of VAT number validation."""
    vat_number: str
    country_code: str
    status: VATValidationStatus
    is_valid: bool = False
    
    # Company details (from VIES)
    company_name: Optional[str] = None
    company_address: Optional[str] = None
    
    # Validation metadata
    validated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    validation_source: str = "format"  # format, vies, cache
    request_id: Optional[str] = None
    
    # Error info
    error_message: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "vat_number": self.vat_number,
            "country_code": self.country_code,
            "status": self.status.value,
            "is_valid": self.is_valid,
            "company_name": self.company_name,
            "company_address": self.company_address,
            "validated_at": self.validated_at,
            "validation_source": self.validation_source,
            "error_message": self.error_message,
        }


# EU VAT number formats by country
# Format: (country_code, regex_pattern, example)
EU_VAT_FORMATS = {
    # EU Member States
    "AT": (r"^ATU\d{8}$", "ATU12345678"),                           # Austria
    "BE": (r"^BE[01]\d{9}$", "BE0123456789"),                       # Belgium
    "BG": (r"^BG\d{9,10}$", "BG123456789"),                         # Bulgaria
    "CY": (r"^CY\d{8}[A-Z]$", "CY12345678A"),                       # Cyprus
    "CZ": (r"^CZ\d{8,10}$", "CZ12345678"),                          # Czech Republic
    "DE": (r"^DE\d{9}$", "DE123456789"),                            # Germany
    "DK": (r"^DK\d{8}$", "DK12345678"),                             # Denmark
    "EE": (r"^EE\d{9}$", "EE123456789"),                            # Estonia
    "EL": (r"^EL\d{9}$", "EL123456789"),                            # Greece (EL not GR)
    "ES": (r"^ES[A-Z0-9]\d{7}[A-Z0-9]$", "ESA12345678"),            # Spain
    "FI": (r"^FI\d{8}$", "FI12345678"),                             # Finland
    "FR": (r"^FR[A-Z0-9]{2}\d{9}$", "FR12345678901"),               # France
    "HR": (r"^HR\d{11}$", "HR12345678901"),                         # Croatia
    "HU": (r"^HU\d{8}$", "HU12345678"),                             # Hungary
    "IE": (r"^IE\d{7}[A-Z]{1,2}$|^IE\d[A-Z+*]\d{5}[A-Z]$", "IE1234567A"),  # Ireland
    "IT": (r"^IT\d{11}$", "IT12345678901"),                         # Italy
    "LT": (r"^LT(\d{9}|\d{12})$", "LT123456789"),                   # Lithuania
    "LU": (r"^LU\d{8}$", "LU12345678"),                             # Luxembourg
    "LV": (r"^LV\d{11}$", "LV12345678901"),                         # Latvia
    "MT": (r"^MT\d{8}$", "MT12345678"),                             # Malta
    "NL": (r"^NL\d{9}B\d{2}$", "NL123456789B01"),                   # Netherlands
    "PL": (r"^PL\d{10}$", "PL1234567890"),                          # Poland
    "PT": (r"^PT\d{9}$", "PT123456789"),                            # Portugal
    "RO": (r"^RO\d{2,10}$", "RO1234567890"),                        # Romania
    "SE": (r"^SE\d{12}$", "SE123456789012"),                        # Sweden
    "SI": (r"^SI\d{8}$", "SI12345678"),                             # Slovenia
    "SK": (r"^SK\d{10}$", "SK1234567890"),                          # Slovakia
    
    # Northern Ireland (special case post-Brexit)
    "XI": (r"^XI\d{9}$|^XI\d{12}$|^XIGD\d{3}$", "XI123456789"),     # Northern Ireland
    
    # UK (for historical validation, no longer in VIES)
    "GB": (r"^GB(\d{9}|\d{12}|GD\d{3}|HA\d{3})$", "GB123456789"),   # UK (historical)
}

# VIES country codes (Greece is EL in VIES, not GR)
VIES_COUNTRY_CODES = set(EU_VAT_FORMATS.keys()) - {"GB"}  # GB no longer in VIES


class EUVATValidationService:
    """
    Service for validating EU VAT numbers.
    
    Features:
    - Format validation using country-specific regex
    - VIES API validation for real-time verification
    - Caching to reduce API calls
    - Batch validation support
    """
    
    # VIES SOAP endpoint
    VIES_URL = "https://ec.europa.eu/taxation_customs/vies/services/checkVatService"
    
    def __init__(self):
        self._cache: Dict[str, Tuple[VATValidationResult, datetime]] = {}
        self._cache_ttl = timedelta(hours=24)  # Cache results for 24 hours
    
    def validate_format(self, vat_number: str) -> VATValidationResult:
        """
        Validate VAT number format only (no API call).
        
        Args:
            vat_number: VAT number with country prefix (e.g., "DE123456789")
        
        Returns:
            VATValidationResult with format validation status
        """
        # Clean input
        vat_number = self._clean_vat_number(vat_number)
        
        if len(vat_number) < 4:
            return VATValidationResult(
                vat_number=vat_number,
                country_code="",
                status=VATValidationStatus.FORMAT_ERROR,
                is_valid=False,
                error_message="VAT number too short",
            )
        
        # Extract country code (first 2 characters)
        country_code = vat_number[:2].upper()
        
        if country_code not in EU_VAT_FORMATS:
            return VATValidationResult(
                vat_number=vat_number,
                country_code=country_code,
                status=VATValidationStatus.FORMAT_ERROR,
                is_valid=False,
                error_message=f"Unknown country code: {country_code}",
            )
        
        pattern, example = EU_VAT_FORMATS[country_code]
        
        if re.match(pattern, vat_number):
            return VATValidationResult(
                vat_number=vat_number,
                country_code=country_code,
                status=VATValidationStatus.VALID,
                is_valid=True,
                validation_source="format",
            )
        else:
            return VATValidationResult(
                vat_number=vat_number,
                country_code=country_code,
                status=VATValidationStatus.FORMAT_ERROR,
                is_valid=False,
                error_message=f"Invalid format for {country_code}. Expected format like: {example}",
            )
    
    async def validate_vies(self, vat_number: str, skip_cache: bool = False) -> VATValidationResult:
        """
        Validate VAT number against EU VIES service.
        
        Args:
            vat_number: VAT number with country prefix
            skip_cache: If True, bypass cache and call VIES directly
        
        Returns:
            VATValidationResult with VIES validation status and company details
        """
        vat_number = self._clean_vat_number(vat_number)
        
        # Check format first
        format_result = self.validate_format(vat_number)
        if not format_result.is_valid:
            return format_result
        
        country_code = format_result.country_code
        
        # UK is no longer in VIES
        if country_code == "GB":
            format_result.error_message = "UK VAT numbers cannot be validated via VIES (post-Brexit)"
            format_result.validation_source = "format_only"
            return format_result
        
        # Check cache
        if not skip_cache:
            cached = self._get_cached(vat_number)
            if cached:
                return cached
        
        # Call VIES API
        try:
            result = await self._call_vies(country_code, vat_number[2:])
            
            # Cache the result
            self._set_cached(vat_number, result)
            
            return result
            
        except Exception as e:
            logger.error(f"VIES API error for {vat_number}: {e}")
            return VATValidationResult(
                vat_number=vat_number,
                country_code=country_code,
                status=VATValidationStatus.SERVICE_UNAVAILABLE,
                is_valid=False,
                error_message=f"VIES service unavailable: {str(e)}",
                validation_source="format",
            )
    
    async def _call_vies(self, country_code: str, vat_number: str) -> VATValidationResult:
        """Call VIES SOAP API."""
        
        # SOAP request body
        soap_body = f"""<?xml version="1.0" encoding="UTF-8"?>
        <soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                          xmlns:urn="urn:ec.europa.eu:taxud:vies:services:checkVat:types">
            <soapenv:Header/>
            <soapenv:Body>
                <urn:checkVat>
                    <urn:countryCode>{country_code}</urn:countryCode>
                    <urn:vatNumber>{vat_number}</urn:vatNumber>
                </urn:checkVat>
            </soapenv:Body>
        </soapenv:Envelope>"""
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                self.VIES_URL,
                content=soap_body,
                headers={
                    "Content-Type": "text/xml; charset=utf-8",
                    "SOAPAction": "",
                },
            )
            
            response.raise_for_status()
            
            # Parse SOAP response
            return self._parse_vies_response(
                country_code + vat_number,
                country_code,
                response.text
            )
    
    def _parse_vies_response(
        self,
        full_vat: str,
        country_code: str,
        xml_response: str
    ) -> VATValidationResult:
        """Parse VIES SOAP response."""
        
        # Extract values using regex (avoiding XML parser for simplicity)
        valid_match = re.search(r"<valid>(\w+)</valid>", xml_response)
        name_match = re.search(r"<name>([^<]*)</name>", xml_response)
        address_match = re.search(r"<address>([^<]*)</address>", xml_response)
        request_id_match = re.search(r"<requestIdentifier>([^<]*)</requestIdentifier>", xml_response)
        
        is_valid = valid_match and valid_match.group(1).lower() == "true"
        
        return VATValidationResult(
            vat_number=full_vat,
            country_code=country_code,
            status=VATValidationStatus.VALID if is_valid else VATValidationStatus.INVALID,
            is_valid=is_valid,
            company_name=name_match.group(1).strip() if name_match else None,
            company_address=address_match.group(1).strip() if address_match else None,
            validation_source="vies",
            request_id=request_id_match.group(1) if request_id_match else None,
        )
    
    async def validate_batch(
        self,
        vat_numbers: list[str],
        use_vies: bool = True
    ) -> list[VATValidationResult]:
        """
        Validate multiple VAT numbers.
        
        Args:
            vat_numbers: List of VAT numbers to validate
            use_vies: If True, validate against VIES API
        
        Returns:
            List of validation results
        """
        if use_vies:
            # Run VIES validations concurrently (with rate limiting)
            tasks = []
            for vat in vat_numbers:
                tasks.append(self.validate_vies(vat))
                if len(tasks) >= 5:  # Batch of 5 to avoid rate limiting
                    await asyncio.sleep(0.5)
            
            return await asyncio.gather(*tasks)
        else:
            return [self.validate_format(vat) for vat in vat_numbers]
    
    def _clean_vat_number(self, vat_number: str) -> str:
        """Clean and normalize VAT number."""
        # Remove spaces, dots, dashes
        cleaned = re.sub(r"[\s.\-]", "", vat_number)
        # Uppercase
        return cleaned.upper()
    
    def _get_cached(self, vat_number: str) -> Optional[VATValidationResult]:
        """Get cached validation result if not expired."""
        if vat_number in self._cache:
            result, cached_at = self._cache[vat_number]
            if datetime.utcnow() - cached_at < self._cache_ttl:
                result.validation_source = "cache"
                return result
            else:
                del self._cache[vat_number]
        return None
    
    def _set_cached(self, vat_number: str, result: VATValidationResult):
        """Cache validation result."""
        self._cache[vat_number] = (result, datetime.utcnow())
    
    def get_country_format(self, country_code: str) -> Optional[Dict[str, str]]:
        """Get VAT format info for a country."""
        country_code = country_code.upper()
        if country_code in EU_VAT_FORMATS:
            pattern, example = EU_VAT_FORMATS[country_code]
            return {
                "country_code": country_code,
                "pattern": pattern,
                "example": example,
            }
        return None
    
    def get_supported_countries(self) -> list[str]:
        """Get list of supported country codes."""
        return list(EU_VAT_FORMATS.keys())


# Singleton instance
_vat_service: Optional[EUVATValidationService] = None


def get_vat_validation_service() -> EUVATValidationService:
    """Get the VAT validation service singleton."""
    global _vat_service
    if _vat_service is None:
        _vat_service = EUVATValidationService()
    return _vat_service


# Convenience functions
def validate_vat_format(vat_number: str) -> VATValidationResult:
    """Validate VAT number format (synchronous)."""
    return get_vat_validation_service().validate_format(vat_number)


async def validate_vat_vies(vat_number: str) -> VATValidationResult:
    """Validate VAT number against VIES (async)."""
    return await get_vat_validation_service().validate_vies(vat_number)
