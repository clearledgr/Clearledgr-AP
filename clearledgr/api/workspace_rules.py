"""Workspace approval rules API — Module 3.

  GET    /api/workspace/rules                       list
  POST   /api/workspace/rules                       create
  PUT    /api/workspace/rules/{id}                  update
  DELETE /api/workspace/rules/{id}                  archive (soft)
  POST   /api/workspace/rules/test                  test mode (run sample
                                                    invoice through rule set)
  GET    /api/workspace/rules/{id}/versions         version history
  POST   /api/workspace/rules/{id}/revert/{version} one-click revert
  GET    /api/workspace/rules/templates             starter templates

Validation surface (every write endpoint goes through it):

  1. Schema validation via ``rule_engine.validate_rule_body``. Rejects
     malformed JSON before it hits the DB. 422 with the structured
     error list.
  2. Conflict detection via ``rule_engine.find_rule_conflicts``.
     Surfaces same-priority overlaps and redundant rules at save time
     (spec §131 — conflicts must NOT be silently resolved). 409 with
     the conflict list when ``force=false`` (default); 200 + warnings
     in the response when ``force=true`` so an operator can knowingly
     override after reading the trace.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from clearledgr.core.auth import TokenData, get_current_user
from clearledgr.core.database import get_db
from clearledgr.core.stores.rules_store import VALID_RULE_STATUSES, VALID_WORKFLOWS
from clearledgr.services import rule_engine

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/workspace/rules", tags=["workspace-rules"])


class RuleCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    entity_id: Optional[str] = None
    workflow: str = Field("ap")
    priority: int = Field(100, ge=0, le=9999)
    conditions: Dict[str, Any]
    actions: List[Dict[str, Any]]
    status: str = Field("active")
    force: bool = Field(False, description="bypass conflict warnings")


class RulePatchRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    entity_id: Optional[str] = None
    priority: Optional[int] = Field(None, ge=0, le=9999)
    conditions: Optional[Dict[str, Any]] = None
    actions: Optional[List[Dict[str, Any]]] = None
    status: Optional[str] = None
    change_note: Optional[str] = None
    force: bool = False


class TestRunRequest(BaseModel):
    invoice: Dict[str, Any]
    entity_id: Optional[str] = None
    rule_id: Optional[str] = Field(
        None, description="If set, evaluate against only this rule (preview before save).",
    )
    candidate_rule: Optional[Dict[str, Any]] = Field(
        None, description=(
            "If set, evaluate against this in-memory rule body (preview before save). "
            "Useful for the JSON-editor's 'try it' button."
        ),
    )


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _validation_errors_to_response(
    errors: List[rule_engine.ValidationError],
) -> Dict[str, Any]:
    return {
        "code": "rule_validation_failed",
        "message": "Rule body has schema errors. See `errors`.",
        "errors": [e.to_dict() for e in errors],
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/templates")
def list_templates(
    _user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    """The 4 starter templates — spec §123."""
    return {"templates": rule_engine.get_starter_templates()}


@router.post("/seed-defaults")
def seed_default_rules(
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    """Module 10 spec line 322: default rule sets pre-built for common patterns.

    Idempotent — refuses to re-seed if the org already has any rules.
    The leader can still delete + re-run, but accidental duplicate
    seeds are a no-op so the wizard's "Apply default rules" button
    is safe to click twice.

    Pulls the 4 starter templates (under-$1K auto-approve, $1K-$10K
    to manager, $10K-$50K to controller, $50K+ dual approval) and
    creates each as an active rule on the org.
    """
    db = get_db()
    org_id = getattr(user, "organization_id", None) or "default"

    existing = db.list_workspace_rules(org_id, include_inactive=True) if hasattr(db, "list_workspace_rules") else []
    if existing:
        return {
            "status": "already_seeded",
            "existing_count": len(existing),
            "message": "Org already has rules; skipping seed to avoid duplicates.",
        }

    templates = rule_engine.get_starter_templates()
    created = []
    for tpl in templates:
        try:
            rule = db.create_workspace_rule(
                organization_id=org_id,
                name=tpl.get("name") or "Untitled rule",
                description=tpl.get("description") or "",
                priority=int(tpl.get("priority") or 100),
                workflow=str(tpl.get("workflow") or "ap"),
                conditions=tpl.get("conditions") or {},
                actions=tpl.get("actions") or [],
                status="active",
                created_by=getattr(user, "email", None) or getattr(user, "user_id", None) or "system",
            )
            created.append(rule)
        except Exception as exc:
            logger.exception("[rules.seed-defaults] failed for template %s: %s", tpl.get("name"), exc)
            continue
    return {"status": "seeded", "created_count": len(created), "rules": created}


@router.get("")
def list_rules(
    workflow: Optional[str] = Query(None),
    entity_id: Optional[str] = Query(None),
    include_inactive: bool = Query(False),
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    if workflow and workflow not in VALID_WORKFLOWS:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_workflow",
                    "message": f"workflow must be one of {sorted(VALID_WORKFLOWS)}"},
        )
    db = get_db()
    list_kwargs: Dict[str, Any] = {
        "workflow": workflow,
        "include_inactive": include_inactive,
    }
    if entity_id is not None:
        list_kwargs["entity_id"] = entity_id
    rows = db.list_rules(user.organization_id, **list_kwargs)
    return {"rules": rows}


@router.post("/test")
def test_rule(
    body: TestRunRequest,
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    """Run an invoice context through the org's active rules and return
    the evaluation trace."""
    db = get_db()
    invoice_ctx = rule_engine.build_invoice_context(body.invoice)
    if body.entity_id is not None:
        invoice_ctx["entity_id"] = body.entity_id

    if body.candidate_rule is not None:
        # Preview mode — evaluate only the candidate body.
        validation_errors = rule_engine.validate_rule_body(
            conditions=body.candidate_rule.get("conditions"),
            actions=body.candidate_rule.get("actions"),
        )
        if validation_errors:
            raise HTTPException(
                status_code=422,
                detail=_validation_errors_to_response(validation_errors),
            )
        candidate = {
            "id": "_candidate",
            "name": body.candidate_rule.get("name") or "Candidate rule",
            "priority": int(body.candidate_rule.get("priority") or 100),
            "workflow": body.candidate_rule.get("workflow") or "ap",
            "entity_id": body.candidate_rule.get("entity_id"),
            "status": "active",
            "conditions": body.candidate_rule.get("conditions"),
            "actions": body.candidate_rule.get("actions"),
            "created_at": "0",
        }
        result = rule_engine.evaluate_rules(invoice_ctx, [candidate])
        return {"result": result.to_dict(), "invoice_context": invoice_ctx}

    if body.rule_id:
        rule = db.get_rule(body.rule_id)
        if not rule or rule.get("organization_id") != user.organization_id:
            raise HTTPException(status_code=404, detail="rule_not_found")
        result = rule_engine.evaluate_rules(invoice_ctx, [rule])
    else:
        rules = db.list_rules(
            user.organization_id,
            workflow=invoice_ctx.get("workflow") or "ap",
        )
        result = rule_engine.evaluate_rules(invoice_ctx, rules)
    return {"result": result.to_dict(), "invoice_context": invoice_ctx}


@router.post("")
def create_rule(
    body: RuleCreateRequest,
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    if body.workflow not in VALID_WORKFLOWS:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_workflow",
                    "message": f"workflow must be one of {sorted(VALID_WORKFLOWS)}"},
        )
    if body.status not in VALID_RULE_STATUSES:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_status",
                    "message": f"status must be one of {sorted(VALID_RULE_STATUSES)}"},
        )

    validation_errors = rule_engine.validate_rule_body(
        conditions=body.conditions, actions=body.actions,
    )
    if validation_errors:
        raise HTTPException(
            status_code=422,
            detail=_validation_errors_to_response(validation_errors),
        )

    db = get_db()
    candidate = {
        "id": "_candidate",
        "name": body.name,
        "priority": body.priority,
        "workflow": body.workflow,
        "entity_id": body.entity_id,
        "status": body.status,
        "conditions": body.conditions,
        "actions": body.actions,
        "created_at": "0",
    }
    existing = db.list_rules(user.organization_id, workflow=body.workflow)
    conflicts = rule_engine.find_rule_conflicts(candidate, existing)
    if conflicts and not body.force:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "rule_conflict",
                "message": (
                    "This rule conflicts with one or more existing active rules. "
                    "Review the conflicts; resubmit with force=true to save anyway."
                ),
                "conflicts": [c.to_dict() for c in conflicts],
            },
        )

    actor = getattr(user, "user_id", "") or getattr(user, "email", "")
    rule = db.create_rule({
        "organization_id": user.organization_id,
        "name": body.name,
        "description": body.description,
        "entity_id": body.entity_id,
        "workflow": body.workflow,
        "priority": body.priority,
        "conditions": body.conditions,
        "actions": body.actions,
        "status": body.status,
        "created_by": actor,
        "change_note": "Initial rule",
    })
    response: Dict[str, Any] = {"rule": rule}
    if conflicts:
        response["warnings"] = [c.to_dict() for c in conflicts]
    return response


@router.put("/{rule_id}")
def update_rule(
    rule_id: str,
    body: RulePatchRequest,
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    db = get_db()
    existing = db.get_rule(rule_id)
    if not existing or existing.get("organization_id") != user.organization_id:
        raise HTTPException(status_code=404, detail="rule_not_found")

    # Compose the post-update body for validation + conflict check
    new_conditions = body.conditions if body.conditions is not None else existing.get("conditions")
    new_actions = body.actions if body.actions is not None else existing.get("actions")
    if body.conditions is not None or body.actions is not None:
        validation_errors = rule_engine.validate_rule_body(
            conditions=new_conditions, actions=new_actions,
        )
        if validation_errors:
            raise HTTPException(
                status_code=422,
                detail=_validation_errors_to_response(validation_errors),
            )

    if body.status is not None and body.status not in VALID_RULE_STATUSES:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_status",
                    "message": f"status must be one of {sorted(VALID_RULE_STATUSES)}"},
        )

    # Conflict detection on the post-update body
    candidate = dict(existing)
    candidate.update({
        "name": body.name if body.name is not None else existing.get("name"),
        "priority": body.priority if body.priority is not None else existing.get("priority"),
        "entity_id": body.entity_id if body.entity_id is not None else existing.get("entity_id"),
        "conditions": new_conditions,
        "actions": new_actions,
        "status": body.status if body.status is not None else existing.get("status"),
    })
    other_rules = [
        r for r in db.list_rules(user.organization_id, workflow=existing.get("workflow", "ap"))
        if r.get("id") != rule_id
    ]
    conflicts = rule_engine.find_rule_conflicts(candidate, other_rules)
    if conflicts and not body.force:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "rule_conflict",
                "message": (
                    "Update conflicts with one or more existing active rules. "
                    "Resubmit with force=true to save anyway."
                ),
                "conflicts": [c.to_dict() for c in conflicts],
            },
        )

    actor = getattr(user, "user_id", "") or getattr(user, "email", "")
    fields: Dict[str, Any] = {}
    if body.name is not None:
        fields["name"] = body.name
    if body.description is not None:
        fields["description"] = body.description
    if body.entity_id is not None:
        fields["entity_id"] = body.entity_id
    if body.priority is not None:
        fields["priority"] = body.priority
    if body.conditions is not None:
        fields["conditions"] = body.conditions
    if body.actions is not None:
        fields["actions"] = body.actions
    if body.status is not None:
        fields["status"] = body.status

    updated = db.update_rule(
        rule_id, user.organization_id,
        actor=actor,
        change_note=body.change_note or "",
        **fields,
    )
    response: Dict[str, Any] = {"rule": updated}
    if conflicts:
        response["warnings"] = [c.to_dict() for c in conflicts]
    return response


@router.delete("/{rule_id}")
def delete_rule(
    rule_id: str,
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    db = get_db()
    existing = db.get_rule(rule_id)
    if not existing or existing.get("organization_id") != user.organization_id:
        raise HTTPException(status_code=404, detail="rule_not_found")
    deleted = db.delete_rule(rule_id, user.organization_id)
    return {"archived": bool(deleted), "rule_id": rule_id}


@router.get("/{rule_id}/versions")
def list_versions(
    rule_id: str,
    limit: int = Query(50, ge=1, le=200),
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    db = get_db()
    existing = db.get_rule(rule_id)
    if not existing or existing.get("organization_id") != user.organization_id:
        raise HTTPException(status_code=404, detail="rule_not_found")
    versions = db.list_rule_versions(rule_id, user.organization_id, limit=limit)
    return {"versions": versions}


@router.post("/{rule_id}/revert/{version}")
def revert_version(
    rule_id: str, version: int,
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    db = get_db()
    existing = db.get_rule(rule_id)
    if not existing or existing.get("organization_id") != user.organization_id:
        raise HTTPException(status_code=404, detail="rule_not_found")
    actor = getattr(user, "user_id", "") or getattr(user, "email", "")
    reverted = db.revert_rule_to_version(
        rule_id, int(version), user.organization_id, actor=actor,
    )
    if reverted is None:
        raise HTTPException(status_code=404, detail="version_not_found")
    return {"rule": reverted}
