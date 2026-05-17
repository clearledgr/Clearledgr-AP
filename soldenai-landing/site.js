/**
 * site.js, tiny shared client behaviour for soldenai.com.
 *
 * Loaded from every page. Intentionally framework-free: the marketing
 * site is static HTML by design.
 *
 * Two responsibilities:
 *   1. Stamp the current year into any `[data-year]` element.
 *   2. Wire the contact form (if present), POST to /api/contact and
 *      flip into success or error state in place.
 */

(function () {
  // ── 1. Year stamps ──
  var yearNodes = document.querySelectorAll('[data-year]');
  var year = String(new Date().getFullYear());
  for (var i = 0; i < yearNodes.length; i++) {
    yearNodes[i].textContent = year;
  }

  // ── 1b. Flow signature: pinned card morphs as the reader
  // scrolls past copy steps on the right. IntersectionObserver
  // fires when each step crosses the middle of the viewport. ──
  initFlow();

  // ── 1c. Live activity ribbon: subscribe to /api/activity-stream
  // and prepend new rows. Initial 5 rows are static HTML so the
  // page paints immediately even if SSE is slow or blocked. ──
  initLiveRibbon();

  // ── 1d. Topbar gets a hairline border + tighter blur once the
  // user scrolls past the hero. Linear pattern. ──
  initTopbarScroll();

  // ── 1e. Sections fade up when they enter the viewport. ──
  initScrollReveal();

  // ── 1f. Runtime concept timeline: advance the active bullet
  // (future → active → past) as the reader scrolls. ──
  initRuntimeBullets();

  function initFlow() {
    var card = document.querySelector('[data-flow-state]');
    if (!card || typeof IntersectionObserver === 'undefined') return;

    var titleEl  = card.querySelector('[data-flow-title]');
    var statusEl = card.querySelector('[data-flow-status]');
    var states = {};
    var stateNodes = card.querySelectorAll('.flow__state');
    for (var i = 0; i < stateNodes.length; i++) {
      var cls = stateNodes[i].className.match(/flow__state--(\w+)/);
      if (cls) states[cls[1]] = stateNodes[i];
    }

    var titles = {
      capture:  'gmail · ap@your-co.example',
      validate: 'match · PO · GRN · invoice',
      route:    'slack · #finance-approvals',
      post:     'netsuite.bill · POST',
      logged:   'audit_chain · ap_items',
    };
    var statuses = {
      capture:  'captured',
      validate: 'matched',
      route:    'awaiting approval',
      post:     '200 OK · 142ms',
      logged:   'sealed',
    };

    function setActive(key) {
      if (!states[key]) return;
      var keys = Object.keys(states);
      for (var j = 0; j < keys.length; j++) {
        states[keys[j]].classList.toggle('is-active', keys[j] === key);
      }
      if (titleEl)  titleEl.textContent  = titles[key]  || titles.capture;
      if (statusEl) statusEl.textContent = statuses[key] || '';
      card.setAttribute('data-flow-state', key);
    }

    var steps = document.querySelectorAll('[data-flow-step]');
    var observer = new IntersectionObserver(function (entries) {
      // Pick the entry with the largest intersection ratio that's
      // currently visible. Avoids flicker when two steps both cross
      // the viewport during a fast scroll.
      var best = null;
      entries.forEach(function (e) {
        if (!e.isIntersecting) return;
        if (!best || e.intersectionRatio > best.intersectionRatio) best = e;
      });
      if (best) setActive(best.target.getAttribute('data-flow-step'));
    }, { rootMargin: '-42% 0px -42% 0px', threshold: [0, 0.25, 0.5, 0.75, 1] });

    for (var k = 0; k < steps.length; k++) observer.observe(steps[k]);
    setActive('capture');
  }

  function initLiveRibbon() {
    var list = document.querySelector('.ribbon__list');
    var meta = document.querySelector('.ribbon__head-meta');
    if (!list || typeof EventSource === 'undefined') return;

    var MAX_ROWS = 5;
    var TONE_DOTS = {
      brand:   'teal',
      info:    'blue',
      success: 'green',
      warning: 'amber',
      neutral: 'gray',
    };
    var SURFACE_LABEL = {
      gmail:    'gmail',
      slack:    'slack',
      teams:    'teams',
      netsuite: 'netsuite',
      sap:      'sap',
      agent:    null,  // hidden — see real workspace ribbon convention
    };

    function escape(s) {
      return String(s == null ? '' : s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }

    function relative(tsIso) {
      var d = new Date(tsIso);
      if (isNaN(d.getTime())) return '';
      var sec = Math.round((Date.now() - d.getTime()) / 1000);
      if (sec < 5) return 'just now';
      if (sec < 60) return sec + 's ago';
      if (sec < 3600) return Math.round(sec / 60) + 'm ago';
      return Math.round(sec / 3600) + 'h ago';
    }

    function render(ev) {
      var dot = TONE_DOTS[ev.tone] || 'gray';
      var surfaceLabel = SURFACE_LABEL[ev.surface];
      var li = document.createElement('li');
      li.className = 'ribbon__row';
      li.setAttribute('data-ts', ev.ts || new Date().toISOString());
      li.innerHTML =
        '<span class="ribbon__dot ribbon__dot--' + escape(dot) + '" aria-hidden="true"></span>' +
        '<div class="ribbon__body">' +
          '<div class="ribbon__line">' +
            '<span class="ribbon__verb">' + escape(ev.action || 'event') + '</span>' +
            '<span class="ribbon__subject">' + escape(ev.subject || '') + '</span>' +
          '</div>' +
          '<div class="ribbon__meta">' +
            '<span class="ribbon__time">' + escape(relative(ev.ts)) + '</span>' +
            '<span class="ribbon__sep">·</span>' +
            '<span class="ribbon__actor">Agent</span>' +
            (surfaceLabel
              ? ('<span class="ribbon__sep">·</span><span class="ribbon__surface">via ' + escape(surfaceLabel) + '</span>')
              : '') +
          '</div>' +
        '</div>';
      // Prepend, then trim to MAX_ROWS.
      list.insertBefore(li, list.firstChild);
      while (list.children.length > MAX_ROWS) list.removeChild(list.lastChild);
    }

    function refreshTimes() {
      var rows = list.querySelectorAll('.ribbon__row');
      for (var i = 0; i < rows.length; i++) {
        var ts = rows[i].getAttribute('data-ts');
        if (!ts) continue;
        var t = rows[i].querySelector('.ribbon__time');
        if (t) t.textContent = relative(ts);
      }
    }

    // Stamp the existing static rows with synthetic timestamps so
    // they age properly while the live feed warms up.
    var staticRows = list.querySelectorAll('.ribbon__row');
    var now = Date.now();
    var ages = [0, 12_000, 44_000, 80_000, 110_000];  // newest to oldest
    for (var s = 0; s < staticRows.length; s++) {
      staticRows[s].setAttribute('data-ts', new Date(now - (ages[s] || (s * 30_000))).toISOString());
    }
    refreshTimes();
    setInterval(refreshTimes, 8_000);

    var source;
    try {
      source = new EventSource('/api/activity-stream');
    } catch (err) {
      return;
    }
    source.addEventListener('activity', function (e) {
      try {
        var ev = JSON.parse(e.data);
        render(ev);
        if (meta && !meta.dataset.liveBound) {
          meta.dataset.liveBound = '1';
          // Keep the "Live, last 5" label as-is; the pulse dot
          // already conveys liveness. No edit needed here.
        }
      } catch (err) { /* ignore bad frame */ }
    });
    source.onerror = function () {
      // EventSource auto-reconnects on transient errors. Only
      // close on terminal failures.
      if (source.readyState === 2) source.close();
    };
  }

  function initTopbarScroll() {
    var topbar = document.querySelector('.topbar');
    if (!topbar) return;
    var THRESHOLD = 60;
    var ticking = false;
    function update() {
      ticking = false;
      topbar.classList.toggle('is-scrolled', (window.scrollY || window.pageYOffset || 0) > THRESHOLD);
    }
    function onScroll() {
      if (ticking) return;
      ticking = true;
      window.requestAnimationFrame(update);
    }
    window.addEventListener('scroll', onScroll, { passive: true });
    update();
  }

  function initScrollReveal() {
    if (typeof IntersectionObserver === 'undefined') return;
    var nodes = document.querySelectorAll('[data-reveal]');
    if (!nodes.length) return;
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) {
          e.target.classList.add('is-revealed');
          io.unobserve(e.target);
        }
      });
    }, { threshold: 0.12, rootMargin: '0px 0px -8% 0px' });
    nodes.forEach(function (n) { io.observe(n); });
  }

  function initRuntimeBullets() {
    var track = document.querySelector('.runtime__scroll-track');
    var bullets = document.querySelectorAll('.runtime__bullet');
    if (!track || !bullets.length) return;

    function setStates(activeIdx) {
      for (var i = 0; i < bullets.length; i++) {
        var state = i < activeIdx ? 'past' : (i === activeIdx ? 'active' : 'future');
        bullets[i].setAttribute('data-state', state);
      }
    }

    // Compute scroll progress through the sticky-pinned scroll track.
    // The track is multi-viewport tall; the sticky-frame pins inside
    // it. Progress is 0 when the track's top hits the viewport top,
    // 1 when its bottom hits the viewport bottom. Divide that range
    // into one segment per bullet, so each bullet stays active for
    // its share of the scroll distance (~70vh per bullet at the
    // current 310vh track height).
    function update() {
      var rect = track.getBoundingClientRect();
      var viewportH = window.innerHeight || document.documentElement.clientHeight;
      var scrollable = rect.height - viewportH;
      if (scrollable <= 0) {
        // Mobile / non-sticky fallback: mark everything active.
        for (var i = 0; i < bullets.length; i++) bullets[i].setAttribute('data-state', 'active');
        return;
      }
      // -rect.top goes from 0 (track top at viewport top) to
      // scrollable (track bottom at viewport bottom).
      var raw = -rect.top / scrollable;
      var progress = Math.max(0, Math.min(0.9999, raw));
      // Each bullet owns 1/N of the scroll distance.
      var activeIdx = Math.floor(progress * bullets.length);
      setStates(activeIdx);
    }

    // Mobile / no-sticky: scroll-track has height:auto via CSS, so
    // scrollable will be <= 0 and everything renders active.
    var ticking = false;
    function onScroll() {
      if (ticking) return;
      ticking = true;
      window.requestAnimationFrame(function () {
        update();
        ticking = false;
      });
    }
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', onScroll, { passive: true });
    update();
  }

  // ── 2. Contact form ──
  var form = document.querySelector('form[data-contact]');
  if (!form) return;

  var submitBtn = form.querySelector('button[type="submit"]');
  var submitOriginalLabel = submitBtn ? submitBtn.innerHTML : '';

  function setSubmitting(on) {
    if (!submitBtn) return;
    if (on) {
      submitBtn.disabled = true;
      submitBtn.textContent = 'Sending…';
      form.classList.add('is-submitting');
    } else {
      submitBtn.disabled = false;
      submitBtn.innerHTML = submitOriginalLabel;
      form.classList.remove('is-submitting');
    }
  }

  function collectFields() {
    var data = {};
    for (var i = 0; i < form.elements.length; i++) {
      var el = form.elements[i];
      if (!el.name) continue;
      data[el.name] = el.value;
    }
    return data;
  }

  form.addEventListener('submit', function (ev) {
    ev.preventDefault();

    // Honeypot, silently flip into success state, do NOT send.
    var hp = form.querySelector('input[name="company_website"]');
    if (hp && hp.value) {
      form.classList.add('is-sent');
      form.classList.remove('is-error');
      return;
    }

    form.classList.remove('is-error');
    setSubmitting(true);

    var payload = collectFields();
    delete payload['form-name']; // legacy field; harmless if missing

    fetch('/api/contact', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(payload),
    })
      .then(function (res) {
        return res.json().catch(function () { return {}; }).then(function (json) {
          return { status: res.status, ok: res.ok, json: json };
        });
      })
      .then(function (out) {
        if (out.ok && out.json && out.json.ok) {
          form.classList.add('is-sent');
        } else {
          form.classList.add('is-error');
          setSubmitting(false);
        }
      })
      .catch(function () {
        form.classList.add('is-error');
        setSubmitting(false);
      });
  });
})();
