"""Field-provenance builder for structured-source extraction paths.

The email/LLM path has its own provenance builder at
``email_parser.py:_build_field_provenance`` because it has to juggle
a multi-source merge (email body, attachment OCR, LLM enrichment) and
record candidate values from each. PEPPOL UBL and the ERP-native intake
adapters are deterministic single-source: every field comes from the
same parsed document or API response, so they share the passthrough
builder here.

Why this matters for the audit trail
------------------------------------
``field_provenance`` answers, for every persisted field, three questions:

1. **Where did this value come from?** (``source``)
2. **Which artefact?** (``source_ref`` — bill id, message id, file hash)
3. **By what method?** (``method`` — ubl_parser, api_passthrough, etc.)

Combined with ``field_evidence``, an auditor opening any AP item can
reconstruct, per field, the full chain back to the system that produced
it — which is what makes the Box record a system-of-record artefact and
not a coordinator's cache.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional


# Canonical ``source`` tokens. Producers MUST use one of these values so
# downstream surfaces (UI rendering, audit search, vendor-feedback loops)
# can match on a known set. New sources require adding here + updating
# ``DecisionContext.ui_surface`` if they're also a decision surface.
SOURCE_EMAIL = "email"
SOURCE_ATTACHMENT = "attachment"
SOURCE_CLAUDE_VISION = "claude_vision"
SOURCE_PEPPOL_UBL = "peppol_ubl"
SOURCE_ERP_NATIVE_QUICKBOOKS = "erp_native_quickbooks"
SOURCE_ERP_NATIVE_NETSUITE = "erp_native_netsuite"
SOURCE_ERP_NATIVE_XERO = "erp_native_xero"
SOURCE_ERP_NATIVE_SAP = "erp_native_sap"

# Canonical ``method`` tokens.
METHOD_LLM_EXTRACT = "llm_extract"
METHOD_UBL_PARSER = "ubl_parser"
METHOD_API_PASSTHROUGH = "api_passthrough"
METHOD_CLAUDE_VISION = "claude_vision"
METHOD_REGEX_EXTRACT = "regex_extract"


def _is_field_value_present(value: Any) -> bool:
    """Match ``email_parser._has_field_value`` semantics so the two
    builders agree on what counts as 'this field has a value'.
    """
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


def build_passthrough_provenance(
    *,
    source: str,
    source_ref: Optional[str],
    method: str,
    fields: Dict[str, Any],
    extracted_at: Optional[str] = None,
    confidences: Optional[Dict[str, float]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Build a uniform ``field_provenance`` dict for a single-source intake.

    Used by:

    * :mod:`solden.services.peppol_ubl_parser`
    * :mod:`solden.integrations.erp_quickbooks_intake_adapter`
    * :mod:`solden.integrations.erp_netsuite_intake_adapter` and ``_intake.py``
    * :mod:`solden.integrations.erp_xero_intake_adapter`
    * :mod:`solden.integrations.erp_sap_s4hana_intake_adapter` and ``_intake.py``

    Every field with a non-empty value gets an entry pointing back to
    the source document and the method that produced it. Empty / missing
    fields are omitted (matching the email path's behaviour, so downstream
    code can treat both shapes uniformly).
    """
    timestamp = extracted_at or datetime.now(timezone.utc).isoformat()
    provenance: Dict[str, Dict[str, Any]] = {}
    confidences = confidences or {}
    for field_name, value in fields.items():
        if not _is_field_value_present(value):
            continue
        entry: Dict[str, Any] = {
            "source": source,
            "source_ref": source_ref,
            "method": method,
            "extracted_at": timestamp,
            "value": value,
        }
        conf = confidences.get(field_name)
        if conf is not None:
            entry["confidence"] = float(conf)
        provenance[field_name] = entry
    return provenance


def build_passthrough_evidence(
    *,
    field_provenance: Dict[str, Dict[str, Any]],
    source_label: str,
) -> Dict[str, Dict[str, Any]]:
    """Build a ``field_evidence`` dict that mirrors what the email path
    produces, for use by sidebar / audit surfaces that render
    "where this value came from".

    ``source_label`` is the human-readable label shown in the UI
    (e.g. "PEPPOL e-invoice", "QuickBooks Online", "NetSuite Vendor Bill").
    """
    evidence: Dict[str, Dict[str, Any]] = {}
    for field_name, entry in field_provenance.items():
        evidence[field_name] = {
            "source": entry.get("source"),
            "source_label": source_label,
            "selected_value": entry.get("value"),
            "source_ref": entry.get("source_ref"),
            "method": entry.get("method"),
        }
    return evidence


__all__ = [
    "SOURCE_EMAIL",
    "SOURCE_ATTACHMENT",
    "SOURCE_CLAUDE_VISION",
    "SOURCE_PEPPOL_UBL",
    "SOURCE_ERP_NATIVE_QUICKBOOKS",
    "SOURCE_ERP_NATIVE_NETSUITE",
    "SOURCE_ERP_NATIVE_XERO",
    "SOURCE_ERP_NATIVE_SAP",
    "METHOD_LLM_EXTRACT",
    "METHOD_UBL_PARSER",
    "METHOD_API_PASSTHROUGH",
    "METHOD_CLAUDE_VISION",
    "METHOD_REGEX_EXTRACT",
    "build_passthrough_provenance",
    "build_passthrough_evidence",
]
