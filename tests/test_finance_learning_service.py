from __future__ import annotations

from clearledgr.core.database import ClearledgrDB
from clearledgr.services.finance_learning import FinanceLearningService


def test_finance_learning_records_outcome_calibration_from_runtime_outcome(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret-key")
    db = ClearledgrDB(str(tmp_path / "finance-learning.db"))
    db.initialize()

    service = FinanceLearningService("test-org", db=db)
    result = service.record_runtime_outcome(
        ap_item={
            "id": "ap-1",
            "vendor_name": "Calibrated Vendor",
            "amount": 100.0,
            "currency": "USD",
        },
        response={
            "status": "posted_to_erp",
            "gl_code": "5200",
            "post_verified": True,
        },
        shadow_decision={"proposed_action": "auto_approve_post"},
        actor_id="tester",
    )
    calibration = service.get_outcome_calibration(
        vendor_name="Calibrated Vendor",
        action_key="auto_approve_post",
    )

    assert result["recorded"]
    assert calibration["sample_count"] == 1
    assert calibration["success_rate"] == 1.0
    assert calibration["shadow_match_rate"] == 1.0
    assert calibration["verification_rate"] == 1.0
