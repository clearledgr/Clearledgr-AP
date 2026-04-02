# Tier 4 Audit — Code Quality, Tech Debt & Maintenance Burden

Date: 2026-04-02
Auditor: Claude Opus 4.6
Scope: Giant files, inconsistent patterns, dead code, naming, type hints, config sprawl, test coverage gaps, unused dependencies
Total issues: 35 (0 critical, 8 high, 18 medium, 9 low)

---

## I. Giant Files (6 issues)

### I1. [HIGH] erp_router.py is 4,478 lines with 5 responsibilities
**File:** `clearledgr/integrations/erp_router.py`
**What:** Sanitization, OAuth headers, query building (4 ERPs), error extraction, credit/settlement logic, and a Vendor class — all in one file.
**Impact:** Every ERP change requires reading 4,478 lines. Hard to test one ERP in isolation.
**Fix:** Split into `erp_router.py` (dispatch only), `erp_query_builders/{qb,xero,netsuite,sap}.py`, `erp_sanitization.py`, `erp_error_handlers.py`.

### I2. [HIGH] ap_item_service.py is 3,523 lines with 83 functions
**File:** `clearledgr/services/ap_item_service.py`
**What:** 83 functions with no class grouping. Mixed concerns: field review (15 functions), confidence scoring, worklist building (600 lines), vendor summaries, upcoming tasks.
**Impact:** No cohesion. Functions range from 2 to 200 lines. Hard to find anything.
**Fix:** Split into `ap_field_review.py`, `ap_projection.py` (already partially exists), `ap_vendor_analysis.py`.

### I3. [MEDIUM] gmail_extension.py is 2,415 lines with 1,000+ lines of Pydantic models
**File:** `clearledgr/api/gmail_extension.py`
**What:** 76 functions, 13 Pydantic models, all in one API file.
**Fix:** Extract Pydantic models to `api/gmail_extension_models.py`.

### I4. [MEDIUM] workspace_shell.py is 2,086 lines mixing admin/config/GA/health
**File:** `clearledgr/api/workspace_shell.py`
**What:** 110 functions covering GA readiness, config, Slack/Teams/Gmail status, health snapshots, and 12 router endpoints.
**Fix:** Split into `workspace_config.py`, `workspace_health.py`, `workspace_ga_readiness.py`.

### I5. [MEDIUM] ap_store.py mixin has 74 methods (2,295 lines)
**File:** `clearledgr/core/stores/ap_store.py`
**What:** One mixin class with 74 persistence methods. Part of a 10-mixin inheritance chain that creates ~10,000 lines of "one giant class in disguise."
**Fix:** Long-term: migrate from mixins to composition (`db.ap.list_items()` instead of `db.list_ap_items()`).

### I6. [MEDIUM] metrics_store.py mixin has 28 methods (2,177 lines)
**File:** `clearledgr/core/stores/metrics_store.py`
**What:** KPI/reporting queries that could be a separate service.
**Fix:** Same as I5 — extract to composition pattern.

---

## J. Inconsistent Patterns (6 issues)

### J1. [MEDIUM] Three different `get_db()` functions
**Files:** `core/database.py`, `api/ops.py`, `integrations/erp_router.py`
**What:** Three entry points for the same DB singleton. 61 files import from different locations.
**Fix:** Delete `ops.py::get_db()` and `erp_router.py::_get_db()`. Update 24 call sites.

### J2. [HIGH] Mixin-based DB pattern prevents isolated testing
**File:** `clearledgr/core/database.py`
**What:** ClearledgrDB inherits from 10+ mixins (APStore, AuthStore, VendorStore, etc.). Each mixin assumes `self.connect()` and `self._prepare_sql()` exist. Can't instantiate or test any store in isolation.
**Impact:** Testing requires the full ClearledgrDB. Mocking is painful. Adding new queries requires knowing which mixin "owns" them.
**Fix:** Migrate to composition pattern over time.

### J3. [LOW] Logging inconsistency across 93 files
**What:** 71 files use `logging.getLogger(__name__)` (good), 22 use global logger (ok), 4 use both (bad). No structured logging. 627 broad `except Exception` catches.
**Fix:** Standardize logger pattern. Add log level guidelines.

