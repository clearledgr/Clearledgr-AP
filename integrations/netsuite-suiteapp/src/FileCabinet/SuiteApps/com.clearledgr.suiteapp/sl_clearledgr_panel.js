/**
 * Clearledgr — Suitelet that serves the panel HTML.
 *
 * Stitches panel.html / panel.js / panel.css from the File Cabinet into
 * a single document, embeds the bill id + account id + minted JWT as
 * <meta> tags, and returns the result as text/html. The User Event
 * Script's iframe `src` resolves to this Suitelet.
 *
 * Phase 1-2 (today): no JWT minted; panel calls api.clearledgr.com with
 *   a static dev token while the Phase 3 auth path is wired up.
 * Phase 3: HMAC-signs a short-lived JWT (5-min exp) using the shared
 *   secret stored in `customrecord_cl_settings`; embeds in <meta name="cl-token">.
 *   See PHASE_NOTES.md for the JWT shape.
 *
 * @NApiVersion 2.1
 * @NScriptType Suitelet
 */
define(['N/file', 'N/runtime', 'N/log'], (fileMod, runtime, log) => {

    const PANEL_FOLDER = 'SuiteApps/com.clearledgr.suiteapp/ui';
    const FILE_HTML = PANEL_FOLDER + '/panel.html';
    const FILE_JS = PANEL_FOLDER + '/panel.js';
    const FILE_CSS = PANEL_FOLDER + '/panel.css';

    // Phase 1-2 default; Phase 3 overrides via customrecord_cl_settings.
    const DEFAULT_API_BASE = 'https://api.clearledgr.com';
    const DEV_TOKEN = 'DEMO_PHASE_2';

    function loadFileContents(path) {
        try {
            return fileMod.load({ id: path }).getContents();
        } catch (err) {
            log.error({
                title: 'Clearledgr panel asset missing',
                details: 'File not found at ' + path + ' — did the SDF deploy include the FileCabinet folder?',
            });
            return '';
        }
    }

    function escapeHtml(value) {
        return String(value || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function onRequest(context) {
        const billId = context.request.parameters.billId || '';
        const accountId = context.request.parameters.accountId || runtime.accountId || '';

        const html = loadFileContents(FILE_HTML);
        const js = loadFileContents(FILE_JS);
        const css = loadFileContents(FILE_CSS);

        // Embed runtime config as meta tags. panel.js reads these on boot.
        const meta =
            '<meta name="cl-bill-id" content="' + escapeHtml(billId) + '">\n' +
            '<meta name="cl-account-id" content="' + escapeHtml(accountId) + '">\n' +
            '<meta name="cl-api-base" content="' + escapeHtml(DEFAULT_API_BASE) + '">\n' +
            '<meta name="cl-token" content="' + escapeHtml(DEV_TOKEN) + '">\n';

        // Minimal stitching: replace placeholders inside panel.html.
        const composed = html
            .replace('<!--CL_META-->', meta)
            .replace('<!--CL_CSS-->', '<style>' + css + '</style>')
            .replace('<!--CL_JS-->', '<script>' + js + '</script>');

        context.response.setHeader({ name: 'Content-Type', value: 'text/html; charset=utf-8' });
        // Prevent NetSuite from caching the panel HTML — script changes
        // should reflect on next load without a Suitelet redeploy.
        context.response.setHeader({ name: 'Cache-Control', value: 'no-store' });
        context.response.write({ output: composed });
    }

    return { onRequest: onRequest };
});
