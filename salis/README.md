# Salis — Clearledgr engineering docs

This directory is the engineering handoff for Clearledgr.v1. If you're joining the team, start here.

Named "Salis" so we can talk about "the docs" as a thing with an identity, not a folder.

---

## Start here

Read [`handoff-tour.md`](./handoff-tour.md) first. It's the narrative walkthrough Mo would give you over coffee. Reference docs come after.

Reading order for your first week is printed at the end of `handoff-tour.md`. Follow it.

---

## 15-minute local setup

Goal: running backend + running tests + running extension build.

### Backend

```bash
# 1. Python 3.11 (check: python --version)
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Copy env
cp env.example .env
# Open .env and at minimum set:
#   CLEARLEDGR_SECRET_KEY=(any random string for dev)
#   DATABASE_URL=sqlite:///./clearledgr.db      # dev default, fine
#   REDIS_URL=redis://localhost:6379/0          # needs a local Redis running
# Anthropic key is optional for most tests; set ANTHROPIC_API_KEY if you want
# to exercise real Claude calls locally.

# 3. Run migrations + start the server
CLEARLEDGR_PROCESS_ROLE=all python main.py
# or
sh scripts/start-api.sh
```

Server binds to `http://localhost:8010`. Visit `/health` and you should see:

```json
{"status":"healthy","checks":{"database":{"status":"healthy"}, ...}}
```

If `/health` returns 404 with `endpoint_disabled_in_ap_v1_profile`, you're hitting the strict profile gate — the route is not on the V1 allow-list. Check `main.py`'s `STRICT_PROFILE_ALLOWED_*` sets in that case.

### Tests

```bash
python -m pytest tests/ -q
```

Expect **2349 passing** on a clean checkout. Anything red is signal, not noise.

Useful subsets:

```bash
python -m pytest tests/test_box_invariants.py -q              # Rule 1 + state/audit atomicity
python -m pytest tests/test_state_audit_atomicity.py -q       # atomicity drift fence
python -m pytest tests/test_box_lifecycle_store.py -q         # exceptions + outcomes lifecycle
python -m pytest tests/test_box_exceptions_admin_api.py -q    # customer admin exception queue
python -m pytest tests/test_execution_engine.py -q            # LLM boundary + planner-handler fences
python -m pytest tests/test_endpoint_idempotency.py -q        # the idempotency contract
python -m pytest tests/test_tenant_isolation.py -q            # cross-org regression fence
python -m pytest tests/test_erp_webhook_security.py -q        # HMAC verification
```

### Gmail extension

```bash
cd ui/gmail-extension
npm install           # first time only
npm run build         # produces dist/ with the bundled extension
npm test              # runs the extension test suite (100 passing expected)
```

`dist/` is what you load as an unpacked extension in Chrome (`chrome://extensions` → Load unpacked → select `ui/gmail-extension/`). See `ui/gmail-extension/README.md` for Chrome load details (create one if missing).

### Full stack with Postgres + Redis (optional)

```bash
docker-compose up -d       # Postgres + Redis + API
```

Hits the same code paths as Railway prod. Slower iteration than SQLite-local but closer to production.

---

## What's in Salis

Read in this order for first week:

| Doc | Why |
|---|---|
| [`handoff-tour.md`](./handoff-tour.md) | Narrative walkthrough. First read. |
| [`architecture.md`](./architecture.md) | Single-page system map. ASCII diagrams of Box lifecycle, coordination engine, event queue. |
| [`adrs/`](./adrs/) | Architecture decision records. *Why* we chose what we chose. Read before changing any load-bearing pattern. |
| [`agent-runtime.md`](./agent-runtime.md) | How planning + coordination + skills work. How Rule 1 is enforced. How to add a skill. |
| [`contributing.md`](./contributing.md) | How to work here. Where code lives. How to add a Box type. How to add an ERP. Code review bar. |
| [`integrations.md`](./integrations.md) | Per-surface playbook. Gmail OAuth + Pub/Sub. Slack events. Teams (V1.1). Outlook (V1.1). Four ERPs. KYC + open-banking stubs. |
| [`operations.md`](./operations.md) | Runbooks. Deploy. Rollback. Investigate stuck Box. Investigate failed ERP post. Rotate secrets. |
| [`security.md`](./security.md) | Threat model. Tenant isolation. Secrets. SOC 2 posture. |
| [`product.md`](./product.md) | What ships, what's deferred, why. For engineers AND non-engineers. |

