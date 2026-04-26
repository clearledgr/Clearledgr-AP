/**
 * Production server for workspace.clearledgr.com.
 *
 * Two responsibilities:
 *   1. Serve the static SPA build from `dist/`.
 *   2. Reverse-proxy API paths to the Clearledgr api service running
 *      on the same Railway project, so the browser sees a single
 *      origin (no CORS, no cookie-domain headaches — the workspace
 *      session cookie is scoped to workspace.clearledgr.com which is also
 *      what the browser hits for /api, /auth, /v1, etc.).
 *
 * Required env:
 *   PORT          — Railway-supplied bind port (defaults to 8080 locally)
 *   API_TARGET    — internal URL of the api service, e.g.
 *                   https://api.clearledgr.com or
 *                   http://api.railway.internal:8000 (Railway private net)
 *
 * Optional env:
 *   STATIC_DIR    — override the default 'dist' static folder
 *   PROXY_LOG     — '1' to enable verbose proxy logs (off by default)
 */
import express from 'express';
import { createProxyMiddleware } from 'http-proxy-middleware';
import path from 'node:path';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const PORT = Number(process.env.PORT || 8080);
const API_TARGET = process.env.API_TARGET;
const STATIC_DIR = path.resolve(__dirname, process.env.STATIC_DIR || 'dist');
const PROXY_LOG = process.env.PROXY_LOG === '1';

if (!API_TARGET) {
  console.error('[startup] API_TARGET env var is required (point at the api service URL).');
  process.exit(1);
}

if (!fs.existsSync(STATIC_DIR)) {
  console.error(`[startup] Static dir not found at ${STATIC_DIR}. Did the build run?`);
  process.exit(1);
}

const app = express();

// Don't advertise the framework on every response — minor info-disclosure
// hardening. No reason for clients to know we're on Express.
app.disable('x-powered-by');

// Trust the Railway edge proxy so req.protocol / req.ip reflect the
// real client, not the internal forwarding hop. Required for HSTS to
// only fire on requests that genuinely arrived over HTTPS.
app.set('trust proxy', 1);

// ── Security headers (workstream G) ─────────────────────────────────
//
// Hand-rolled rather than `helmet` so the policy is explicit and
// auditable. Applied to every response, including proxied API replies
// and the SPA fallback.
//
// CSP rationale:
//   default-src 'self'           — start strict; explicit allow per directive
//   script-src 'self'            — Vite emits ONE bundled JS file; no inline
//                                  scripts. Does NOT include 'unsafe-inline'
//                                  or 'unsafe-eval'.
//   style-src 'self' 'unsafe-inline' https://rsms.me
//                                — index-*.css from /assets/ + Inter from
//                                  rsms.me. 'unsafe-inline' kept because the
//                                  lifted PipelinePage/ReviewPage/etc. carry
//                                  inline style="..." attrs from their
//                                  extension origin (BatchOps STATE_STYLES,
//                                  per-row colour swatches). Tightening this
//                                  to nonce-based is a follow-up.
//   font-src 'self' https://rsms.me data:
//                                — rsms.me serves the woff2 referenced by
//                                  inter.css. data: covers any inline font
//                                  preloads.
//   img-src 'self' data: https:  — page may render Slack avatars / Google
//                                  profile photos via https:// URLs.
//   connect-src 'self'           — every API call same-origin (proxy).
//   frame-ancestors 'none'       — blocks iframe embedding (clickjacking).
//                                  Modern replacement for X-Frame-Options.
//   form-action 'self' https://accounts.google.com
//                                — Google OAuth start uses location.href so
//                                  this is defence-in-depth, not load-bearing.
//   base-uri 'self'              — prevents <base> tag injection from
//                                  redirecting relative URLs.
//   object-src 'none'            — no flash, no plugins.
//   upgrade-insecure-requests    — auto-rewrite http:// URLs in resources
//                                  to https://.
const CSP = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self' 'unsafe-inline' https://rsms.me",
  "font-src 'self' https://rsms.me data:",
  "img-src 'self' data: https:",
  "connect-src 'self'",
  "frame-ancestors 'none'",
  "form-action 'self' https://accounts.google.com",
  "base-uri 'self'",
  "object-src 'none'",
  "upgrade-insecure-requests",
].join('; ');

