/**
 * Production server for soldenai.com.
 *
 * Two responsibilities:
 *   1. Serve the static marketing HTML/CSS/JS with strict CSP + HSTS.
 *   2. Receive contact-form submissions and write them to a small
 *      Postgres database dedicated to the landing site.
 *
 * The product DB (clearledgr core) is intentionally isolated from
 * this service, marketing leads have a different blast radius and
 * different ops model than production AP data.
 *
 * Required env:
 *   PORT         , Railway-supplied bind port (defaults to 8080 locally).
 *   DATABASE_URL , Postgres connection string. If absent, the form
 *                   endpoint returns 503 and logs a warning, but the
 *                   static site still serves cleanly.
 *
 * Optional env:
 *   SLACK_WEBHOOK_URL, if set, fire a non-blocking Slack notification
 *                       when a lead lands. DB write is the source of
 *                       truth; Slack is human-visibility only.
 *   DB_SSL           , "true" to force TLS on the PG connection. Most
 *                       Railway internal connections don't need it; set
 *                       this only when pointing at an external PG that
 *                       requires SSL.
 */
import express from 'express';
import path from 'node:path';
import fs from 'node:fs';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import pg from 'pg';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const PORT = Number(process.env.PORT || 8080);
const STATIC_DIR = path.resolve(__dirname);
const DATABASE_URL = process.env.DATABASE_URL || '';
const SLACK_WEBHOOK_URL = process.env.SLACK_WEBHOOK_URL || '';

if (!fs.existsSync(path.join(STATIC_DIR, 'index.html'))) {
  console.error(`[startup] index.html not found at ${STATIC_DIR}`);
  process.exit(1);
}

// ── Postgres pool ─────────────────────────────────────────────
//
// Lazily connected, the static site stays up even if Postgres is
// down or unconfigured. The form endpoint surfaces a 503 in that case
// so the operator sees a clear error rather than silent data loss.
let pool = null;
if (DATABASE_URL) {
  pool = new pg.Pool({
    connectionString: DATABASE_URL,
    ssl:
      process.env.DB_SSL === 'true'
        ? { rejectUnauthorized: false }
        : false,
    max: 4,
    idleTimeoutMillis: 30_000,
    connectionTimeoutMillis: 10_000,
  });
  pool.on('error', (err) => {
    console.error('[pg] idle client error', err.message);
  });
} else {
  console.warn(
    '[startup] DATABASE_URL not set, contact submissions will be rejected with 503'
  );
}

async function ensureSchema() {
  if (!pool) return;
  // Idempotent: safe to run on every boot. The marketing site has no
  // formal migration framework, the schema is small enough to keep
  // co-located with the code that uses it.
  await pool.query(`
    CREATE TABLE IF NOT EXISTS leads (
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
      ip_hash      TEXT,
      user_agent   TEXT
    );
  `);
  await pool.query(
    `CREATE INDEX IF NOT EXISTS idx_leads_created_at ON leads (created_at DESC);`
  );
  await pool.query(
    `CREATE INDEX IF NOT EXISTS idx_leads_email ON leads (lower(email));`
  );
}

// ── Express ───────────────────────────────────────────────────
const app = express();
app.disable('x-powered-by');
app.set('trust proxy', 1);

// Tight body limit, the form is short. Anything larger is abuse.
app.use(express.json({ limit: '32kb' }));

// ── Security headers ─────────────────────────────────────────
//
// CSP rationale:
//   default-src 'self'          , start strict; explicit allow per directive
//   script-src 'self'            , no inline scripts (site.js is external)
//   style-src 'self' 'unsafe-inline'
//                                , a few inline style attrs on hero swatches
//   font-src 'self' data:        , Geist self-hosted from /assets/fonts/
//   img-src 'self' data:
//   connect-src 'self'           , fetch only same-origin (POST /api/contact)
//   form-action 'self'           , form posts only same-origin
//   frame-ancestors 'none'       , no clickjacking
const CSP = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "font-src 'self' data:",
  "img-src 'self' data:",
  "connect-src 'self'",
  "frame-ancestors 'none'",
  "form-action 'self'",
  "base-uri 'self'",
  "object-src 'none'",
  "upgrade-insecure-requests",
].join('; ');

