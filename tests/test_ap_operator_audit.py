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
    assert row["operator_action_hint"] == "Review blocking checks and route for approval."
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

    assert retry_blocked["operator_title"] == "Action blocked for safety"
    assert "Automatic retry was blocked" in str(retry_blocked["operator_message"])

    assert illegal_transition["operator_title"] == "Action blocked for safety"
    assert "current invoice status" in str(illegal_transition["operator_message"])


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
    assert row["operator_action_hint"] is None


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
    assert row["operator_action_hint"] == "Wait for approval callback."


def test_normalize_operator_audit_event_maps_approval_nudge_failed_and_browser_fallback_prepared():
    nudge_failed = normalize_operator_audit_event(
        {
            "id": "evt-7",
            "event_type": "approval_nudge_failed",
            "reason": "approval_nudge",
        }
    )
    fallback_ready = normalize_operator_audit_event(
        {
            "id": "evt-8",
            "event_type": "browser_session_created",
            "reason": "browser_session_created",
        }
    )

    assert nudge_failed["operator_title"] == "Approval reminder failed"
    assert "nudge approver" in str(nudge_failed["operator_message"]).lower()
    assert nudge_failed["operator_action_hint"] == 'Retry "Nudge approver".'

    assert fallback_ready["operator_title"] == "ERP fallback prepared"
    assert "prepared secure erp browser fallback session" in str(fallback_ready["operator_message"]).lower()
    assert fallback_ready["operator_action_hint"] == "Continue approval/posting flow."


def test_normalize_operator_audit_event_maps_erp_fallback_requested():
    fallback_requested = normalize_operator_audit_event(
        {
            "id": "evt-9",
            "event_type": "erp_api_fallback_requested",
            "reason": "fallback_preview_confirmed_and_dispatched",
        }
    )
    assert fallback_requested["operator_title"] == "ERP fallback prepared"
    assert "fallback" in str(fallback_requested["operator_message"]).lower()


def test_normalize_operator_audit_event_maps_runtime_event_classes_to_plain_language():
    approval_sent = normalize_operator_audit_event(
        {
            "id": "evt-10",
            "event_type": "approval_request_routed",
        }
    )
    erp_posted = normalize_operator_audit_event(
        {
            "id": "evt-11",
            "event_type": "erp_post_completed",
        }
    )
    retry_done = normalize_operator_audit_event(
        {
            "id": "evt-12",
            "event_type": "retry_recoverable_failure_completed",
        }
    )
    followup_ready = normalize_operator_audit_event(
        {
            "id": "evt-13",
            "event_type": "vendor_followup_draft_prepared",
        }
    )

    assert approval_sent["operator_title"] == "Approval request sent"
    assert "routed" in str(approval_sent["operator_message"]).lower()

    assert erp_posted["operator_title"] == "Posted to ERP"
    assert "completed successfully" in str(erp_posted["operator_message"]).lower()

    assert retry_done["operator_title"] == "Retry completed"
    assert "retried" in str(retry_done["operator_message"]).lower()

    assert followup_ready["operator_title"] == "Vendor follow-up prepared"
    assert "draft is ready" in str(followup_ready["operator_message"]).lower()


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
