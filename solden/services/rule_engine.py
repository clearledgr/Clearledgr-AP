"""Rule engine — Module 3 (workspace approval rules).

Two layers:

  - **Schema validation** (``validate_rule_body``) — checked at the
    API layer before any rule lands in the DB. Rejects malformed
    JSON before it can confuse the evaluator.
  - **Evaluation** (``evaluate_rules``) — given an AP item context
    and a list of active rules, returns the FIRST matching rule
    plus a structured trace showing why each rule did or didn't
    match. Test-mode in the API surfaces the same trace.

Conditions schema (the body the operator writes in the JSON editor):

    {
      "all_of": [{"field": "...", "op": "...", "value": ...}, ...],
      "any_of": [{"field": "...", "op": "...", "value": ...}, ...]
    }

  - ``all_of`` clauses must all match (AND).
  - ``any_of`` clauses match if any one matches (OR).
  - When both are present, ``all_of`` AND ``any_of`` both must hold.
  - Empty/missing clause arrays are no-ops.

Operators: eq, ne, lt, lte, gt, gte, in, not_in, matches (glob),
contains.

Fields the engine knows about (others surface as a validation error):
amount, currency, vendor_name, vendor_id, gl_code, department,
entity_id, invoice_age_days, vendor_age_days, workflow.

Actions schema (a list of action specs applied when the rule matches):

    [
      {"type": "auto_approve"},
      {"type": "route_to_role", "role": "ap_manager"},
      {"type": "route_to_user", "user_email": "sara@co.com"},
      {"type": "require_n_approvals", "n": 2},
      {"type": "require_dual_approval"},
      {"type": "escalate_after", "hours": 24, "to": "controller@co.com"},
      {"type": "hold_for_finance_review"}
    ]

Multiple actions can apply; the workflow processes them in order.

Conflict detection (``find_rule_conflicts``):
  Surfaces two flavours at save time:
    - ``same_priority_overlap``: two active rules at the same priority
      with overlapping conditions. The evaluator order is unstable
      (tie-broken on created_at), so this is a real correctness risk.
    - ``redundant``: an existing higher-priority rule already matches
      every input the new rule would catch — the new rule never
      fires.
  Detection runs the candidate plus existing rules through a fixed
  set of probe invoices. Probes are deterministic so the same set of
  rules always reports the same conflict signal — no flaky save-time
  warnings.
"""
from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


VALID_OPERATORS = frozenset({
    "eq", "ne", "lt", "lte", "gt", "gte",
    "in", "not_in", "matches", "contains",
})

VALID_FIELDS = frozenset({
    "amount", "currency", "vendor_name", "vendor_id",
    "gl_code", "department", "entity_id",
    "invoice_age_days", "vendor_age_days", "workflow",
})

VALID_ACTION_TYPES = frozenset({
    "auto_approve",
    "route_to_role",
    "route_to_user",
    "require_n_approvals",
    "require_dual_approval",
    "escalate_after",
    "hold_for_finance_review",
})


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

@dataclass
class ValidationError:
    code: str
    message: str
    path: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "message": self.message, "path": self.path}


def validate_rule_body(
    *, conditions: Any, actions: Any,
) -> List[ValidationError]:
    """Validate the rule body. Returns a list of errors; empty list = OK."""
    errors: List[ValidationError] = []
    errors.extend(_validate_conditions(conditions))
    errors.extend(_validate_actions(actions))
    return errors


def _validate_conditions(conditions: Any) -> List[ValidationError]:
    errors: List[ValidationError] = []
    if not isinstance(conditions, dict):
        errors.append(ValidationError(
            code="conditions_not_object",
            message="conditions must be a JSON object with all_of and/or any_of",
            path="conditions",
        ))
        return errors

    has_clauses = False
    for clause_key in ("all_of", "any_of"):
        clauses = conditions.get(clause_key)
        if clauses is None:
            continue
        has_clauses = True
        if not isinstance(clauses, list):
            errors.append(ValidationError(
                code="clause_not_array",
                message=f"{clause_key} must be a JSON array",
                path=f"conditions.{clause_key}",
            ))
            continue
        for idx, clause in enumerate(clauses):
            errors.extend(_validate_clause(
                clause, path=f"conditions.{clause_key}[{idx}]",
            ))

    if not has_clauses:
        errors.append(ValidationError(
            code="no_clauses",
            message="conditions must include at least one of all_of / any_of",
            path="conditions",
        ))
    return errors


