/**
 * Clearledgr — User Event Script for Vendor Bill record.
 *
 * Two responsibilities:
 *
 * 1. **Read direction (`beforeLoad`)** — renders a "Clearledgr" subtab
 *    on the Vendor Bill page when the user is viewing or editing an
 *    existing bill. The subtab hosts an iframe that loads our Suitelet,
 *    which serves the panel HTML/JS/CSS and (Phase 3) mints a short-
 *    lived JWT for the panel to call api.clearledgr.com.
 *    Skipped on `create` (no record id yet) and on `xedit` (inline
 *    edit loads partial records — the iframe would render with stale ids).
 *
 * 2. **Write direction (`afterSubmit`)** — fires an HMAC-signed webhook
 *    to api.clearledgr.com/erp/webhooks/netsuite/<orgId> on every
 *    vendor-bill insert/update/delete. This is what makes ERP-arrived
 *    bills (EDI, vendor portal, AP-clerk-typed) visible to Clearledgr
 *    without going through Gmail. The same `bundle_secret` provisioned
 *    in `customrecord_cl_settings` signs both the panel JWT and these
 *    outbound payloads.
 *
 * @NApiVersion 2.1
 * @NScriptType UserEventScript
 */
define(['N/url', 'N/runtime', 'N/search', 'N/https', 'N/crypto', 'N/encode', 'N/log'],
(urlMod, runtime, searchMod, httpsMod, cryptoMod, encodeMod, log) => {

    const SUITELET_SCRIPT_ID = 'customscript_cl_sl_panel';
    const SUITELET_DEPLOY_ID = 'customdeploy_cl_sl_panel';
    const SETTINGS_RECORD_TYPE = 'customrecord_cl_settings';

    /* ───────────── tenant config ───────────── */

    /** @return {{apiBase:string,bundleSecret:string,orgId:string}|null} */
    function loadSettings() {
        try {
            const result = searchMod.create({
                type: SETTINGS_RECORD_TYPE,
                filters: [['isinactive', 'is', 'F']],
                columns: ['custrecord_cl_api_base', 'custrecord_cl_bundle_secret', 'custrecord_cl_org_id'],
            }).run().getRange({ start: 0, end: 1 });
            if (!result || !result.length) return null;
            const row = result[0];
            return {
                apiBase: String(row.getValue({ name: 'custrecord_cl_api_base' }) || '').trim(),
                bundleSecret: String(row.getValue({ name: 'custrecord_cl_bundle_secret' }) || '').trim(),
                orgId: String(row.getValue({ name: 'custrecord_cl_org_id' }) || '').trim(),
            };
        } catch (err) {
            log.error({ title: 'Clearledgr settings load failed', details: String(err) });
            return null;
        }
    }

    /* ───────────── beforeLoad — panel iframe ───────────── */

    function beforeLoad(context) {
        if (context.type !== context.UserEventType.VIEW && context.type !== context.UserEventType.EDIT) {
            return;
        }
        const billId = context.newRecord.id;
        if (!billId) return;
        const accountId = runtime.accountId;

        const form = context.form;
        if (!form || typeof form.addTab !== 'function') return;
        form.addTab({ id: 'custpage_clearledgr_tab', label: 'Clearledgr' });

        const suiteletUrl = urlMod.resolveScript({
            scriptId: SUITELET_SCRIPT_ID,
            deploymentId: SUITELET_DEPLOY_ID,
            params: { billId: billId, accountId: accountId },
            returnExternalUrl: false,
        });
        const iframeHtml =
            '<iframe ' +
            'src="' + suiteletUrl + '" ' +
            'style="width:100%;height:560px;border:0;background:transparent" ' +
            'sandbox="allow-scripts allow-same-origin allow-forms allow-popups">' +
            '</iframe>';

        const panelField = form.addField({
            id: 'custpage_clearledgr_panel',
            type: 'INLINEHTML',
            label: ' ',
            container: 'custpage_clearledgr_tab',
        });
        panelField.defaultValue = iframeHtml;
    }

    /* ───────────── afterSubmit — outbound webhook ───────────── */

    function summarizeBillRecord(rec) {
        if (!rec) return {};
        const safeGet = (field) => {
            try { return rec.getValue({ fieldId: field }); } catch (_) { return null; }
        };
        const safeText = (field) => {
            try { return rec.getText({ fieldId: field }); } catch (_) { return null; }
        };
        return {
            ns_internal_id: rec.id ? String(rec.id) : null,
            transaction_number: safeGet('transactionnumber'),
            tran_id: safeGet('tranid'),
            external_id: safeGet('externalid'),
            entity_id: safeGet('entity'),
            entity_name: safeText('entity'),
            subsidiary_id: safeGet('subsidiary'),
            subsidiary_name: safeText('subsidiary'),
            amount: safeGet('total'),
            currency: safeText('currency') || safeGet('currency'),
            invoice_number: safeGet('billnumber') || safeGet('tranid'),
            tran_date: safeGet('trandate'),
            due_date: safeGet('duedate'),
            memo: safeGet('memo'),
            status: safeGet('status'),
            status_label: safeText('status'),
            posting_period_id: safeGet('postingperiod'),
            payment_hold: safeGet('paymenthold'),
            approval_status: safeGet('approvalstatus'),
        };
    }

    function hexHmacSha256(secret, message) {
        const hmac = cryptoMod.createHmac({
            algorithm: cryptoMod.HashAlg.SHA256,
            key: cryptoMod.createSecretKey({ guid: secret, encoding: encodeMod.Encoding.UTF_8 }),
        });
        hmac.update({ input: message, inputEncoding: encodeMod.Encoding.UTF_8 });
        return hmac.digest({ outputEncoding: encodeMod.Encoding.HEX });
    }

    function fallbackHexHmacSha256(secret, message) {
        // Some sandboxes restrict crypto.createSecretKey to GUID-only secrets;
        // the fallback uses an N/encode round-trip via raw bytes.
        try {
            return hexHmacSha256(secret, message);
        } catch (_) {
            // Cheap fallback: rely on N/crypto's `createHash` for raw HMAC by
            // concatenating secret and message — only for sandboxes; production
            // deployments should use the GUID-secret path. If both fail, the
            // outbound HTTP throws and the script simply skips this event.
            const hash = cryptoMod.createHash({ algorithm: cryptoMod.HashAlg.SHA256 });
            hash.update({ input: secret + ':' + message, inputEncoding: encodeMod.Encoding.UTF_8 });
            return hash.digest({ outputEncoding: encodeMod.Encoding.HEX });
        }
    }

    function eventTypeFor(contextType, types) {
        if (contextType === types.CREATE) return 'vendorbill.create';
        if (contextType === types.EDIT) return 'vendorbill.update';
        if (contextType === types.DELETE) return 'vendorbill.delete';
        if (contextType === types.PAID) return 'vendorbill.paid';
        return 'vendorbill.' + String(contextType || 'unknown').toLowerCase();
    }

    function afterSubmit(context) {
        const types = context.UserEventType;
        // Phase 1 of ERP-native intake: track create + update + paid.
        // Inline-edit (xedit) is partial and noisy; skip.
        if (![types.CREATE, types.EDIT, types.DELETE, types.PAID].includes(context.type)) {
            return;
        }
        const settings = loadSettings();
        if (!settings || !settings.apiBase || !settings.bundleSecret || !settings.orgId) {
            log.audit({
                title: 'Clearledgr webhook skipped',
                details: 'customrecord_cl_settings is missing api_base / bundle_secret / org_id; not firing webhook.',
            });
            return;
        }

        const eventType = eventTypeFor(context.type, types);
        const accountId = runtime.accountId;
        const summary = summarizeBillRecord(context.newRecord);
        const previousSummary = context.type === types.EDIT && context.oldRecord ? summarizeBillRecord(context.oldRecord) : null;

        const payload = {
            event_type: eventType,
            event_id: 'ns:' + accountId + ':' + summary.ns_internal_id + ':' + Date.now(),
            account_id: accountId,
            occurred_at: new Date().toISOString(),
            bill: summary,
            previous: previousSummary,
        };
        const body = JSON.stringify(payload);
        const ts = String(Math.floor(Date.now() / 1000));
        const signed = ts + '.' + body;
        let signature;
        try {
            signature = fallbackHexHmacSha256(settings.bundleSecret, signed);
        } catch (err) {
            log.error({ title: 'Clearledgr HMAC sign failed', details: String(err) });
            return;
        }

        const url = settings.apiBase.replace(/\/$/, '') + '/erp/webhooks/netsuite/' + encodeURIComponent(settings.orgId);
        try {
            const resp = httpsMod.post({
                url: url,
                body: body,
                headers: {
                    'Content-Type': 'application/json',
                    'X-NetSuite-Signature': 'v1=' + signature,
                    'X-NetSuite-Timestamp': ts,
                    'X-Clearledgr-Event': eventType,
                },
            });
            if (resp.code >= 400) {
                log.audit({
                    title: 'Clearledgr webhook non-2xx',
                    details: 'event=' + eventType + ' code=' + resp.code + ' body=' + String(resp.body || '').slice(0, 500),
                });
            }
        } catch (err) {
            // Don't let webhook failures roll back the user's NetSuite save.
            log.error({ title: 'Clearledgr webhook POST failed', details: String(err) });
        }
    }

    return { beforeLoad: beforeLoad, afterSubmit: afterSubmit };
});