ADRs exist one per decision, short, numbered (`001-box-abstraction.md`, `002-coordination-engine.md`, ...). List in [`adrs/README.md`](./adrs/README.md).

---

## Related canonical docs outside Salis

Salis is the onboarding layer. The deeper canonical docs live at the repo root:

| Doc | Owner | What |
|---|---|---|
| [`../CLAUDE.md`](../CLAUDE.md) | Agents | The working-rules file that AI agents and engineers read before starting work. |
| [`../DESIGN_THESIS.md`](../DESIGN_THESIS.md) | Product | The product doctrine. Box-first, coordination-layer identity, the whole §1-§19 thesis. |
| [`../AGENT_DESIGN_SPECIFICATION.md`](../AGENT_DESIGN_SPECIFICATION.md) | Architecture | The canonical agent architecture. §1-§13. |
| [`../CLEARLEDGR_MEMO.md`](../CLEARLEDGR_MEMO.md) | CEO | External-facing product identity memo. Read this once for the customer-facing framing. |
| [`../PLAN.md`](../PLAN.md) | Launch | AP v1 GA launch spec. Treat as historical reference; the current 30-day plan lives in `~/.gstack/projects/clearledgr-Clearledgr-AP/ceo-plans/2026-04-21-path-to-first-customer.md`. |
| [`../TODOS.md`](../TODOS.md) | Engineering | Deferred work ledger. Not an implementation-completeness tracker. |
| `../commission-clawback-spec.md` | Frozen spec | Second workflow class spec. Frozen pending V1 launch. |
| `../vendor-onboarding-spec.md` | Engineering | 1500-line engineering spec for vendor onboarding. Implementation shipped; KYC + open-banking providers stubbed. |

---

## One honest note on doc drift

The repo has accumulated doctrine layers over time. Not all of them agree.

Canonical as of 2026-04-21 (in order of authority):

1. [`../CLEARLEDGR_MEMO.md`](../CLEARLEDGR_MEMO.md) — product identity. Coordination layer. Box as product.
2. [`../DESIGN_THESIS.md`](../DESIGN_THESIS.md) — product doctrine. §1-§19.
3. [`../AGENT_DESIGN_SPECIFICATION.md`](../AGENT_DESIGN_SPECIFICATION.md) — agent architecture.
4. Salis (this directory) — engineering onboarding. Translates the canonical docs for new engineers.

Not canonical:

- **Top-level `../README.md`** has older framing ("Finance Execution Layer") and older product doctrine (Pipeline-as-primary, Teams-and-Slack-co-equal) that was walked back this session. The current identity is "AP coordination agent living in Gmail, routing approvals through Slack, posting bills into ERP." Teams is V1.1 feature-flagged off. Pipeline is a view, not the control plane. Top-level README update is in the backlog; until then, read around its framing.
- The 2026-03-25 product-direction CEO plan (`~/.gstack/projects/.../ceo-plans/2026-03-25-product-direction.md`) is marked **SUPERSEDED** by the 2026-04-21 plan. Its frontmatter has the pointer.
- Older readiness trackers and GA-readiness docs in `../docs/` are audit-time snapshots, not living doctrine. Use them to answer "what was true in March 2026?" not "what should we build now?"

If you find something in the code that contradicts a doc, the code wins and the doc gets a PR. If two docs contradict, the one higher in the canonical list wins.

---

## When Salis is wrong

Every doc in this directory has a status at the top and a last-verified date. If you find something stale, fix it — the PR is the contribution. If you're not sure whether it's stale or you're reading it wrong, ask Suleiman.