def _validate_clause(clause: Any, *, path: str) -> List[ValidationError]:
    errors: List[ValidationError] = []
    if not isinstance(clause, dict):
        errors.append(ValidationError(
            code="clause_not_object", message="clause must be an object", path=path,
        ))
        return errors

    field_name = clause.get("field")
    op = clause.get("op")

    if field_name not in VALID_FIELDS:
        errors.append(ValidationError(
            code="unknown_field",
            message=(
                f"field must be one of {sorted(VALID_FIELDS)}; "
                f"got {field_name!r}"
            ),
            path=f"{path}.field",
        ))
    if op not in VALID_OPERATORS:
        errors.append(ValidationError(
            code="unknown_op",
            message=f"op must be one of {sorted(VALID_OPERATORS)}; got {op!r}",
            path=f"{path}.op",
        ))
    if "value" not in clause:
        errors.append(ValidationError(
            code="missing_value",
            message="clause must include a value field",
            path=f"{path}.value",
        ))

    if op in ("in", "not_in") and "value" in clause:
        if not isinstance(clause["value"], list):
            errors.append(ValidationError(
                code="value_must_be_array",
                message=f"op={op!r} requires value to be a JSON array",
                path=f"{path}.value",
            ))

    return errors


def _validate_actions(actions: Any) -> List[ValidationError]:
    errors: List[ValidationError] = []
    if not isinstance(actions, list):
        errors.append(ValidationError(
            code="actions_not_array",
            message="actions must be a JSON array of action objects",
            path="actions",
        ))
        return errors
    if not actions:
        errors.append(ValidationError(
            code="actions_empty",
            message="actions must contain at least one action",
            path="actions",
        ))
        return errors

    for idx, action in enumerate(actions):
        path = f"actions[{idx}]"
        if not isinstance(action, dict):
            errors.append(ValidationError(
                code="action_not_object", message="action must be an object", path=path,
            ))
            continue
        action_type = action.get("type")
        if action_type not in VALID_ACTION_TYPES:
            errors.append(ValidationError(
                code="unknown_action_type",
                message=(
                    f"type must be one of {sorted(VALID_ACTION_TYPES)}; "
                    f"got {action_type!r}"
                ),
                path=f"{path}.type",
            ))
            continue

        if action_type == "route_to_role" and not action.get("role"):
            errors.append(ValidationError(
                code="missing_role",
                message="route_to_role requires a 'role' field",
                path=f"{path}.role",
            ))
        if action_type == "route_to_user" and not action.get("user_email"):
            errors.append(ValidationError(
                code="missing_user_email",
                message="route_to_user requires a 'user_email' field",
                path=f"{path}.user_email",
            ))
        if action_type == "require_n_approvals":
            n = action.get("n")
            if not isinstance(n, int) or n < 1 or n > 10:
                errors.append(ValidationError(
                    code="invalid_n",
                    message="require_n_approvals.n must be an integer 1-10",
                    path=f"{path}.n",
                ))
        if action_type == "escalate_after":
            hours = action.get("hours")
            if not isinstance(hours, (int, float)) or hours <= 0:
                errors.append(ValidationError(
                    code="invalid_hours",
                    message="escalate_after.hours must be a positive number",
                    path=f"{path}.hours",
                ))
            if not action.get("to"):
                errors.append(ValidationError(
                    code="missing_to",
                    message="escalate_after requires a 'to' recipient",
                    path=f"{path}.to",
                ))
    return errors


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

@dataclass
class ClauseTrace:
    field: str
    op: str
    expected: Any
    actual: Any
    matched: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field": self.field, "op": self.op,
            "expected": self.expected, "actual": self.actual,
            "matched": self.matched,
        }


@dataclass
class RuleTrace:
    rule_id: str
    rule_name: str
    priority: int
    matched: bool
    skipped_reason: Optional[str] = None
    all_of_traces: List[ClauseTrace] = field(default_factory=list)
    any_of_traces: List[ClauseTrace] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "priority": self.priority,
            "matched": self.matched,
            "skipped_reason": self.skipped_reason,
            "all_of": [t.to_dict() for t in self.all_of_traces],
            "any_of": [t.to_dict() for t in self.any_of_traces],
        }


