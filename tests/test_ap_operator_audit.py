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
    assert row["operator_importance"] == "medium"
    assert row["operator_category"] == "policy"
    assert row["operator_evidence_label"] == "Policy check"
    assert "workflow and policy guardrails" in str(row["operator_evidence_detail"]).lower()
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
    assert row["operator_importance"] == "medium"
    assert row["operator_category"] == "posting"
    assert row["operator_evidence_label"] == "ERP workflow"
    assert "posting workflow state change" in str(row["operator_evidence_detail"]).lower()


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


def test_normalize_operator_audit_event_maps_auto_escalation_reason():
    row = normalize_operator_audit_event(
        {
            "id": "evt-5b",
            "event_type": "approval_escalation_sent",
            "reason": "approval_escalation_auto_6h",
        }
    )
    assert row["operator_code"] == "approval_escalation_sent"
    assert row["operator_title"] == "Approval escalated"
    assert "after 6 hours pending" in str(row["operator_message"]).lower()


def test_normalize_operator_audit_event_maps_approval_nudge_failed():
    nudge_failed = normalize_operator_audit_event(
        {
            "id": "evt-7",
            "event_type": "approval_nudge_failed",
            "reason": "approval_nudge",
        }
    )

    assert nudge_failed["operator_title"] == "Approval reminder failed"
    assert "nudge approver" in str(nudge_failed["operator_message"]).lower()
    assert nudge_failed["operator_action_hint"] == 'Retry "Nudge approver".'


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

    assert approval_sent["operator_title"] == "Approval requested"
    assert "routed" in str(approval_sent["operator_message"]).lower()
    assert approval_sent["operator_importance"] == "high"
    assert approval_sent["operator_evidence_label"] == "Approval action"

    assert erp_posted["operator_title"] == "Posted to ERP"
    assert "completed successfully" in str(erp_posted["operator_message"]).lower()
    assert erp_posted["operator_importance"] == "high"
    assert erp_posted["operator_evidence_label"] == "ERP result"

    assert retry_done["operator_title"] == "Retry completed"
    assert "retried" in str(retry_done["operator_message"]).lower()
    assert retry_done["operator_importance"] == "medium"

    assert followup_ready["operator_title"] == "Vendor follow-up prepared"
    assert "draft is ready" in str(followup_ready["operator_message"]).lower()
    assert followup_ready["operator_evidence_label"] == "Vendor follow-up"


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


def test_normalize_operator_audit_event_links_rejection_to_channel_evidence():
    row = normalize_operator_audit_event(
        {
            "id": "evt-14",
            "event_type": "invoice_rejected",
            "payload_json": {
                "source_channel": "teams",
            },
        }
    )

    assert row["operator_title"] == "Rejected"
    assert row["operator_importance"] == "high"
    assert row["operator_category"] == "approval"
    assert row["operator_evidence_label"] == "Teams approval action"
    assert "teams approval workflow" in str(row["operator_evidence_detail"]).lower()


def test_normalize_operator_audit_event_makes_field_corrections_and_summary_events_operator_readable():
    correction = normalize_operator_audit_event(
        {
            "id": "evt-15",
            "event_type": "field_correction",
            "payload_json": {
                "field": "vendor_name",
            },
        }
    )
    summary = normalize_operator_audit_event(
        {
            "id": "evt-16",
            "event_type": "finance_summary_share_prepared",
        }
    )

    assert correction["operator_title"] == "Field corrected"
    assert "vendor name was corrected" in str(correction["operator_message"]).lower()
    assert correction["operator_evidence_label"] == "Field correction"
    assert "operator correction to vendor name" in str(correction["operator_evidence_detail"]).lower()

    assert summary["operator_title"] == "Finance summary prepared"
    assert summary["operator_importance"] == "low"
    assert summary["operator_category"] == "collaboration"
    assert summary["operator_evidence_label"] == "Record summary"