### J4. [MEDIUM] Three different error return styles
**What:** API routes raise HTTPException. Services return None. Some services return empty dict. 471 raise statements vs 558 `return None/{}` for "failure."
**Impact:** Callers must handle both exceptions and null checks. No unified error contract.
**Fix:** Create error hierarchy (`ClearledgrError`, `NotFoundError`, `ValidationError`). Transform at API boundary.

### J5. [LOW] No structured error codes
**What:** Error messages are free-form strings. No standardized error code enum. Different services use different formats for the same failure.
**Fix:** Create error code enum. Use consistently across services.

### J6. [LOW] Inconsistent async/sync patterns
**What:** Some services are async, some sync. `agent_background.py` calls sync functions from async context without `run_in_executor()`.
**Fix:** Make all DB calls async-compatible or use executor consistently.

---

## K. Dead Code & Unused Dependencies (7 issues)

### K1. [LOW] Dead functions in ap_item_service.py
**File:** `clearledgr/services/ap_item_service.py`
**What:** `_sort_vendor_issue_items()` never called. 85+ single-caller functions that could be inlined.
**Fix:** Remove dead functions. Inline single-callers where they don't add clarity.

### K2. [LOW] Feature flags always True
**What:** `AGENT_PLANNING_LOOP=true` in env.example and always True in code. `GMAIL_AUTOPILOT_ENABLED=true` default. Dead flag branches add code that never executes.
**Fix:** Remove flags that are always on. Keep only flags that are actually toggled.

### K3. [MEDIUM] ~17 unused Python dependencies
**File:** `requirements.txt`
**What:** `sqlalchemy`, `alembic`, `aiosqlite` (ORM stack never used — code uses sqlite3 directly). `pyyaml`, `pytz`, `python-dateutil` (standard library used instead). `tenacity` (custom retry logic). `prometheus-client` (custom metrics).
**Impact:** Supply chain risk, install time, dependency conflicts.
**Fix:** Create `requirements-minimal.txt` for production. Remove dead deps.

### K4. [HIGH] 6 critical production services have NO test files
**What:** Missing test files for:
- `invoice_posting.py` — ERP posting logic (critical mutation point)
- `policy_compliance.py` — Approval policy evaluation
- `budget_awareness.py` — Budget impact calculations
- `approval_card_builder.py` — Slack/Teams card construction
- `vendor_intelligence.py` — Vendor profile enrichment
- `auto_followup.py` — Follow-up draft creation

**Impact:** Untested ERP posting, policy logic, and approval card construction. Changes to these files have no safety net.
**Fix:** Create unit tests, prioritizing `invoice_posting.py` and `policy_compliance.py`.

### K5. [LOW] Duplicate ARCHITECTURE.md
**What:** `/ARCHITECTURE.md` (29KB, current, 2026-04-01) vs `/docs/ARCHITECTURE.md` (5KB, stale, 2026-03-25). Root version is canonical.
**Fix:** Delete `/docs/ARCHITECTURE.md` or add redirect note.

### K6. [LOW] Unused imports scattered across files
**What:** Estimated 5-10% of imports are unused (e.g., `from collections import Counter` in ap_item_service.py). No linting enforced.
**Fix:** Run `ruff check --select F401` and fix. Add to CI.

### K7. [LOW] 103 undocumented environment variables
**What:** 163 total env vars in code, only ~60 in env.example. 103 are undocumented.
**Fix:** Document all in env.example with comments: purpose, valid values, production safety, default.

---

## L. Naming & Type Safety (8 issues)

### L1. [MEDIUM] ap_item_id vs invoice_key vs invoice_number used interchangeably
**What:** 3 different identifiers for "an invoice" used across 2,000+ call sites. Functions sometimes accept wrong type without error.
**Fix:** Create NewType aliases. Add type hints. Document rules: `ap_item_id` = UUID primary key, `invoice_key` = natural key, `invoice_number` = raw vendor string.

### L2. [MEDIUM] organization_id parameter naming inconsistent
**What:** 2,851 occurrences. Sometimes required positional, sometimes optional kwarg. Internal vars use `org` or `org_id`.
**Fix:** Always `organization_id: str` as parameter. Never abbreviate.

