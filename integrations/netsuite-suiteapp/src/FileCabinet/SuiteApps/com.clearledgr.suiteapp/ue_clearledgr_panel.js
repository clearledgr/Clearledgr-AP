/**
 * Clearledgr — User Event Script for Vendor Bill record.
 *
 * Renders a "Clearledgr" subtab on the Vendor Bill page when the user is
 * viewing or editing an existing bill. The subtab hosts an iframe that
 * loads our Suitelet, which serves the panel HTML/JS/CSS and mints a
 * short-lived JWT for the panel to call api.clearledgr.com.
 *
 * Skipped on `create` (no record id yet) and on `xedit` (inline edit
 * loads partial records — the iframe would render with stale ids).
 *
 * @NApiVersion 2.1
 * @NScriptType UserEventScript
 */
define(['N/url', 'N/runtime'], (urlMod, runtime) => {

    const SUITELET_SCRIPT_ID = 'customscript_cl_sl_panel';
    const SUITELET_DEPLOY_ID = 'customdeploy_cl_sl_panel';

    function beforeLoad(context) {
        if (context.type !== context.UserEventType.VIEW && context.type !== context.UserEventType.EDIT) {
            return;
        }
        const billId = context.newRecord.id;
        if (!billId) {
            return;
        }
        const accountId = runtime.accountId;

        const form = context.form;
        if (!form || typeof form.addTab !== 'function') {
            return;
        }
        form.addTab({
            id: 'custpage_clearledgr_tab',
            label: 'Clearledgr',
        });

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

    return { beforeLoad: beforeLoad };
});
