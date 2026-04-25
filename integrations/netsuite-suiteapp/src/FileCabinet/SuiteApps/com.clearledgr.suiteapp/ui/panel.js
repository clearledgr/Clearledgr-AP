/* Clearledgr panel — boots inside the Suitelet-served iframe.
   Reads bill id + account id + API base + auth token from <meta> tags
   in <head>, calls Clearledgr's by-netsuite-bill endpoint, and renders
   the Box (state, timeline, exceptions) + action buttons.

   Vanilla JS, no build step. Phase 3 will add JWT-bearer auth; Phase 1-2
   uses a static dev token issued by the Suitelet so the same code path
   works once the JWT swap is wired. */
(function () {
    'use strict';

    const meta = (name) => {
        const el = document.querySelector('meta[name="' + name + '"]');
        return el ? el.getAttribute('content') : '';
    };

    const config = {
        billId: meta('cl-bill-id'),
        accountId: meta('cl-account-id'),
        apiBase: meta('cl-api-base') || 'https://api.clearledgr.com',
        token: meta('cl-token') || '',
    };

    const $ = (id) => document.getElementById(id);
    const show = (id) => { const el = $(id); if (el) el.classList.remove('cl-hidden'); };
    const hide = (id) => { const el = $(id); if (el) el.classList.add('cl-hidden'); };

    function setState(stateValue) {
        const badge = $('cl-state-badge');
        if (!badge) return;
        const normalized = String(stateValue || '').toLowerCase().replace(/[^a-z_]/g, '');
        badge.textContent = normalized
            ? normalized.replace(/_/g, ' ')
            : 'unknown';
        badge.className = 'cl-badge cl-badge-state-' + (normalized || 'unknown');
    }

    function setText(id, value) {
        const el = $(id);
        if (el) el.textContent = value == null ? '—' : String(value);
    }

    function fmtMoney(amount, currency) {
        if (amount == null || amount === '') return '—';
        const num = Number(amount);
        if (!isFinite(num)) return String(amount);
        try {
            return new Intl.NumberFormat('en-US', {
                style: 'currency',
                currency: (currency || 'USD').toUpperCase(),
                maximumFractionDigits: 2,
            }).format(num);
        } catch (_) {
            return (currency || '') + ' ' + num.toFixed(2);
        }
    }

    function fmtDate(value) {
        if (!value) return '—';
        const d = new Date(value);
        if (isNaN(d.getTime())) return String(value);
        return d.toISOString().slice(0, 10);
    }

    function fmtDateTime(value) {
        if (!value) return '';
        const d = new Date(value);
        if (isNaN(d.getTime())) return String(value);
        return d.toLocaleString();
    }

    function renderTimeline(events) {
        const list = $('cl-timeline');
        if (!list) return;
        list.innerHTML = '';
        const sorted = (events || []).slice().sort((a, b) => {
            const at = a && a.created_at ? new Date(a.created_at).getTime() : 0;
            const bt = b && b.created_at ? new Date(b.created_at).getTime() : 0;
            return bt - at;  // newest first
        });
        if (!sorted.length) {
            hide('cl-timeline-section');
            return;
        }
        sorted.slice(0, 25).forEach((event) => {
            const li = document.createElement('li');
            const timeEl = document.createElement('span');
            timeEl.className = 'cl-time';
            timeEl.textContent = fmtDateTime(event.created_at);
            const eventEl = document.createElement('span');
            eventEl.className = 'cl-event';
            const verb = String(event.event_type || event.type || 'event').replace(/_/g, ' ');
            const summary = event.summary || event.message || '';
            eventEl.textContent = summary ? verb + ' — ' + summary : verb;
            li.appendChild(timeEl);
            li.appendChild(eventEl);
            list.appendChild(li);
        });
        show('cl-timeline-section');
    }

    function renderExceptions(exceptions) {
        const list = $('cl-exceptions');
        if (!list) return;
        list.innerHTML = '';
        if (!exceptions || !exceptions.length) {
            hide('cl-exceptions-section');
            return;
        }
        exceptions.forEach((exc) => {
            const li = document.createElement('li');
            const code = exc.code || exc.exception_code || 'exception';
            const detail = exc.detail || exc.message || exc.description || '';
            li.textContent = detail ? code + ' — ' + detail : code;
            if (String(exc.severity || '').toLowerCase() === 'warn') {
                li.className = 'cl-warn';
            }
            list.appendChild(li);
        });
        show('cl-exceptions-section');
    }

    function renderActions(state) {
        const actionable = state === 'needs_approval' || state === 'needs_info' || state === 'received' || state === 'validated';
        if (actionable) {
            show('cl-actions');
        } else {
            hide('cl-actions');
        }
    }

    function renderSummary(item) {
        const summary = item || {};
        setText('cl-vendor', summary.vendor_name);
        setText('cl-amount', fmtMoney(summary.amount, summary.currency));
        setText('cl-invoice-number', summary.invoice_number);
        setText('cl-due-date', fmtDate(summary.due_date));
        show('cl-summary');
    }

    function renderDeeplink(apItemId) {
        const link = $('cl-deeplink');
        if (!link) return;
        const base = (config.apiBase || '').replace(/\/api$/, '').replace(/\/$/, '');
        // Deep-link target is the customer-facing app, not the API.
        // We don't know the app domain here — fall back to clearledgr.com/app
        // until Phase 3 surfaces a per-tenant app URL via customrecord_cl_settings.
        link.href = 'https://app.clearledgr.com/ap-items/' + encodeURIComponent(apItemId);
    }

    function renderError(message) {
        $('cl-error-text').textContent = message || 'Something went wrong.';
        show('cl-error');
        hide('cl-summary');
        hide('cl-exceptions-section');
        hide('cl-timeline-section');
        hide('cl-actions');
    }

    function renderEmpty() {
        show('cl-empty');
        hide('cl-summary');
        hide('cl-exceptions-section');
        hide('cl-timeline-section');
        hide('cl-actions');
        const badge = $('cl-state-badge');
        if (badge) {
            badge.className = 'cl-badge cl-badge-loading';
            badge.textContent = 'Not in Clearledgr';
        }
    }

    async function api(path, init) {
        const url = config.apiBase.replace(/\/$/, '') + path;
        const headers = (init && init.headers) || {};
        if (config.token) headers['Authorization'] = 'Bearer ' + config.token;
        headers['Accept'] = 'application/json';
        const res = await fetch(url, Object.assign({}, init, { headers: headers, credentials: 'omit' }));
        if (res.status === 404) {
            const err = new Error('not_found');
            err.code = 'not_found';
            throw err;
        }
        if (!res.ok) {
            let body = '';
            try { body = await res.text(); } catch (_) { /* swallow */ }
            const err = new Error('api_error_' + res.status);
            err.code = 'api_error';
            err.status = res.status;
            err.body = body;
            throw err;
        }
        return res.json();
    }

    async function load() {
        if (!config.billId) {
            renderError('Missing bill id — reload the page or contact support.');
            return;
        }
        try {
            const data = await api(
                '/extension/ap-items/by-netsuite-bill/' + encodeURIComponent(config.billId)
                    + '?account_id=' + encodeURIComponent(config.accountId)
            );
            // Expected shape from new endpoint:
            //   { ap_item_id, box_id, box_type, state, timeline, exceptions, outcome,
            //     summary: { vendor_name, amount, currency, invoice_number, due_date } }
            setState(data.state);
            renderSummary(data.summary || {});
            renderExceptions(data.exceptions || []);
            renderTimeline(data.timeline || []);
            renderActions(data.state);
            renderDeeplink(data.ap_item_id);
            window.__clState = { apItemId: data.ap_item_id, state: data.state };
        } catch (err) {
            if (err.code === 'not_found') {
                renderEmpty();
                return;
            }
            // eslint-disable-next-line no-console
            console.error('[clearledgr] load failed', err);
            renderError('Could not reach Clearledgr (' + (err.status || err.code || 'error') + ').');
        }
    }

    async function dispatchAction(action) {
        const ctx = window.__clState || {};
        if (!ctx.apItemId) return;
        const buttons = document.querySelectorAll('#cl-actions .cl-btn');
        buttons.forEach((b) => { b.setAttribute('disabled', ''); });
        try {
            const path = action === 'approve'
                ? '/extension/route-low-risk-approval'
                : action === 'reject'
                    ? '/extension/reject-invoice'
                    : '/extension/submit-for-approval';
            await api(path, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ap_item_id: ctx.apItemId, source: 'netsuite_panel' }),
            });
            await load();  // refresh the panel state
        } catch (err) {
            // eslint-disable-next-line no-console
            console.error('[clearledgr] action failed', action, err);
            renderError('Action failed (' + (err.status || err.code || 'error') + ').');
        } finally {
            buttons.forEach((b) => { b.removeAttribute('disabled'); });
        }
    }

    document.addEventListener('click', (e) => {
        const target = e.target.closest('[data-action]');
        if (!target) return;
        const action = target.getAttribute('data-action');
        if (action === 'retry') {
            hide('cl-error');
            load();
            return;
        }
        dispatchAction(action);
    });

    load();
})();
