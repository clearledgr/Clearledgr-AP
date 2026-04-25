/* Clearledgr — Fiori extension component.
 *
 * Boots a single-view SAPUI5 application that renders the Clearledgr
 * Box (state, timeline, exceptions, outcome) for a supplier invoice
 * the user is looking at in S/4HANA. The invoice's composite key
 * (CompanyCode + SupplierInvoice + FiscalYear) arrives via URL params
 * — either from a side-by-side launch (a button on the Manage
 * Supplier Invoices Fiori app) or via a Fiori Launchpad cross-
 * navigation intent (configured in manifest.json under
 * sap.app.crossNavigation).
 *
 * On boot, the controller hits POST /clearledgr-api/extension/sap/exchange
 * to swap the BTP-issued XSUAA JWT for a 5-minute Clearledgr access
 * token, then GET /clearledgr-api/extension/ap-items/by-sap-invoice
 * with the composite key to fetch the Box payload.
 */
sap.ui.define([
    "sap/ui/core/UIComponent",
    "sap/ui/Device",
    "sap/ui/model/json/JSONModel"
], function (UIComponent, Device, JSONModel) {
    "use strict";

    return UIComponent.extend("com.clearledgr.s4hana.boxpanel.Component", {

        metadata: {
            manifest: "json"
        },

        init: function () {
            UIComponent.prototype.init.apply(this, arguments);

            // Initialize an empty Box JSON model — the controller fills it
            // once the API call returns.
            const oBoxModel = new JSONModel({
                state: "",
                summary: {},
                timeline: [],
                exceptions: [],
                outcome: null,
                composite_key: "",
                ap_item_id: "",
                _loading: true,
                _error: null,
                _empty: false
            });
            this.setModel(oBoxModel, "box");

            // Session model — holds the Clearledgr access token after the
            // XSUAA exchange. Kept in memory (no localStorage) so a stale
            // tab doesn't carry credentials past the next page load.
            const oSessionModel = new JSONModel({
                clearledgr_token: "",
                clearledgr_expires_at: 0,
                user_email: "",
                organization_id: ""
            });
            this.setModel(oSessionModel, "session");

            // Device model — drives compact/cozy density choice.
            const oDeviceModel = new JSONModel(Device);
            oDeviceModel.setDefaultBindingMode("OneWay");
            this.setModel(oDeviceModel, "device");
        },

        /**
         * Read the supplier-invoice composite key from the URL. Two paths:
         *   - Standalone Fiori app: ?CompanyCode=...&SupplierInvoice=...&FiscalYear=...
         *   - Launchpad cross-nav: same param names, hash-encoded
         */
        getInvoiceContextFromUrl: function () {
            const params = new URLSearchParams(window.location.search);
            const hashParams = window.location.hash
                ? new URLSearchParams(window.location.hash.replace(/^#/, "").replace(/^.*\?/, ""))
                : new URLSearchParams("");
            const get = (key) => params.get(key) || hashParams.get(key) || "";
            return {
                companyCode: get("CompanyCode") || get("company_code"),
                supplierInvoice: get("SupplierInvoice") || get("supplier_invoice"),
                fiscalYear: get("FiscalYear") || get("fiscal_year")
            };
        }

    });
});
