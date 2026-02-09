# Clearledgr

Clearledgr builds the execution layer for finance workflows.

Today, that means shipping one thing only:

**Clearledgr AP v1** — an embedded system that executes Accounts Payable workflows end-to-end inside the tools finance teams already use.

Clearledgr:
- Starts work in email
- Coordinates approvals in Slack or Teams
- Writes approved entries to the ERP
- Produces a complete, immutable audit trail

Clearledgr does not surface dashboards, insights, or tasks.
It executes work.

No other workflows exist until AP works in production.

## Roadmap

### v2 (Coming Soon)
- ERP Connectors (NetSuite, Xero, QuickBooks)
- Approval Workflows
- Advanced Analytics

## Quick Start

### Local Development

1. **Install dependencies:**
   ```bash
   pip install -r requirements
   ```

2. **Set environment variables:**
   ```bash
   cp env.example .env
   # Edit .env with your configuration
   ```

3. **Run the API:**
   ```bash
   uvicorn main:app --host 0.0.0.0 --port 8000 --reload
   ```

4. **Access API documentation:**
   - Swagger UI: http://localhost:8000/docs
   - ReDoc: http://localhost:8000/redoc
   - Admin QA Page: http://localhost:8000/admin

### Docker Deployment

```bash
docker build -t clearledgr:latest .
docker-compose up -d
```

## API Endpoints

### System
- `GET /health` - Health check
- `GET /metrics` - API metrics and statistics
- `GET /admin` - QA testing page

### Reconciliation
- `POST /run-reconciliation` - Run reconciliation with CSV uploads
- `POST /run-reconciliation-sheets` - Run with Google Sheets

### LLM Proxy
- `POST /llm/explain-exception` - Get explanation for exception
- `POST /llm/explain-batch` - Batch explanation requests

### History
- `GET /runs` - List reconciliation runs
- `GET /runs/{run_id}` - Get specific run details
- `GET /runs/stats` - Get aggregate statistics

### Agent Features
- `POST /agent/schedules` - Create agent schedule
- `GET /agent/schedules/{tool_type}/{tool_id}` - Get schedules
- `POST /agent/feedback` - Submit agent feedback
- `GET /agent/memory/{organization_id}` - Get agent memory
- `GET /agent/recommendations/{organization_id}` - Get proactive recommendations
- `POST /agent/quality-check` - Check data quality

### Email Integration
- `POST /email/parse` - Parse email and extract financial data
- `POST /email/match-invoice` - Match invoice to transactions
- `POST /email/match-payment` - Match payment to open invoices
- `POST /email/vendor-exceptions` - Get unmatched items for vendor
- `POST /email/process` - Full end-to-end email processing
- `POST /email/tasks` - Create task from email
- `GET /email/tasks` - List tasks
- `GET /email/tasks/{task_id}` - Get task details
- `PATCH /email/tasks/status` - Update task status
- `POST /audit/record` - Record audit event
- `GET /audit/trail` - Query audit trail

## Embedded Integrations

### Google Sheets Add-on

The Sheets add-on provides:
- **Clearledgr > Reconciliation > Run Reconciliation** - 3-way matching
- **Clearledgr > Review Exceptions** - Triage unmatched items

Results are written to output sheets:
- `CL_SUMMARY` - Reconciliation summary
- `CL_RECONCILED` - Matched transactions
- `CL_EXCEPTIONS` - Unmatched with explanations

### Microsoft Excel Add-in (Planned)
Office.js add-in planned after v1.

### Gmail Add-on

Context-aware finance copilot in Gmail:
- **Side Panel**: Opens alongside email threads
- **Email Analysis**: Automatic invoice/payment detection
- **Quick Actions**: Add to AP, Match, Flag Variance
- **Transaction Matching**: 3-way/2-way matching
- **Vendor Exceptions**: See unmatched items
- **Task Creation**: Turn threads into close tasks

Deployment: See `ui/gmail/DEPLOYMENT.md`

### Outlook Add-in (Planned)
Planned after v1.

### Slack Integration

Notifications and commands:
- Reconciliation completion alerts
- Summary statistics
- Exception counts

## Configuration

### Reconciliation Config

```json
{
  "mappings": {
    "payment_gateway": {
      "Transaction ID": "txn_id",
      "Date": "date",
      "Net Amount": "net_amount"
    },
    "bank": {
      "Bank Transaction ID": "bank_txn_id",
      "Booking Date": "date",
      "Amount": "amount"
    },
    "internal": {
      "Internal ID": "internal_id",
      "Date": "date",
      "Amount": "amount"
    }
  },
  "amount_tolerance_pct": 0.5,
  "date_window_days": 3
}
```

### Reconciliation Aggregation Config

```json
{
  "variance_threshold_pct": 5.0,
  "significant_amount": 1000.0
}
```

## Environment Variables

### API Security
- `API_KEY` - API key for authentication (optional)
- `RATE_LIMIT_ENABLED` - Enable rate limiting (default: true)
- `RATE_LIMIT_REQUESTS` - Requests per window (default: 100)
- `RATE_LIMIT_WINDOW` - Window in seconds (default: 60)

### LLM Configuration
- `LLM_PRIMARY_PROVIDER` - `anthropic` or `mistral`
- `LLM_TIMEOUT_SECONDS` - Request timeout in seconds (default: 30)
- `ANTHROPIC_API_KEY` - Anthropic API key
- `ANTHROPIC_MODEL` - Anthropic model (default: claude-3-5-sonnet-20240620)
- `MISTRAL_API_KEY` - Mistral API key
- `MISTRAL_MODEL` - Mistral model (default: mistral-large-latest)

### Google Sheets Integration
- `GOOGLE_SERVICE_ACCOUNT_JSON` - Service account JSON

### Slack App
- `SLACK_BOT_TOKEN` - Bot token from Slack app installation
- `SLACK_SIGNING_SECRET` - Signing secret for request verification
- `SLACK_CLIENT_ID` - OAuth client ID
- `SLACK_CLIENT_SECRET` - OAuth client secret
- `SLACK_DEFAULT_CHANNEL` - Default channel for notifications

### Teams App (Planned)
- `TEAMS_APP_ID` - Azure Bot app ID
- `TEAMS_APP_PASSWORD` - Azure Bot password

### Database
- `STATE_DB_PATH` - SQLite database path (default: state.sqlite3)

## Architecture

- **FastAPI** - Async web framework
- **SQLite** - Embedded database for run history
- **Anthropic/Mistral** - LLM providers for extraction and explanations
- **gspread** - Google Sheets integration

## Production Deployment

```bash
docker run -d \
  --name clearledgr \
  -p 8000:8000 \
  --env-file .env \
  -v $(pwd)/data:/app/data \
  clearledgr:latest
```

For production, set:
- `API_KEY` - Required for authentication
- `RATE_LIMIT_ENABLED=true`
- `USE_JSON_LOGS=true`
- `LOG_LEVEL=INFO`

## License

Proprietary
