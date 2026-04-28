/**
 * Status-aware favicon badge for the workspace SPA.
 *
 * The base favicon (the Clearledgr logomark) lives at /favicon.png and
 * is referenced by ``<link rel="icon" href="/favicon.png">`` in
 * ``index.html``. When there's something operator-actionable in the
 * pipeline (pending approvals waiting on the current user), this
 * module composites a small red badge onto the upper-right of the
 * mark and swaps the link's href to the resulting data URL — same
 * pattern Gmail / Slack / Linear use to make the tab a notification
 * surface.
 *
 * Public API:
 *   • setFaviconBadge(count: number) — count > 0 paints the badge
 *     (with the count drawn inside if 1..9, "9+" otherwise);
 *     count === 0 restores the unbadged base favicon.
 *
 * The first call asynchronously loads + caches the base bitmap.
 * Subsequent calls reuse the cache so updates are synchronous to the
 * caller and the canvas operation is cheap (~1ms on a 32×32 canvas).
 *
 * Why the badge is red and not brand-mint:
 *   The mint color in the underlying mark already represents brand /
 *   "good." A badge sitting on top must read as "needs your attention"
 *   at 16×16 — red is the universal pattern for that. Mint badge on a
 *   mint-flecked logo would be invisible.
 */

const BASE_FAVICON_URL = '/favicon.png';
const CANVAS_SIZE = 64; // 64×64 source — browsers downscale to 16/32 cleanly
const LINK_ID_HINT = 'cl-favicon-dynamic';

// Memoise the base bitmap and the rendered data-URLs so we don't
// re-decode the PNG or re-paint the canvas when the count is the same.
let _baseImagePromise = null;
let _lastRenderedKey = null;
let _baseDataUrl = null;

function _loadBaseImage() {
  if (_baseImagePromise) return _baseImagePromise;
  _baseImagePromise = new Promise((resolve, reject) => {
    if (typeof Image === 'undefined') {
      // Non-browser env (SSR / unit test). Resolve with null so callers
      // gracefully no-op.
      resolve(null);
      return;
    }
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.onload = () => resolve(img);
    img.onerror = (err) => reject(err);
    // Cache-bust query param so a deploy that ships a new mark is
    // picked up on next reload without forcing the user to clear cache.
    img.src = BASE_FAVICON_URL + '?fav=base';
  });
  return _baseImagePromise;
}

/**
 * Format a count for badge display. ≥10 collapses to "9+" — anything
 * larger stops being a useful number in 16×16 anyway, and a static cap
 * keeps the rendered character width predictable so the badge circle
 * never overflows.
 */
export function formatBadgeCount(count) {
  const n = Math.max(0, Math.floor(Number(count) || 0));
  if (n === 0) return '';
  if (n >= 10) return '9+';
  return String(n);
}

/**
 * Decide which `<link rel="icon">` href the document should currently
 * have. Exposed for unit tests.
 *
 * Returns one of:
 *   • a data: URL — a count > 0 has been rendered and painted
 *   • the base favicon URL — count is 0, restore the unbadged mark
 *   • null — pre-load (the base bitmap hasn't decoded yet)
 */
export function decideFaviconHref({ count, baseDataUrl, paintedDataUrl }) {
  const n = Math.max(0, Math.floor(Number(count) || 0));
  if (n <= 0) {
    return baseDataUrl || BASE_FAVICON_URL;
  }
  return paintedDataUrl || baseDataUrl || BASE_FAVICON_URL;
}

function _ensureLink() {
  if (typeof document === 'undefined') return null;
  // Replace the static <link rel="icon"> with a dedicated dynamic one
  // on first use, and keep using that node for subsequent updates.
  // Browsers re-fetch the icon on href change, but only if the *node*
  // is the live <link rel="icon"> in the head.
  let link = document.querySelector(`link[data-id="${LINK_ID_HINT}"]`);
  if (link) return link;
  // Promote whichever <link rel="icon"> exists, or create one.
  link = document.querySelector('link[rel="icon"]');
  if (!link) {
    link = document.createElement('link');
    link.setAttribute('rel', 'icon');
    document.head.appendChild(link);
  }
  link.setAttribute('data-id', LINK_ID_HINT);
  link.setAttribute('type', 'image/png');
  return link;
}

