# soldenai-landing

Marketing site for **soldenai.com**. Static HTML/CSS plus a small
Express server that:

- Serves the marketing pages with strict CSP, HSTS, and immutable
  asset caching.
- Receives contact-form submissions on `POST /api/contact` and writes
  them to a dedicated Postgres database.
- Optionally fires a Slack notification when a lead lands.

The product (workspace SPA + API) lives elsewhere, this directory is
marketing only, with its own dedicated database. The product DB is
intentionally isolated from this service.

## Structure

```
soldenai-landing/
├── index.html        # hero, how it works, render targets, trust, CTA
├── about.html        # mission + founders + backing
├── security.html     # SOC2 posture, DPA, controls, sub-processors
├── contact.html      # demo / sales / support form
├── styles.css        # design tokens + homepage layout
├── pages.css         # sub-page primitives (subpage hero, prose, founders, form)
├── site.js           # year stamp + contact-form fetch
├── server.js         # Express static server + /api/contact + CSP / HSTS / health
├── scripts/
│   └── list-leads.js # `npm run leads`, print recent submissions
├── railway.toml      # Railway build/deploy config
├── package.json
└── assets/           # brand PNGs (lockups + favicons)
```

## Running locally

```bash
cd soldenai-landing
npm install

# Provide a DATABASE_URL pointing at any Postgres you have locally.
# The schema (leads table + indexes) is created on first boot.
export DATABASE_URL="postgresql://you@localhost:5432/soldenai_landing"
npm start                                    # http://localhost:8080
```

The static site serves cleanly even if `DATABASE_URL` is missing ,
form submissions return `503 no_storage` in that case, with a logged
warning, so a misconfigured deploy is loud rather than silent.

To watch leads land:

```bash
npm run leads                                # last 50 by default
npm run leads -- --limit=200                 # bigger window
```

## Deploying to Railway

This service deploys as **its own Railway project**, separate from any
Clearledgr services. Steps:

1. **Create a new Railway service** from this repo. Set the service's
   **Root Directory** to `soldenai-landing/` so Railway only builds
   this folder.
2. **Provision a Postgres plugin** on the same project. Railway will
   auto-inject `DATABASE_URL` into the web service. No SSL toggle is
   needed for Railway's internal networking.
3. **Healthcheck**: already pinned to `/healthz` in `railway.toml`. The
   endpoint reports `db: "ok"` once Postgres is reachable, so a wrong
   `DATABASE_URL` shows up in Railway's healthcheck panel rather than
   on the contact page.
4. **Custom domain**: attach `soldenai.com` (and `www.soldenai.com`)
   to this service in the Railway dashboard.
5. **Optional Slack alerts**: set `SLACK_WEBHOOK_URL` to an incoming-
   webhook URL. Each lead fires a non-blocking Slack notification; the
   DB write is the source of truth, so Slack delivery isn't load-
   bearing.

That's it. Schema bootstrap (`CREATE TABLE IF NOT EXISTS leads ...`)
runs on every boot, so the first deploy creates the table without a
migration step.

### Required env

| Var            | Purpose                                              |
|----------------|------------------------------------------------------|
| `PORT`         | Provided by Railway. Defaults to `8080` locally.     |
| `DATABASE_URL` | Provided by the Railway Postgres plugin.             |

### Optional env

| Var                  | Purpose                                                    |
|----------------------|------------------------------------------------------------|
| `SLACK_WEBHOOK_URL`  | Per-lead notification target. Omit to disable.             |
| `DB_SSL`             | Set to `true` only when pointing at an external PG that requires TLS. Railway's internal network does not. |

## Schema

Single `leads` table, created idempotently on startup:

```sql
CREATE TABLE leads (
  id           BIGSERIAL PRIMARY KEY,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  name         TEXT NOT NULL,
  email        TEXT NOT NULL,
  company      TEXT,
  role         TEXT,
  erp          TEXT,
  topic        TEXT,
  message      TEXT,
  source       TEXT NOT NULL DEFAULT 'soldenai.com',
  ip_hash      TEXT,         -- sha256(IP)[:32], abuse signal w/o PII
  user_agent   TEXT
);
```

`ip_hash` is a truncated SHA-256 of the requesting IP, enough to
spot a single source spamming, without retaining PII directly.

Each request also runs through:

- **Honeypot** (`company_website` field). If filled, the response is a
  silent `200 ok` and nothing is stored.
- **Email + name validation**. Bad email or empty name to `400`.
- **Bounded body** (`32kb` JSON limit) and bounded per-field length.
- **Parameterised SQL**, no string interpolation.

## Brand

Brand kit lives in `ui/web-app/public/` (cropped lockups + favicon
variants). Any change to the canonical brand assets there should be
mirrored into `soldenai-landing/assets/`.

Brand colours: navy `#0A1F44` + teal `#18BFB0`. Inter (rsms.me) at 700
for headlines, 500/600 for body. Visual references: Linear, Vercel,
Modal, explicitly **not** BILL/Ramp/Stacks.ai.

## Content rules

- "Talk to sales" only, no public pricing on the marketing site.
- All four product claims (capture / validate / route / post) are
  verified against the codebase. Solden does not initiate payments.
- Solden authors zero vendor-facing email body text. Don't add copy
  that suggests otherwise.
- Render targets are five peers: Gmail, Slack, Teams, NetSuite, SAP
  Fiori. Don't lead with "Gmail-first" framing.

## Migration from `landing-page/`

The legacy Clearledgr-era `landing-page/` directory continues to run
at `clearledgr.com`. Once `soldenai.com` is live on Railway, point a
301 redirect from `clearledgr.com` to `soldenai.com` at the edge (or
inside `landing-page/server.js`) and retire the old service when
analytics confirm zero residual traffic.