@dataclass
class EvaluationResult:
    matched_rule: Optional[Dict[str, Any]]
    matched_actions: List[Dict[str, Any]]
    rule_trace: List[RuleTrace]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "matched_rule_id": (self.matched_rule or {}).get("id"),
            "matched_rule_name": (self.matched_rule or {}).get("name"),
            "actions": list(self.matched_actions),
            "trace": [t.to_dict() for t in self.rule_trace],
        }


def evaluate_rules(
    invoice_context: Dict[str, Any],
    rules: List[Dict[str, Any]],
) -> EvaluationResult:
    """Run an AP item context through a list of rules in priority order.

    Returns the first matching rule's actions plus the full trace of
    every rule's evaluation (for test-mode display).
    """
    sorted_rules = sorted(
        rules,
        key=lambda r: (
            int(r.get("priority", 100)),
            str(r.get("created_at", "")),
        ),
    )
    trace: List[RuleTrace] = []
    matched_rule: Optional[Dict[str, Any]] = None
    matched_actions: List[Dict[str, Any]] = []

    for rule in sorted_rules:
        if rule.get("status") != "active":
            trace.append(RuleTrace(
                rule_id=rule.get("id", ""),
                rule_name=rule.get("name", ""),
                priority=int(rule.get("priority", 100)),
                matched=False,
                skipped_reason=f"status={rule.get('status', 'unknown')}",
            ))
            continue

        rule_workflow = rule.get("workflow", "ap")
        ctx_workflow = invoice_context.get("workflow", "ap")
        if rule_workflow and rule_workflow != ctx_workflow:
            trace.append(RuleTrace(
                rule_id=rule.get("id", ""),
                rule_name=rule.get("name", ""),
                priority=int(rule.get("priority", 100)),
                matched=False,
                skipped_reason=f"workflow_mismatch:{rule_workflow}!={ctx_workflow}",
            ))
            continue

        rule_entity = rule.get("entity_id")
        ctx_entity = invoice_context.get("entity_id")
        if rule_entity and rule_entity != ctx_entity:
            trace.append(RuleTrace(
                rule_id=rule.get("id", ""),
                rule_name=rule.get("name", ""),
                priority=int(rule.get("priority", 100)),
                matched=False,
                skipped_reason=f"entity_mismatch:{rule_entity}!={ctx_entity}",
            ))
            continue

        all_traces, all_matched = _evaluate_clauses(
            (rule.get("conditions") or {}).get("all_of") or [],
            invoice_context, mode="all",
        )
        any_traces, any_matched = _evaluate_clauses(
            (rule.get("conditions") or {}).get("any_of") or [],
            invoice_context, mode="any",
        )

        rule_matched = all_matched and any_matched
        rt = RuleTrace(
            rule_id=rule.get("id", ""),
            rule_name=rule.get("name", ""),
            priority=int(rule.get("priority", 100)),
            matched=rule_matched,
            all_of_traces=all_traces,
            any_of_traces=any_traces,
        )
        trace.append(rt)

        if rule_matched and matched_rule is None:
            matched_rule = rule
            matched_actions = list(rule.get("actions") or [])
            # Continue iteration for full trace, but the first match
            # wins — subsequent rules show as "skipped:already_matched".

    # Annotate post-match rules
    seen_match = False
    for rt in trace:
        if rt.matched:
            seen_match = True
            continue
        if seen_match and rt.skipped_reason is None:
            rt.skipped_reason = "rule_above_already_matched"

    return EvaluationResult(
        matched_rule=matched_rule,
        matched_actions=matched_actions,
        rule_trace=trace,
    )


def _evaluate_clauses(
    clauses: List[Dict[str, Any]],
    context: Dict[str, Any], *, mode: str,
) -> Tuple[List[ClauseTrace], bool]:
    if not clauses:
        return [], True  # No clauses = vacuously true

    traces: List[ClauseTrace] = []
    matched_any = False
    matched_all = True

    for clause in clauses:
        field_name = clause.get("field")
        op = clause.get("op")
        expected = clause.get("value")
        actual = context.get(field_name)
        ok = _evaluate_op(op, actual, expected)
        traces.append(ClauseTrace(
            field=str(field_name or ""), op=str(op or ""),
            expected=expected, actual=actual, matched=ok,
        ))
        if ok:
            matched_any = True
        else:
            matched_all = False

    if mode == "all":
        return traces, matched_all
    return traces, matched_any


