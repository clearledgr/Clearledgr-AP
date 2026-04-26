/**
 * Production server for app.clearledgr.com.
 *
 * Two responsibilities:
 *   1. Serve the static SPA build from `dist/`.
 *   2. Reverse-proxy API paths to the Clearledgr api service running
 *      on the same Railway project, so the browser sees a single
 *      origin (no CORS, no cookie-domain headaches — the workspace
 *      session cookie is scoped to app.clearledgr.com which is also
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
  // Set-Cookie to land on app.clearledgr.com, not the upstream host).
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
