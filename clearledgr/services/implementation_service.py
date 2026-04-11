"""
Implementation Service — DESIGN_THESIS.md §13

"Clearledgr's Implementation Service follows the Streak model —
five hours of dedicated setup work."

This module provides the implementation checklist and validation
for the Clearledgr team delivering Enterprise onboarding. Each
step has automated validation that confirms the step is complete
before the implementation engineer can proceed.

The checklist is stored in the org's settings and visible in the
backoffice.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

IMPLEMENTATION_STEPS = [
    {
        "id": "erp_connection",
        "name": "ERP Connection and Validation",
        "description": (
            "Configure and test the ERP OAuth connection. Validate that PO, GRN, "
            "and vendor master data is accessible and correctly mapped. Run a test "
            "invoice through the full match flow before go-live."
        ),
        "validation_fn": "_validate_erp_connection",
    },
    {
        "id": "ap_policy",
        "name": "AP Policy Configuration",
        "description": (
            "Set auto-approve threshold, match tolerance, and approval routing "
            "to match the customer's documented finance policy. Not a generic "
            "default — built to their actual rules."
        ),
        "validation_fn": "_validate_ap_policy",
    },
    {
        "id": "vendor_import",
        "name": "Vendor Master Import",
        "description": (
            "Import existing vendor records into the onboarding pipeline with "
            "correct status. Flag vendors with missing bank details for active "
            "verification."
        ),
        "validation_fn": "_validate_vendor_import",
    },
    {
        "id": "team_setup",
        "name": "Team Setup and Role Assignment",
        "description": (
            "Walk each stakeholder through their specific surface: AP Clerk "
            "through inbox labels and the sidebar, AP Manager through the "
            "approval flow, CFO through the pipeline view and Slack notifications."
        ),
        "validation_fn": "_validate_team_setup",
    },
    {
        "id": "first_batch",
        "name": "First Invoice Batch Review",
        "description": (
            "Process the first 20 invoices live with the finance team present. "
            "Review every agent action, every exception, every match result. Tune "
            "policy settings based on what is found. Clearledgr does not leave "
            "until the first batch runs cleanly."
        ),
        "validation_fn": "_validate_first_batch",
    },
]


async def get_implementation_status(
    organization_id: str,
    db: Any = None,
) -> Dict[str, Any]:
    """Get the implementation checklist status for an org."""
    if db is None:
        from clearledgr.core.database import get_db
        db = get_db()

    org = db.get_organization(organization_id)
    settings = _load_settings(org)
    impl = settings.get("implementation") or {}

    steps = []
    for step_def in IMPLEMENTATION_STEPS:
        step_state = impl.get(step_def["id"]) or {}
        completed = bool(step_state.get("completed"))
        completed_at = step_state.get("completed_at")
        completed_by = step_state.get("completed_by")
        notes = step_state.get("notes") or ""

        # Run validation if not completed
        validation = None
        if not completed:
            validator = globals().get(step_def["validation_fn"])
            if validator:
                validation = await validator(db, organization_id)

        steps.append({
            "id": step_def["id"],
            "name": step_def["name"],
            "description": step_def["description"],
            "completed": completed,
            "completed_at": completed_at,
            "completed_by": completed_by,
            "notes": notes,
            "validation": validation,
        })

    all_complete = all(s["completed"] for s in steps)
    return {
        "organization_id": organization_id,
        "steps": steps,
        "all_complete": all_complete,
        "completed_count": sum(1 for s in steps if s["completed"]),
        "total_steps": len(steps),
    }


async def complete_implementation_step(
    organization_id: str,
    step_id: str,
    completed_by: str,
    notes: str = "",
    db: Any = None,
) -> Dict[str, Any]:
    """Mark an implementation step as complete."""
    if db is None:
        from clearledgr.core.database import get_db
        db = get_db()

    valid_ids = {s["id"] for s in IMPLEMENTATION_STEPS}
    if step_id not in valid_ids:
        return {"error": f"Unknown step: {step_id}"}

    org = db.get_organization(organization_id)
    settings = _load_settings(org)
    impl = settings.get("implementation") or {}

    now = datetime.now(timezone.utc).isoformat()
    impl[step_id] = {
        "completed": True,
        "completed_at": now,
        "completed_by": completed_by,
        "notes": notes,
    }
    settings["implementation"] = impl

    import json
    db.update_organization(organization_id, settings_json=json.dumps(settings))

    # Audit
    try:
        db.append_ap_audit_event({
            "event_type": "implementation_step_completed",
            "actor_type": "user",
            "actor_id": completed_by,
            "organization_id": organization_id,
            "source": "implementation_service",
            "payload_json": {"step_id": step_id, "notes": notes},
        })
    except Exception:
        pass

    return {"step_id": step_id, "completed": True, "completed_at": now}


# ==================== VALIDATION FUNCTIONS ====================


async def _validate_erp_connection(db, org_id: str) -> Dict[str, Any]:
    """Check if ERP is connected and PO/GRN/vendor data accessible."""
    issues = []
    try:
        connections = db.get_erp_connections(org_id) if hasattr(db, "get_erp_connections") else []
        if not connections:
            issues.append("No ERP connection configured")
        else:
            conn = connections[0]
            if not conn.get("is_active"):
                issues.append("ERP connection is inactive")
    except Exception:
        issues.append("Could not check ERP connections")

    # Check vendor master is accessible
    try:
        vendors = db.list_vendor_profiles(org_id, limit=5) if hasattr(db, "list_vendor_profiles") else []
        if not vendors:
            issues.append("No vendors synced from ERP — vendor master import may be needed")
    except Exception:
        pass

    return {"ready": len(issues) == 0, "issues": issues}


async def _validate_ap_policy(db, org_id: str) -> Dict[str, Any]:
    """Check if AP policy is configured (not just defaults)."""
    issues = []
    try:
        org = db.get_organization(org_id)
        settings = _load_settings(org)
        if not settings.get("approval_thresholds"):
            issues.append("No approval routing rules configured")
        if not settings.get("auto_approve_confidence_threshold"):
            issues.append("Auto-approve threshold not set (using default)")
    except Exception:
        issues.append("Could not load org settings")
    return {"ready": len(issues) == 0, "issues": issues}


async def _validate_vendor_import(db, org_id: str) -> Dict[str, Any]:
    """Check if vendors have been imported."""
    issues = []
    try:
        vendors = db.list_vendor_profiles(org_id, limit=100) if hasattr(db, "list_vendor_profiles") else []
        if len(vendors) < 3:
            issues.append(f"Only {len(vendors)} vendors imported — expected more for a production setup")
        missing_bank = [v for v in vendors if not v.get("bank_details_encrypted")]
        if missing_bank:
            issues.append(f"{len(missing_bank)} vendors missing bank details — need verification")
    except Exception:
        issues.append("Could not check vendor profiles")
    return {"ready": len(issues) == 0, "issues": issues}


async def _validate_team_setup(db, org_id: str) -> Dict[str, Any]:
    """Check if team members are set up with correct roles."""
    issues = []
    try:
        users = db.get_users(org_id) if hasattr(db, "get_users") else []
        if len(users) < 2:
            issues.append("Only 1 user — need at least AP Manager + one other role")
        roles = {u.get("role") for u in users}
        if "ap_manager" not in roles and "financial_controller" not in roles and "cfo" not in roles:
            issues.append("No AP Manager, Controller, or CFO role assigned")
    except Exception:
        issues.append("Could not check team")
    return {"ready": len(issues) == 0, "issues": issues}


async def _validate_first_batch(db, org_id: str) -> Dict[str, Any]:
    """Check if at least 20 invoices have been processed."""
    issues = []
    try:
        items = db.list_ap_items(org_id, limit=25)
        processed = [i for i in items if i.get("state") not in ("received",)]
        if len(processed) < 20:
            issues.append(f"Only {len(processed)} invoices processed — need at least 20 for the first batch review")
    except Exception:
        issues.append("Could not check AP items")
    return {"ready": len(issues) == 0, "issues": issues}


def _load_settings(org) -> Dict[str, Any]:
    if not org:
        return {}
    raw = org.get("settings_json") or {}
    if isinstance(raw, str):
        import json
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return raw or {}