def _evaluate_op(op: Any, actual: Any, expected: Any) -> bool:
    op = str(op or "").lower()
    try:
        if op == "eq":
            return actual == expected
        if op == "ne":
            return actual != expected
        if op == "lt":
            return _to_number(actual) < _to_number(expected)
        if op == "lte":
            return _to_number(actual) <= _to_number(expected)
        if op == "gt":
            return _to_number(actual) > _to_number(expected)
        if op == "gte":
            return _to_number(actual) >= _to_number(expected)
        if op == "in":
            return actual in (expected or [])
        if op == "not_in":
            return actual not in (expected or [])
        if op == "matches":
            if actual is None:
                return False
            return fnmatch.fnmatch(str(actual), str(expected))
        if op == "contains":
            if actual is None:
                return False
            return str(expected) in str(actual)
    except (TypeError, ValueError):
        return False
    return False


def _to_number(value: Any) -> float:
    if value is None:
        raise ValueError("none")
    return float(value)


# ---------------------------------------------------------------------------
# Conflict detection
# ---------------------------------------------------------------------------

# Probe invoices used to detect rule overlap. Deterministic — same
# input always produces the same conflict signal.
_PROBE_INVOICES: List[Dict[str, Any]] = [
    {"amount": 500, "currency": "USD", "vendor_name": "Acme Corp",
     "gl_code": "5000", "department": "engineering", "entity_id": None,
     "invoice_age_days": 0, "vendor_age_days": 30, "workflow": "ap"},
    {"amount": 5000, "currency": "USD", "vendor_name": "Acme Corp",
     "gl_code": "5000", "department": "engineering", "entity_id": None,
     "invoice_age_days": 5, "vendor_age_days": 30, "workflow": "ap"},
    {"amount": 25000, "currency": "USD", "vendor_name": "Beta Vendors LLC",
     "gl_code": "6100", "department": "marketing", "entity_id": None,
     "invoice_age_days": 10, "vendor_age_days": 200, "workflow": "ap"},
    {"amount": 100000, "currency": "USD", "vendor_name": "Megacorp Holdings",
     "gl_code": "7000", "department": "operations", "entity_id": None,
     "invoice_age_days": 1, "vendor_age_days": 1000, "workflow": "ap"},
    {"amount": 750, "currency": "EUR", "vendor_name": "Café Paris",
     "gl_code": "5500", "department": "office", "entity_id": "eu-1",
     "invoice_age_days": 30, "vendor_age_days": 90, "workflow": "ap"},
]


@dataclass
class RuleConflict:
    kind: str
    rule_a: Dict[str, Any]
    rule_b: Dict[str, Any]
    note: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "rule_a_id": self.rule_a.get("id"),
            "rule_a_name": self.rule_a.get("name"),
            "rule_b_id": self.rule_b.get("id"),
            "rule_b_name": self.rule_b.get("name"),
            "note": self.note,
        }


def find_rule_conflicts(
    candidate: Dict[str, Any],
    existing_rules: List[Dict[str, Any]],
) -> List[RuleConflict]:
    """Detect overlap between the candidate rule and existing rules.

    Returns a list of conflict descriptions (empty = no conflict).
    The two flavours surfaced today:
      - ``same_priority_overlap``: candidate + existing rule at the
        same priority both match the same probe invoice.
      - ``redundant``: an existing higher-priority rule matches
        every probe the candidate would catch, so the candidate
        never fires.
    """
    conflicts: List[RuleConflict] = []
    candidate_status = candidate.get("status") or "active"
    if candidate_status != "active":
        return conflicts

    candidate_priority = int(candidate.get("priority") or 100)

    # Which probes the candidate matches.
    candidate_matches = {
        idx for idx, probe in enumerate(_PROBE_INVOICES)
        if evaluate_rules(probe, [candidate]).matched_rule is not None
    }
    if not candidate_matches:
        # Candidate matches nothing — trivially no conflict.
        return conflicts

    for other in existing_rules:
        if other.get("id") == candidate.get("id"):
            continue
        if (other.get("status") or "active") != "active":
            continue
        other_priority = int(other.get("priority") or 100)
        other_matches = {
            idx for idx, probe in enumerate(_PROBE_INVOICES)
            if evaluate_rules(probe, [other]).matched_rule is not None
        }
        if not other_matches:
            continue

        overlap = candidate_matches & other_matches
        if not overlap:
            continue

        if other_priority == candidate_priority:
            conflicts.append(RuleConflict(
                kind="same_priority_overlap",
                rule_a=candidate, rule_b=other,
                note=(
                    f"Both rules sit at priority {candidate_priority} and match "
                    f"{len(overlap)} of {len(_PROBE_INVOICES)} probe invoices. "
                    "Evaluation order between them is unstable."
                ),
            ))
        elif other_priority < candidate_priority and candidate_matches.issubset(other_matches):
            conflicts.append(RuleConflict(
                kind="redundant",
                rule_a=candidate, rule_b=other,
                note=(
                    f"Rule '{other.get('name')}' has higher priority "
                    f"({other_priority} < {candidate_priority}) and matches every "
                    "probe invoice the new rule would. The new rule never fires."
                ),
            ))

    return conflicts