app.use((req, res, next) => {
  if (req.secure) {
    res.setHeader(
      'Strict-Transport-Security',
      'max-age=31536000; includeSubDomains; preload'
    );
  }
  res.setHeader('Content-Security-Policy', CSP);
  res.setHeader('X-Content-Type-Options', 'nosniff');
  res.setHeader('Referrer-Policy', 'strict-origin-when-cross-origin');
  res.setHeader(
    'Permissions-Policy',
    'camera=(), microphone=(), geolocation=(), payment=(), usb=()'
  );
  res.setHeader('Cross-Origin-Opener-Policy', 'same-origin');
  res.setHeader('X-Frame-Options', 'DENY');
  next();
});

// ── Healthcheck ──────────────────────────────────────────────
//
// Reports DB reachability so a misconfigured deploy is visible in
// Railway's healthcheck panel, not just at form-submit time.
app.get('/healthz', async (_req, res) => {
  // Liveness is about the web server, not the leads DB. The static
  // marketing site stays up even when Postgres is unreachable; only
  // contact-form submissions degrade (they 503 at submit time). So
  // /healthz always returns 200 when the server is running and just
  // REPORTS db reachability for visibility. Gating the healthcheck on
  // the DB previously took the whole site down whenever the leads
  // Postgres was down.
  const out = { ok: true, service: 'soldenai-landing', db: 'unconfigured' };
  if (pool) {
    try {
      await pool.query('SELECT 1');
      out.db = 'ok';
    } catch (err) {
      out.db = 'down';
      out.dbError = err.message;
    }
  }
  res.status(200).json(out);
});

// ── Contact form ─────────────────────────────────────────────
//
// Fields match the markup in contact.html. Honeypot field is named
// `company_website`; bots fill it because the label says so, real
// users never see it (CSS off-screen). Honeypot trips to silent 200,
// no DB write, no Slack notify.
app.post('/api/contact', async (req, res) => {
  try {
    const body = req.body || {};

    // Honeypot, return 200 so bots don't get useful signal.
    if (body.company_website && String(body.company_website).trim() !== '') {
      return res.json({ ok: true });
    }

    // Light validation. Every field is bounded; SQL parameterised.
    const name = String(body.name || '').trim();
    const email = String(body.email || '').trim();
    if (!name || name.length > 200) {
      return res.status(400).json({ ok: false, error: 'invalid_name' });
    }
    if (!email || !email.includes('@') || email.length > 254) {
      return res.status(400).json({ ok: false, error: 'invalid_email' });
    }

    if (!pool) {
      console.warn('[contact] DATABASE_URL missing, dropping submission');
      return res.status(503).json({ ok: false, error: 'no_storage' });
    }

    // Hash the IP so we have an abuse signal without retaining PII
    // directly. Sixteen bytes is plenty to spot the same source.
    const fwd = String(req.headers['x-forwarded-for'] || '').split(',')[0].trim();
    const ip = fwd || req.ip || '';
    const ipHash = ip
      ? crypto.createHash('sha256').update(ip).digest('hex').slice(0, 32)
      : null;

    const trimOrNull = (v, max) => {
      const s = String(v || '').trim();
      return s ? s.slice(0, max) : null;
    };

    const result = await pool.query(
      `INSERT INTO leads (
        name, email, company, role, erp, topic, message, ip_hash, user_agent
      ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
      RETURNING id, created_at`,
      [
        name.slice(0, 200),
        email.slice(0, 254),
        trimOrNull(body.company, 200),
        trimOrNull(body.role, 200),
        trimOrNull(body.erp, 60),
        trimOrNull(body.topic, 60),
        trimOrNull(body.message, 5000),
        ipHash,
        trimOrNull(req.headers['user-agent'], 500),
      ]
    );

    const row = result.rows[0];
    console.log(`[contact] lead #${row.id} stored (${email})`);

    // Non-blocking Slack notify. DB insert above is the source of
    // truth; if Slack fails the lead is still safe.
    notifySlack({
      id: row.id,
      name,
      email,
      company: body.company,
      role: body.role,
      erp: body.erp,
      topic: body.topic,
      message: body.message,
    }).catch((err) =>
      console.warn('[contact] slack notify failed:', err.message)
    );

    return res.json({ ok: true });
  } catch (err) {
    console.error('[contact] insert failed', err);
    return res.status(500).json({ ok: false, error: 'server_error' });
  }
});

