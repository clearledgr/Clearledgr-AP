# Railway Deployment

Clearledgr is ready to run on Railway as two services plus managed data:

1. `api`
   - public HTTPS service
   - serves FastAPI, Slack callbacks, Gmail callbacks, docs, and extension APIs
2. `worker`
   - private/background service
   - runs Gmail autopilot, approval reminder/escalation loops, agent background jobs, and startup recovery work
3. `postgres`
   - managed Postgres
4. `redis`
   - optional but strongly recommended for shared rate limiting and background coordination

## Why split web and worker

The backend now supports explicit process roles:

- `CLEARLEDGR_PROCESS_ROLE=web`
- `CLEARLEDGR_PROCESS_ROLE=worker`
- `CLEARLEDGR_PROCESS_ROLE=all`

Use `web` on the API service and `worker` on the background service.

This prevents duplicated Gmail autopilot / agent background loops when the API runs with multiple Gunicorn workers.

## Service commands

API service:

```bash
sh scripts/start-api.sh
```

Worker service:

```bash
sh scripts/start-worker.sh
```

## Required environment variables

Shared:

```bash
DATABASE_URL=postgresql://...
REDIS_URL=redis://...
CLEARLEDGR_SECRET_KEY=...
TOKEN_ENCRYPTION_KEY=...
ANTHROPIC_API_KEY=...
OPENAI_API_KEY=...
ENV=production
AP_V1_STRICT_SURFACES=true
```

Public URL configuration:

```bash
API_BASE_URL=https://<your-public-api-domain>
APP_BASE_URL=https://<your-public-api-domain>
SLACK_REDIRECT_URI=https://<your-public-api-domain>/api/workspace/integrations/slack/install/callback
GOOGLE_GMAIL_REDIRECT_URI=https://<your-public-api-domain>/gmail/callback
GOOGLE_REDIRECT_URI=https://<your-public-api-domain>/gmail/callback
GOOGLE_CONSOLE_REDIRECT_URI=https://<your-public-api-domain>/auth/google/callback
```

Slack:

```bash
SLACK_CLIENT_ID=...
SLACK_CLIENT_SECRET=...
SLACK_SIGNING_SECRET=...
SLACK_BOT_TOKEN=...
SLACK_APPROVAL_CHANNEL=#finance-approvals
SLACK_DEFAULT_CHANNEL=#finance-approvals
```

API service only:

```bash
CLEARLEDGR_PROCESS_ROLE=web
PORT=8010
HOST=0.0.0.0
WORKERS=2
```

Worker service only:

```bash
CLEARLEDGR_PROCESS_ROLE=worker
```

## Slack app settings

Point Slack at the public HTTPS domain, not localhost:

- Redirect URL:
  - `https://<your-public-api-domain>/api/workspace/integrations/slack/install/callback`
- Interactivity:
  - `https://<your-public-api-domain>/slack/invoices/interactive`
- Events:
  - `https://<your-public-api-domain>/slack/events`
- Slash commands:
  - `https://<your-public-api-domain>/slack/commands`

Recommended scopes:

Bot scopes:

- `chat:write`
- `commands`
- `channels:read`
- `groups:read`
- `users:read`

User scopes:

- `users:read.email`

## Gmail extension build against Railway

For a Railway-hosted backend:

```bash
cd ui/gmail-extension
CLEARLEDGR_API_URL=https://<your-public-api-domain> ./build.sh railway
```

Then load the unpacked build from `ui/gmail-extension/build`.

## Recommended rollout order

1. Deploy Postgres and Redis.
2. Deploy the `api` service with `CLEARLEDGR_PROCESS_ROLE=web`.
3. Deploy the `worker` service with `CLEARLEDGR_PROCESS_ROLE=worker`.
4. Set Slack callback URLs to the Railway public domain.
5. Set Gmail OAuth redirect URIs to the Railway public domain.
6. Reconnect Slack from inside Clearledgr.
7. Reconnect Gmail from inside Clearledgr.
8. Build the Gmail extension against the Railway backend URL.

## Sanity checks

API:

```bash
curl https://<your-public-api-domain>/health
```

Expected: HTTP 200 with `{"status":"healthy", ...}`

Worker:

- Railway logs should show:
  - `Gmail autopilot started`
  - `Agent background intelligence started`
  - `Finance agent runtime started`

## Current caveats

- The Gmail extension still defaults to localhost in raw dev mode; build it with the Railway backend URL for cloud usage.
- Any Google/Slack console config still pointing at localhost will break reconnect flows even if Railway is healthy.
- SQLite fallback remains for local development only. Use managed Postgres in Railway.