app.use((req, res, next) => {
  // Force HTTPS for a year, including subdomains, with preload eligibility.
  // `req.secure` honours the `trust proxy` setting above so we don't issue
  // HSTS on http traffic during local dev.
  if (req.secure) {
    res.setHeader(
      'Strict-Transport-Security',
      'max-age=31536000; includeSubDomains; preload'
    );
  }
  res.setHeader('Content-Security-Policy', CSP);
  res.setHeader('X-Content-Type-Options', 'nosniff');
  res.setHeader('Referrer-Policy', 'strict-origin-when-cross-origin');
  // Permissions-Policy disables browser features we never use. Keeps the
  // attack surface narrow if a dependency ever tries to access them.
  res.setHeader(
    'Permissions-Policy',
    'camera=(), microphone=(), geolocation=(), payment=(), usb=(), magnetometer=(), gyroscope=(), accelerometer=()'
  );
  // COOP isolates window references so cross-origin popups (e.g. Google
  // OAuth) don't leak access to window.opener. COEP intentionally NOT
  // set — it would block the rsms.me / data: / https: image loads we
  // currently allow via CSP.
  res.setHeader('Cross-Origin-Opener-Policy', 'same-origin');
  // X-Frame-Options is redundant with `frame-ancestors 'none'` above for
  // modern browsers, but kept for legacy IE-style fallbacks.
  res.setHeader('X-Frame-Options', 'DENY');
  next();
});

// Health endpoint used by Railway's healthchecker. Returns 200 once the
// process is up; the static dist + proxy target existence are validated
// at startup above so we don't need a deeper probe here.
app.get('/healthz', (_req, res) => res.json({ ok: true, service: 'web-app' }));

// Paths handled by the api service. Mounting the proxy via a single
// pathFilter at root preserves the full URL — `app.use('/auth', ...)`
// would strip the `/auth` prefix and forward `/me` to the api as
// just `/me`, which the api rejects with strict-profile 404.
//
// /healthz is intentionally absent: the Express server above answers
// it directly for Railway's per-service healthcheck.
const PROXY_PATHS = ['/api', '/auth', '/v1', '/extension', '/erp', '/portal', '/onboard', '/slack', '/teams', '/outlook', '/oauth'];

const proxy = createProxyMiddleware({
  target: API_TARGET,
  changeOrigin: true,
  xfwd: true,
  ws: true,
  logLevel: PROXY_LOG ? 'debug' : 'warn',
  pathFilter: (path) => PROXY_PATHS.some((p) => path === p || path.startsWith(`${p}/`)),
  // Preserve cookies on responses (the SPA needs the workspace session
  // Set-Cookie to land on workspace.clearledgr.com, not the upstream host).
  cookieDomainRewrite: '',
});

app.use(proxy);

// Static SPA. Cache JS/CSS aggressively (they're hashed by Vite); never
// cache index.html (it references the latest hashed asset names).
app.use(
  express.static(STATIC_DIR, {
    index: false,
    setHeaders(res, filePath) {
      if (filePath.endsWith('.html')) {
        res.setHeader('Cache-Control', 'no-store, max-age=0');
      } else if (/\.(js|css|woff2?|svg|png|jpg|webp|ico)$/.test(filePath)) {
        res.setHeader('Cache-Control', 'public, max-age=31536000, immutable');
      }
    },
  })
);

// SPA fallback — every non-asset, non-proxy path resolves to index.html
// so wouter handles client-side routing. Returns 404 for anything that
// looks like an asset to avoid serving the index for missing chunks.
app.get('*', (req, res) => {
  if (/\.[a-z0-9]{1,5}$/i.test(req.path)) {
    return res.status(404).send('Not found');
  }
  res.setHeader('Cache-Control', 'no-store, max-age=0');
  res.sendFile(path.join(STATIC_DIR, 'index.html'));
});

app.listen(PORT, () => {
  console.log(
    `[startup] web-app listening on :${PORT} — static=${STATIC_DIR}, api_target=${API_TARGET}`
  );
});