function _paint(baseImg, count) {
  if (typeof document === 'undefined') return null;
  const canvas = document.createElement('canvas');
  canvas.width = CANVAS_SIZE;
  canvas.height = CANVAS_SIZE;
  const ctx = canvas.getContext('2d');
  if (!ctx) return null;

  // Base mark
  ctx.drawImage(baseImg, 0, 0, CANVAS_SIZE, CANVAS_SIZE);

  const text = formatBadgeCount(count);
  if (!text) {
    return canvas.toDataURL('image/png');
  }

  // Badge — red disk in the upper-right, slightly inset so the edge
  // anti-aliases cleanly when the browser downscales to 16×16.
  // Sizes chosen so a 1-character count remains legible at 16×16.
  const radius = CANVAS_SIZE * 0.32;
  const cx = CANVAS_SIZE - radius - 2;
  const cy = radius + 2;

  // White outline ring — gives the badge contrast against any underlying
  // mark color (the Clearledgr mark has both navy and mint pixels).
  ctx.beginPath();
  ctx.arc(cx, cy, radius + 2, 0, Math.PI * 2);
  ctx.fillStyle = '#ffffff';
  ctx.fill();

  // Red disk
  ctx.beginPath();
  ctx.arc(cx, cy, radius, 0, Math.PI * 2);
  ctx.fillStyle = '#e02020';
  ctx.fill();

  // Number
  ctx.fillStyle = '#ffffff';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  // Bold sans matches the design system's headline weight; system-ui
  // keeps the file portable without bundling a webfont.
  const fontSize = text.length === 1 ? CANVAS_SIZE * 0.42 : CANVAS_SIZE * 0.32;
  ctx.font = `700 ${Math.round(fontSize)}px system-ui, -apple-system, "Segoe UI", sans-serif`;
  // Optical centering: drop the baseline ~1px to compensate for the
  // bold descender lift on the digits.
  ctx.fillText(text, cx, cy + 1);

  return canvas.toDataURL('image/png');
}

/**
 * Paint the badge for *count* and update the document's
 * ``<link rel="icon">``. count <= 0 restores the base favicon.
 *
 * Idempotent: repeated calls with the same count are cheap (no
 * re-paint, no DOM mutation if href is unchanged).
 */
export async function setFaviconBadge(count) {
  const n = Math.max(0, Math.floor(Number(count) || 0));
  const cacheKey = `count:${n}`;
  if (_lastRenderedKey === cacheKey) return;

  if (typeof document === 'undefined') {
    _lastRenderedKey = cacheKey;
    return;
  }
  const link = _ensureLink();
  if (!link) return;

  let baseImg;
  try {
    baseImg = await _loadBaseImage();
  } catch (err) {
    // Network/CORS issue loading the base PNG. Fall back to the static
    // path so the tab still shows *some* icon.
    link.setAttribute('href', BASE_FAVICON_URL);
    _lastRenderedKey = cacheKey;
    return;
  }
  if (!baseImg) {
    _lastRenderedKey = cacheKey;
    return;
  }

  // Cache the unbadged data URL once — it's what we restore to when
  // count drops back to 0, and it's the no-network reference for the
  // base bitmap.
  if (!_baseDataUrl) {
    _baseDataUrl = _paint(baseImg, 0);
  }

  if (n === 0) {
    link.setAttribute('href', _baseDataUrl || BASE_FAVICON_URL);
    _lastRenderedKey = cacheKey;
    return;
  }

  const painted = _paint(baseImg, n);
  if (painted) {
    link.setAttribute('href', painted);
  } else {
    link.setAttribute('href', _baseDataUrl || BASE_FAVICON_URL);
  }
  _lastRenderedKey = cacheKey;
}

// Test-only: reset the module's memoised state. Avoid calling from
// production code — there's no reason to.
export function _resetForTests() {
  _baseImagePromise = null;
  _lastRenderedKey = null;
  _baseDataUrl = null;
}