async function notifySlack(lead) {
  if (!SLACK_WEBHOOK_URL) return;
  const lines = [
    `:envelope_with_arrow: *New Solden lead* #${lead.id}`,
    `*${lead.name}*, ${lead.email}`,
    lead.company ? `Company: ${lead.company}` : null,
    lead.role ? `Role: ${lead.role}` : null,
    lead.erp ? `ERP: ${lead.erp}` : null,
    lead.topic ? `Topic: ${lead.topic}` : null,
    lead.message ? `> ${String(lead.message).slice(0, 500)}` : null,
  ].filter(Boolean);
  await fetch(SLACK_WEBHOOK_URL, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ text: lines.join('\n') }),
  });
}

// ── Live activity stream (SSE) ───────────────────────────────
//
// The marketing site shows an "agent activity" ribbon in the hero.
// In production we'd subscribe to a sandbox tenant on the real
// product (`api.clearledgr.com/api/workspace/dashboard/stream`).
// Until that's wired, we synthesize a believable demo loop here
// so the page feels live in production. Each connected client
// gets a personal SSE stream; the server emits one frame every
// 8-22 seconds.
//
// To swap in real data later, set DEMO_ACTIVITY=false and proxy
// the SSE from the production stream. The client doesn't need to
// change.

const DEMO_ACTIVITY = process.env.DEMO_ACTIVITY !== 'false';

const VENDOR_POOL = [
  { name: 'Northwind Logistics',  amount: 18420.00, gl: '5210 Logistics' },
  { name: 'Acme Office Supplies', amount:   612.40, gl: '6310 Office'    },
  { name: 'Globex Software',      amount:  4900.00, gl: '6420 SaaS'      },
  { name: 'Initech Cleaning Co.', amount:   780.00, gl: '6310 Office'    },
  { name: 'Soylent Print',        amount:   244.18, gl: '6320 Marketing' },
  { name: 'Wayland Freight',      amount:  9320.00, gl: '5210 Logistics' },
  { name: 'Massive Dynamic',      amount: 22600.00, gl: '5410 R&D'       },
  { name: 'Tyrell Components',    amount:  6740.00, gl: '5310 COGS'      },
  { name: 'Stark Industries',     amount: 11800.00, gl: '5310 COGS'      },
  { name: 'Cyberdyne Systems',    amount:  3520.00, gl: '6420 SaaS'      },
];

const APPROVERS = ['priya', 'marcus', 'amaka', 'felix', 'sara', 'jonas'];

function pick(arr) { return arr[Math.floor(Math.random() * arr.length)]; }

