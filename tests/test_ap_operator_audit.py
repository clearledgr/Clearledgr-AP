from clearledgr.services.ap_operator_audit import (
    normalize_operator_audit_event,
    normalize_operator_audit_events,
)


def test_normalize_operator_audit_event_maps_validation_reason_codes():
    row = normalize_operator_audit_event(
        {
            "id": "evt-1",
            "event_type": "deterministic_validation_failed",
            "decision_reason": "policy_requirement_amt_500,po_match_no_gr,confidence_field_review_required",
        }
    )
    assert row["operator_code"] == "validation_failed"
    assert row["operator_title"] == "Validation checks failed"
    assert "Policy requires approval for invoices above $500." in row["operator_message"]
    assert "PO/GR check failed because goods receipt is missing." in row["operator_message"]
    assert row["operator_severity"] == "warning"
    assert isinstance(row.get("operator"), dict)


def test_normalize_operator_audit_event_distinguishes_blocked_retry_vs_transition():
    retry_blocked = normalize_operator_audit_event(
        {
            "id": "evt-2",
            "event_type": "state_transition_rejected",
            "decision_reason": "autonomous_retry_attempt",
        }
    )
    illegal_transition = normalize_operator_audit_event(
        {
            "id": "evt-3",
            "event_type": "state_transition_rejected",
            "decision_reason": "illegal_transition",
        }
    )

    assert retry_blocked["operator_title"] == "Retry paused"
    assert "Auto-retry is paused" in str(retry_blocked["operator_message"])

    assert illegal_transition["operator_title"] == "Step blocked"
    assert "required status" in str(illegal_transition["operator_message"])


def test_normalize_operator_audit_events_adds_operator_contract_fields():
    rows = normalize_operator_audit_events(
        [
            {
                "id": "evt-4",
                "event_type": "state_transition",
                "from_state": "needs_approval",
                "to_state": "ready_to_post",
            }
        ]
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["operator_code"] == "state_transition:ready_to_post"
    assert row["operator_title"] == "Status updated: Ready to post"
    assert "Moved from Needs approval to Ready to post." == row["operator_message"]


def test_normalize_operator_audit_event_maps_nudge_sent_alias_and_auto_reason():
    row = normalize_operator_audit_event(
        {
            "id": "evt-5",
            "event_type": "approval_nudge_sent",
            "reason": "approval_nudge_auto_4h",
        }
    )
    assert row["operator_code"] == "approval_reminder_sent"
    assert row["operator_title"] == "Reminder sent"
    assert "automatic approval reminder" in str(row["operator_message"]).lower()


def test_normalize_operator_audit_event_prefers_canonical_mapping_over_stale_operator_payload():
    row = normalize_operator_audit_event(
        {
            "id": "evt-6",
            "event_type": "deterministic_validation_failed",
            "decision_reason": "policy_requirement_amt_500",
            "operator": {
                "title": "Deterministic Validation Failed",
                "message": "policy_requirement_amt_500",
            },
        }
    )
    assert row["operator_title"] == "Validation checks failed"
    assert "requires approval" in str(row["operator_message"]).lower()
