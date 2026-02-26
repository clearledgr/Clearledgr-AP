# Lessons Learned

Patterns and corrections captured during development.
Updated after every user correction per working rules.

---

## DB / ORM Patterns

### `create_ap_item` takes a dict, not kwargs
- **Mistake**: Passing kwargs to `create_ap_item(vendor=..., amount=...)`
- **Correct**: `create_ap_item({"vendor_name": ..., "amount": ...})`
- **Root cause**: Method signature is `def create_ap_item(self, payload: dict)`

### Column names differ from legacy kwarg names
- `state` not `status`
- `erp_reference` not `erp_reference_id`
- `last_error` not `error_message`
- `thread_id` maps to `gmail_id`

### `:memory:` SQLite DBs don't persist across `connect()` calls
- Each `connect()` creates a new connection → `:memory:` DB is blank each time
- **Fix**: Use a temp file (`tempfile.mkstemp(suffix=".db")`) in tests that need persistence across calls

### New columns need both `_ensure_column` AND `_AP_ITEM_ALLOWED_COLUMNS`
- Adding a column to `ap_items` requires:
  1. `_ensure_column(cur, "ap_items", "col_name", "TYPE")` in `database.py`
  2. `"col_name"` added to `_AP_ITEM_ALLOWED_COLUMNS` in `ap_store.py`
  3. If used in INSERT: update `create_ap_item` SQL + values tuple

---

## Auth Patterns

### Gmail extension content-script.js does NOT send auth headers
- Existing fetch calls use no `Authorization` or `X-API-Key`
- Extension endpoints that need to be callable from content-script.js must NOT require `Depends(get_current_user)`
- Use `Depends(get_optional_user)` or no auth dependency, relying on `actor_id` in request body

### `get_current_user` requires Bearer token OR X-API-Key header
- Missing both raises HTTP 401
- Extension sidebar (inboxsdk-layer.js) communicates via DOM events to content-script.js, which makes the fetch calls

---

## Test Patterns

### Pre-existing failures (do not count against new work)
- `tests/test_engine.py` — missing `_users_db` attribute
- `tests/test_api_endpoints.py` — 422s and schema mismatches
- `tests/test_core_workflows.py` — import error, skip with `--ignore`
- `tests/test_invoice_extraction_eval_harness.py` — LLM eval harness

### Baseline as of 2026-02-26
- 193 passed, 32 failed (all pre-existing)

---

## Architecture Patterns

### AP item state machine is the canonical source of truth
- All state transitions go through `clearledgr/core/ap_states.py`
- Never set `state` directly — always call `transition_or_raise()` or `update_ap_item(state=...)` which validates internally

### Idempotency keys prevent double-posting
- Pattern: `f"{operation_type}:{ap_item_id}"` as stable key
- `resume:<ap_item_id>:erp_post` — prevents double ERP post on resume
- `erp_post_retry:<ap_item_id>` — prevents duplicate retry jobs

### `field_confidences` column is the accuracy foundation
- Populated by `approve_invoice()` after confidence gate evaluation
- Read by `build_worklist_item()` → exposed in worklist payload → rendered in Gmail card
- Queried by `GET /api/ops/extraction-quality` for per-field accuracy trends
- Corrections from `handleFixInvoice` flow: content-script.js → `/extension/record-field-correction` → `CorrectionLearningService` → `agent_corrections` table → `audit_events`

---

## Process Lessons

### Plan mode before 3+ step tasks
- Jumped straight into implementation without entering plan mode
- Cost: risk of going in the wrong direction, harder to course-correct mid-implementation

### Subagents for codebase exploration
- Exploring call sites / grep patterns in main context burns tokens unnecessarily
- Use `Explore` subagent for "find all places X is called" type research

### Verify before marking complete
- Always run `python -m pytest` (or the relevant subset) before marking a task done
- Import check: `python -c "from module import thing; print('OK')"` catches syntax errors fast
