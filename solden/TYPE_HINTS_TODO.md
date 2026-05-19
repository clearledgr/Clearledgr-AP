# Type Hints TODO

Critical functions that need proper type hints, TypedDicts for dict payloads,
and input validation. Starting point for a future sprint.

## Priority 1 — Core data paths (dicts everywhere, easy to pass wrong keys)

1. `clearledgr/core/stores/ap_store.py` — `create_ap_item(payload: dict)` needs a TypedDict for the payload shape
2. `clearledgr/core/stores/ap_store.py` — `update_ap_item(ap_item_id, **kwargs)` needs typed kwargs or a TypedDict
3. `clearledgr/services/finance_runtime_invoice_processing.py` — `execute_ap_invoice_processing()` takes untyped dicts for extraction, bank_match, erp_match
4. `clearledgr/integrations/erp_router.py` — `post_bill()` return value is an untyped dict with varying keys per ERP

## Priority 2 — Agent / reasoning layer (dict-heavy, hard to debug)

5. `clearledgr/services/invoice_workflow.py` — `_get_ap_decision()` returns untyped APDecision-like dict
6. `clearledgr/core/agent_runtime.py` — `run_task(task)` and `SkillResult` need tighter typing on the result dict
7. `clearledgr/services/gmail_extension_support.py` — `apply_intelligence()` mutates and returns a dict with many optional keys

## Priority 3 — Integration boundaries (where external data enters)

8. `clearledgr/integrations/erp_router.py` — `get_erp_connection()` return could be a proper dataclass (partially done with ERPConnection)
9. `clearledgr/api/gmail_extension.py` — `triage_email()` handler assembles a large response dict; should use a response model
10. `clearledgr/services/gmail_extension_support.py` — `build_extension_pipeline()` returns a complex nested dict

## How to approach

- Add TypedDicts in `clearledgr/core/types.py` (create if needed)
- Start with Priority 1 — these are the most common source of key-name bugs
- Use `from __future__ import annotations` to avoid circular imports
- Add runtime validation with Pydantic where data crosses trust boundaries (API input, ERP responses)
