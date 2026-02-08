# Temporal Setup (Clearledgr)

This repo supports Temporal-based orchestration for reconciliation and invoice workflows.

## Prereqs
- Temporal server running (local dev: `temporal server start-dev`).
- `temporalio` installed (`pip install -r requirements`).

## Environment Variables
Set these in your environment or `.env`:
```
TEMPORAL_ENABLED=true
TEMPORAL_ADDRESS=localhost:7233
TEMPORAL_NAMESPACE=default
TEMPORAL_TASK_QUEUE=clearledgr-v1
```

For multi-modal extraction:
```
ANTHROPIC_API_KEY=...
MISTRAL_API_KEY=...
LLM_PRIMARY_PROVIDER=anthropic
```

## Run the Temporal Worker
```
python -m clearledgr.workflows.temporal_worker
```

## API Usage
Use the `/v1` endpoints:
- `POST /v1/reconciliation/run` (will start a workflow and await result when `TEMPORAL_ENABLED=true`)
- `POST /v1/invoices/extract` (same behavior)

Async workflows:
- `POST /v1/workflows/reconciliation/start`
- `POST /v1/workflows/invoice/start`
- `GET /v1/workflows/{workflow_id}`

## Notes
- Activities do the I/O (LLM calls, audit logging, Slack routing).
- Workflows only orchestrate, ensuring deterministic execution.
- Set `TEMPORAL_ENABLED=false` to run workflows inline (no Temporal).

## Scheduling
- `POST /agent/schedules` creates a persisted schedule and a Temporal schedule (daily/weekly/monthly).
- Schedules are stored in SQLite via `clearledgr/state/agent_memory.py`.
- Temporal schedules use cron defaults (2am local server time).
- Scheduled reconciliations run `ScheduledReconciliationWorkflowTemporal` and will fetch Sheets data when no transactions are provided.

Schedule payload fields (in `schedule_config`):
- `sheet_id` (optional): defaults to the schedule `tool_id` for Sheets.
- `bank_tab` (optional): defaults to `BANK`.
- `gl_tab` or `internal_tab` (optional): defaults to `INTERNAL`.
- `config` or `mappings` (optional): overrides `CL_CONFIG` mappings/tolerances.
