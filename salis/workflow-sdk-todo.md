# Workflow platform — public SDK: engineering TODO

Status as of 2026-05-21. Owner: Engineering.

The declarative workflow **platform** is shipped and in `main` (commits
`a2213d5b`, `07e87147`, `181dc42c`, `21ec4afa`, `6126c714`). Reference doc:
[`../docs/WORKFLOW_PLATFORM.md`](../docs/WORKFLOW_PLATFORM.md).

What exists today (do not rebuild):
- Declarative `WorkflowSpec` (states, transitions, actions, fields); a new
  built-in type needs zero bespoke Python.
- Tenant-authored, **versioned**, per-org specs (`workflow_specs`, v93) with
  box version-pinning. Authoring API + no-code builder UI (`/workflows`).
- Generic `boxes` table (v92); one request-time data-plane router.
- Customer-code **hooks** in a WASM sandbox (`FEATURE_WORKFLOW_HOOKS`, default
  OFF), safe expression conditions, SSRF-guarded effect catalog,
  `workflow_hook_runs` audit (v94).
- The REST API **already accepts `X-API-Key` auth** via `get_current_user`
  ([`../solden/core/auth.py`](../solden/core/auth.py) L373-417), so the data
  plane is programmatically reachable today.

This doc tracks what is left to turn that platform into a **public,
externally-distributable SDK** that an outside developer builds on. None of it
is required for the in-product platform to work; it is the "ship it as an SDK
product" layer.

---

## 1. Workflow API-key scopes for authoring

**Why:** the box data plane works with an `X-API-Key` today, but spec authoring
(`POST /workflow-specs`, `.../activate`) is gated by `require_workspace_admin`
(session + workspace_role). An external integration can drive boxes but cannot
*author* a workflow programmatically.

**Do:** add a scope (e.g. `workflows:author`) to the API-key model; a new
dependency that accepts a scoped key OR an admin session. Keep tenant isolation
(the key's org is the only org it can touch).

**Files:** `solden/core/auth.py` (`validate_api_key` -> scopes on `TokenData`;
new `require_workflow_author` dep), `solden/api/workflow_spec_routes.py`.

**Done when:** a key with the scope can create + activate a spec for its own
org; an unscoped key gets 403; a key for org A cannot author for org B; tests
in `tests/test_workflow_isolation.py` extended to cover key-auth.

## 2. Client library (typed JS/TS + Python)

**Why:** an SDK is a library, not raw HTTP. Customers should write
`client.workflows.create(spec)` / `client.boxes.act(type, id, action)`, not
hand-roll fetch calls.

**Do:** generate a typed client against the workflow endpoints (FastAPI already
emits OpenAPI; curate a workflow-only schema and codegen). Ship a JS/TS package
first, Python second. Include a runnable example.

**Files:** new top-level `sdk/` (or a published `@solden/workflows` package);
an OpenAPI export step for the workflow routes.

**Done when:** the example app creates a type, activates it, creates a box, and
drives it through to a terminal state using only the client + an API key.

## 3. Public, versioned API reference

**Why:** `docs/WORKFLOW_PLATFORM.md` is internal. External devs need a stable,
versioned contract with examples and deprecation policy.

**Do:** publish an external reference (curated OpenAPI + prose). State the API
version, stability guarantees, and the spec-schema version. Document the hook
contract + effect catalog as the customer-facing extension surface.

**Done when:** a developer who has never seen the repo can integrate from the
reference alone.

## 4. External-grade rate limiting + quotas

**Why:** today there is a per-process limiter + `MAX_WORKFLOW_TYPES_PER_ORG`
(50). That is not enough for untrusted external traffic.

**Do:** per-tenant + per-key request throttles (429 on breach); per-tenant hook
CPU/memory budgets accumulated over time (read from `workflow_hook_runs`);
box-volume limits. Make limits configurable per plan.

**Files:** rate-limit middleware; quota checks in
`solden/core/stores/workflow_spec_store.py` and the generic box store; hook
budget accounting in `solden/core/hooks/dispatcher.py`.

**Done when:** documented limits are enforced and breaches return a clean 429
with a retry hint.

## 5. Hook execution productionization (customer JS)

**Why:** the sandbox core is proven, but two gaps remain for real customer code:
(a) hooks run **inline** in `update_generic_box_state` (the request path);
(b) customers can only supply a raw WASM module, not JavaScript source.

**Do:** run hooks **async** off the request path via the existing
coordination/task layer; add a QuickJS-in-WASM guest so customers write JS that
is fed source + sanitized box context (host marshals via the existing JSON ABI);
a hook-module/source upload + storage API; per-tenant fuel/mem budgets (ties to
item 4).

**Files:** `solden/core/hooks/sandbox.py` (JS guest), `dispatcher.py` (async
path), a new hook-upload route, storage for hook source/modules.

**Done when:** a customer uploads a JS hook, it runs sandboxed off the request
path within budget, and a denied/patched/effect-emitting result is observable in
`workflow_hook_runs`.

---

## Human-in-the-loop gates (NOT code; need real-world resources)

These cannot be closed in a coding session and must be done before any external
customer code runs in production:

- **Security pentest** of the WASM sandbox + SSRF guard + tenant isolation.
  Re-run the adversarial review (a `code-reviewer` pass already found and fixed
  3 critical SSRF/sandbox issues; see `docs/WORKFLOW_PLATFORM.md` security
  section) after items 1-5 land. `FEATURE_WORKFLOW_HOOKS` stays OFF until this
  passes.
- **External-developer beta:** real outside developers define a workflow in
  their own tenant. Validate developer experience AND safety with traffic we
  did not write.

---

## Suggested sequence

1 (scopes) -> 2 (client) + 3 (API ref) -> 4 (limits/quotas) -> 5 (JS guest +
async hooks) -> pentest -> external beta.