function fmtAmount(n) {
  return '€' + n.toLocaleString('en-IE', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// Each invoice progresses through a small lifecycle. Rather than
// emit independent random events, we maintain per-stream state so
// the ribbon reads as ordered work, not noise.
function nextDemoEvent(state) {
  const stages = ['captured', 'validated', 'routed', 'posted', 'logged'];
  if (!state.current) {
    state.current = { vendor: pick(VENDOR_POOL), stage: 0, approver: pick(APPROVERS), poRef: 4800 + Math.floor(Math.random() * 200) };
  }
  const cur = state.current;
  const stage = stages[cur.stage];
  let action, subject, surface, tone;
  switch (stage) {
    case 'captured':
      action = 'captured';
      subject = `invoice from ${cur.vendor.name}, ${fmtAmount(cur.vendor.amount)}`;
      surface = 'gmail';
      tone = 'info';
      break;
    case 'validated':
      action = 'validated';
      subject = `3-way match, PO #${cur.poRef}, vendor verified`;
      surface = 'agent';
      tone = 'brand';
      break;
    case 'routed':
      action = 'routed';
      subject = `to ${cur.approver} for approval, ${cur.vendor.amount > 5000 ? 'over €5k threshold' : 'standard band'}`;
      surface = 'slack';
      tone = 'warning';
      break;
    case 'posted':
      action = 'posted';
      subject = `bill to NetSuite, GL ${cur.vendor.gl}, vendor balance updated`;
      surface = 'netsuite';
      tone = 'success';
      break;
    case 'logged':
      action = 'logged';
      subject = `audit event AP-${9000 + Math.floor(Math.random() * 999)}, append-only`;
      surface = 'agent';
      tone = 'info';
      break;
  }
  cur.stage += 1;
  if (cur.stage >= stages.length) state.current = null;
  return {
    id: 'ev_' + Date.now().toString(36) + Math.floor(Math.random() * 1000).toString(36),
    ts: new Date().toISOString(),
    action,
    subject,
    surface,
    tone,
  };
}

app.get('/api/activity-stream', (req, res) => {
  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-store, no-transform');
  res.setHeader('Connection', 'keep-alive');
  res.setHeader('X-Accel-Buffering', 'no');
  res.flushHeaders?.();

  const state = {};
  let alive = true;

  // Heartbeat every 25s so proxies don't kill the connection.
  const heartbeat = setInterval(() => {
    if (!alive) return;
    try { res.write(': ping\n\n'); } catch { /* ignore */ }
  }, 25_000);

  // Initial frame after a short pause so the client's static
  // ribbon has a moment to render before the first new row.
  function emit() {
    if (!alive || !DEMO_ACTIVITY) return;
    const ev = nextDemoEvent(state);
    try {
      res.write(`event: activity\ndata: ${JSON.stringify(ev)}\n\n`);
    } catch {
      cleanup();
      return;
    }
    // Next event in 8-22s, weighted toward the lower end.
    const delay = 8_000 + Math.floor(Math.random() * 14_000);
    setTimeout(emit, delay);
  }
  setTimeout(emit, 4_000 + Math.floor(Math.random() * 3_000));

  function cleanup() {
    alive = false;
    clearInterval(heartbeat);
    try { res.end(); } catch { /* ignore */ }
  }
  req.on('close', cleanup);
  req.on('aborted', cleanup);
});

// ── Static assets ────────────────────────────────────────────
app.use(
  express.static(STATIC_DIR, {
    extensions: ['html'],
    setHeaders(res, filePath) {
      if (filePath.endsWith('.html')) {
        res.setHeader('Cache-Control', 'no-store, max-age=0');
      } else if (/\.(js|css|woff2?|svg|png|jpg|webp|ico)$/.test(filePath)) {
        res.setHeader('Cache-Control', 'public, max-age=31536000, immutable');
      }
    },
  })
);

// ── 404 ──────────────────────────────────────────────────────
app.use((req, res) => {
  res.status(404).type('html').send(
    '<!doctype html><title>Not found · Solden</title>' +
      '<p style="font-family:Inter,system-ui;padding:48px">' +
      'Not found. <a href="/">Back to home</a>.</p>'
  );
});

// ── Boot ─────────────────────────────────────────────────────
ensureSchema()
  .then(() => {
    if (pool) console.log('[startup] schema ensured (leads table)');
    app.listen(PORT, () => {
      console.log(
        `[startup] soldenai-landing listening on :${PORT}, static=${STATIC_DIR}`
      );
    });
  })
  .catch((err) => {
    // Schema bootstrap failure is fatal, better to crash loudly than
    // serve a form that silently drops every submission.
    console.error('[startup] failed to ensure schema:', err.message);
    process.exit(1);
  });