# ---------------------------------------------------------------------------
# Starter templates — Module 3 spec §123 ("4 starter rule templates")
# ---------------------------------------------------------------------------

STARTER_TEMPLATES: List[Dict[str, Any]] = [
    {
        "id": "tpl-low-amount-auto",
        "name": "Auto-approve low-amount invoices",
        "description": (
            "Invoices under $1,000 from any vendor route to auto-approval. "
            "Fast-path the long tail of small expenses so leaders only see "
            "the items that matter."
        ),
        "priority": 100,
        "conditions": {
            "all_of": [
                {"field": "amount", "op": "lt", "value": 1000},
                {"field": "currency", "op": "eq", "value": "USD"},
            ],
        },
        "actions": [{"type": "auto_approve"}],
    },
    {
        "id": "tpl-mid-amount-manager",
        "name": "Mid-amount invoices to AP manager",
        "description": (
            "Invoices between $1,000 and $10,000 go to the AP manager for "
            "single-signature approval. Threshold the typical "
            "departmental-spend band."
        ),
        "priority": 200,
        "conditions": {
            "all_of": [
                {"field": "amount", "op": "gte", "value": 1000},
                {"field": "amount", "op": "lt", "value": 10000},
                {"field": "currency", "op": "eq", "value": "USD"},
            ],
        },
        "actions": [{"type": "route_to_role", "role": "ap_manager"}],
    },
    {
        "id": "tpl-high-amount-controller",
        "name": "Higher-amount invoices to controller",
        "description": (
            "Invoices between $10,000 and $50,000 escalate to the controller. "
            "Single-signature; the controller is accountable for the band."
        ),
        "priority": 300,
        "conditions": {
            "all_of": [
                {"field": "amount", "op": "gte", "value": 10000},
                {"field": "amount", "op": "lt", "value": 50000},
                {"field": "currency", "op": "eq", "value": "USD"},
            ],
        },
        "actions": [{"type": "route_to_role", "role": "financial_controller"}],
    },
    {
        "id": "tpl-large-amount-dual-approval",
        "name": "Large invoices require dual approval",
        "description": (
            "Invoices $50,000 and above require two distinct approvers. "
            "Catches the kind of write that benefits from a second pair of eyes."
        ),
        "priority": 400,
        "conditions": {
            "all_of": [
                {"field": "amount", "op": "gte", "value": 50000},
                {"field": "currency", "op": "eq", "value": "USD"},
            ],
        },
        "actions": [{"type": "require_dual_approval"}],
    },
]


def get_starter_templates() -> List[Dict[str, Any]]:
    """Return the canonical starter templates."""
    return [dict(tpl) for tpl in STARTER_TEMPLATES]


# ---------------------------------------------------------------------------
# Invoice-context builder (used by AP integration + test mode)
# ---------------------------------------------------------------------------

def build_invoice_context(invoice: Any) -> Dict[str, Any]:
    """Project an InvoiceData (or dict) into the field shape the engine
    evaluates against."""
    if isinstance(invoice, dict):
        get = invoice.get
    else:
        def get(key, default=None):  # type: ignore[no-redef]
            return getattr(invoice, key, default)

    return {
        "amount": float(get("amount") or 0),
        "currency": get("currency") or "USD",
        "vendor_name": get("vendor_name") or get("vendor") or "",
        "vendor_id": get("vendor_id") or "",
        "gl_code": get("gl_code") or "",
        "department": get("department") or "",
        "entity_id": get("entity_id"),
        "invoice_age_days": int(get("invoice_age_days") or 0),
        "vendor_age_days": int(get("vendor_age_days") or 0),
        "workflow": get("workflow") or "ap",
    }