### L3. [LOW] user_id vs actor_id vs user confusion
**What:** 1,503 occurrences. `actor_id` can be "system" or "anonymous" — not always a real user_id. No type distinction.
**Fix:** Document rules. `user_id` = authenticated user. `actor_id` = who took action (may be system).

### L4. [MEDIUM] 547 functions missing return type hints
**What:** 56% of functions have return types. Critical domain functions like `_build_context_payload()` and `_merge_ap_item_metadata()` lack types.
**Impact:** IDEs can't autocomplete. Refactoring is risky.
**Fix:** Add return types to all public functions. Start with services/ and core/.

### L5. [MEDIUM] Dict[str, Any] used everywhere instead of TypedDict
**What:** Most functions accept and return `Dict[str, Any]`. No compile-time checking of dict keys.
**Fix:** Create TypedDict classes for major data shapes (APItem, InvoiceData, VendorProfile, etc.).

### L6. [LOW] gmail_id vs thread_id vs message_id ambiguity
**What:** Sometimes used interchangeably. `gmail_id` maps to `thread_id` in the AP items table. `message_id` is a different field. Code sometimes passes one where the other is expected.
**Fix:** Document rules. Add type aliases.

### L7. [MEDIUM] No input validation on API request payloads
**What:** Many internal service functions accept `Dict[str, Any]` without validating keys exist. Rely on `.get()` with fallback, which masks missing data.
**Fix:** Use Pydantic models at service boundaries, not just API routes.

### L8. [LOW] Magic numbers scattered in validation logic
**What:** Thresholds like `0.95`, `0.7`, `0.3`, `100`, `5000` hardcoded in service functions without named constants.
**Fix:** Extract to module-level constants with descriptive names.

---

## M. Configuration Sprawl (2 issues)

### M1. [MEDIUM] 163 environment variables, 103 undocumented
**What:** Production, staging, and dev flags mixed together. Some flags are production-unsafe but have no guards. No validation at startup for required vars.
**Impact:** New deployments miss critical flags. Operators don't know what to set.
**Fix:** Group by category in env.example. Add startup validation. Add `REQUIRED_IN_PROD` comments.

### M2. [LOW] Conflicting env var defaults
**What:** Some flags default differently in dev vs prod via runtime checks (`_is_prod`). Logic is scattered across files with no central config module.
**Fix:** Create `clearledgr/config.py` that centralizes all env var reads with typed defaults and validation.

---

## Priority Matrix

### Fix immediately (quick wins, 1-2 weeks)
- J1 — Kill redundant get_db() functions (2 hours)
- K7/M1 — Document all 163 env vars in env.example (1 day)
- K1 — Remove dead functions (1 day)
- K6 — Run ruff to remove unused imports (1 hour)
- K5 — Delete stale docs/ARCHITECTURE.md (5 minutes)

### Fix before enterprise (3-4 weeks)
- I1, I2, I4 — Split the 3 worst giant files (erp_router, ap_item_service, workspace_shell)
- K4 — Write tests for 6 untested critical services
- K3 — Remove unused dependencies
- L4, L5 — Add return types and TypedDict to critical functions
- J4 — Standardize error handling

### Fix when time permits (strategic)
- I5, I6, J2 — Migrate from mixins to composition
- L1, L2 — Naming consistency cleanup
- J3, J5, J6 — Logging, error codes, async patterns
- K2, L3, L6, L7, L8, M2 — Minor polish

---

## Overall Debt Score

| Category | Score (1-10) | Notes |
|----------|-------------|-------|
| File organization | 5/10 | 14 files >1500 lines. 6 are serious. |
| Code consistency | 6/10 | DB access, error handling, logging patterns vary. |
| Dead code | 8/10 | Very little dead code. ~50 functions to clean. |
| Type safety | 5/10 | 547 functions missing types. Dict[str,Any] everywhere. |
| Test coverage | 6/10 | 869 tests but 6 critical services untested. |
| Documentation | 7/10 | Good breadth but 103 undocumented env vars. |
| Dependencies | 6/10 | ~17 unused packages. No lock file. |
| Naming | 6/10 | 3 terms for "invoice," inconsistent param names. |
| **Overall** | **6.1/10** | Solid foundation, needs cleanup sprint before scaling team. |
