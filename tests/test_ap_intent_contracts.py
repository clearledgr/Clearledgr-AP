from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.services.finance_skills.ap_intent_contracts import (  # noqa: E402
    build_operator_copy,
    get_intent_audit_contract,
)


def test_audit_contract_registry_returns_canonical_events():
    contract = get_intent_audit_contract("request_approval")
    assert contract["mutates_ap_state"] is True
    assert contract["events"] == [
        "approval_request_routed",
        "approval_request_blocked",
        "approval_request_failed",
    ]


def test_operator_copy_registry_switches_recommendation_by_eligibility():
    allowed = build_operator_copy("post_to_erp", eligible=True)
    blocked = build_operator_copy("post_to_erp", eligible=False)

    assert allowed["recommended_now"] == "Post this invoice to ERP."
    assert blocked["recommended_now"] == "Wait until the invoice reaches a postable state."
