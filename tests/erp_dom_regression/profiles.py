from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List


@dataclass(frozen=True)
class ERPDOMProfile:
    name: str
    fixture_filename: str
    description: str

    def fixture_path(self) -> Path:
        return Path(__file__).resolve().parent / "fixtures" / self.fixture_filename


STAGED_ERP_DOM_PROFILES: List[ERPDOMProfile] = [
    ERPDOMProfile(
        name="quickbooks_stage",
        fixture_filename="quickbooks_stage.html",
        description="QuickBooks style bill form with DocNumber and DocTotal fields.",
    ),
    ERPDOMProfile(
        name="xero_stage",
        fixture_filename="xero_stage.html",
        description="Xero style invoice form with aria-label and placeholder based selectors.",
    ),
    ERPDOMProfile(
        name="netsuite_stage",
        fixture_filename="netsuite_stage.html",
        description="NetSuite style form using canonical invoice_number/vendor/amount names.",
    ),
    ERPDOMProfile(
        name="sap_stage",
        fixture_filename="sap_stage.html",
        description="SAP style posting form using DocNumber/CardCode/DocTotal fields.",
    ),
]


REQUIRED_ERP_POST_COMMANDS = (
    "macro_post_find_entry",
    "macro_post_open_form",
    "macro_post_invoice_number",
    "macro_post_vendor",
    "macro_post_amount",
    "macro_post_submit",
)

