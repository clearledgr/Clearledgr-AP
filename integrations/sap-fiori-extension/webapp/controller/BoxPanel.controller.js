/* Clearledgr — BoxPanel controller.
 *
 * Lifecycle on the supplier-invoice display screen:
 *
 *   1. onInit reads CompanyCode/SupplierInvoice/FiscalYear from the URL
 *      (set either by the cross-navigation intent on the standard Manage
 *      Supplier Invoices Fiori app, or by a direct link from the Fiori
 *      Launchpad tile).
 *   2. _bootstrapSession exchanges the BTP-issued XSUAA JWT — forwarded
 *      to us via Approuter in the Authorization header — for a 5-minute
 *      Clearledgr access token. Approuter strips the original XSUAA
 *      JWT and re-injects its own header, so we look at document.cookie
 *      for the session, fall through to a same-origin POST that the
 *      backend recognizes and signs against XSUAA's JWKS.
 *   3. _loadBox calls /clearledgr-api/extension/ap-items/by-sap-invoice
 *      with the composite key, populates the Box JSON model, and binds
 *      the view.
 *   4. onApprovePress / onRejectPress call back into the same Slack-
 *      backed action endpoints the NetSuite SuiteApp uses
 *      (/extension/route-low-risk-approval and /extension/reject-invoice
 *      via the existing /extension/* surface). The Approuter prefix
 *      `/clearledgr-api/` resolves to api.clearledgr.com via the BTP
 *      Destination configured in xs-app.json.
 */
sap.ui.define([
    "sap/ui/core/mvc/Controller",
    "sap/m/MessageToast",
    "sap/m/MessageBox"
], function (Controller, MessageToast, MessageBox) {
    "use strict";

    return Controller.extend("com.clearledgr.s4hana.boxpanel.controller.BoxPanel", {

        onInit: function () {
            const oCtx = this.getOwnerComponent().getInvoiceContextFromUrl();
            this._compositeKey = {
                CompanyCode: oCtx.companyCode || "",
                SupplierInvoice: oCtx.supplierInvoice || "",
                FiscalYear: oCtx.fiscalYear || ""
            };
            if (!this._compositeKey.CompanyCode || !this._compositeKey.SupplierInvoice || !this._compositeKey.FiscalYear) {
                this._showError("Missing supplier-invoice context (CompanyCode / SupplierInvoice / FiscalYear).");
                return;
            }
            this._bootstrapSession()
                .then(this._loadBox.bind(this))
                .catch(this._handleBootstrapFailure.bind(this));
        },

        /* ─── Session bootstrap ─────────────────────────────────────── */

        _bootstrapSession: async function () {
            // Approuter forwards the XSUAA JWT to the backend in the
            // Authorization header. Our exchange endpoint expects the JWT
            // in the body so it can verify against JWKS server-side
            // (Approuter doesn't proxy raw tokens to non-managed
            // destinations). We post via a same-origin call that
            // Approuter rewrites onto the clearledgr-api destination.
            const oXsuaaToken = await this._fetchXsuaaToken();
            if (!oXsuaaToken) {
                throw new Error("xsuaa_token_unavailable");
            }
            const sUrl = "/clearledgr-api/extension/sap/exchange";
            const oResponse = await fetch(sUrl, {
                method: "POST",
                headers: { "Content-Type": "application/json", "Accept": "application/json" },
                body: JSON.stringify({ xsuaa_jwt: oXsuaaToken }),
                credentials: "include"
            });
            if (!oResponse.ok) {
                const sBody = await oResponse.text().catch(() => "");
                throw new Error("exchange_failed_" + oResponse.status + ": " + sBody.slice(0, 200));
            }
            const oTokenData = await oResponse.json();
            const oSessionModel = this.getOwnerComponent().getModel("session");
            oSessionModel.setProperty("/clearledgr_token", oTokenData.access_token);
            oSessionModel.setProperty("/clearledgr_expires_at", Date.now() + (oTokenData.expires_in * 1000) - 30000);
            return oTokenData.access_token;
        },

        /**
         * Surface the XSUAA JWT to JS. In a BTP-deployed app, Approuter
         * exposes the user's identity via /user-api/currentUser and
         * /user-api/attributes — the JWT itself is exposed via the
         * "Authorization" header on backend-bound requests but NOT to the
         * client JS by default. Two paths to make this work:
         *
         *   1. Approuter fetches /user-api/attributes which includes
         *      "id_token" when the IdP issued one. We read that.
         *   2. Fallback: a small managed proxy route on the backend
         *      (`/clearledgr-api/extension/sap/whoami`) that simply
         *      reflects the Authorization header back to the client.
         *      This is safe because Approuter only proxies to it after
         *      authenticating the user, and the response body never
         *      leaves the user's own browser.
         *
         * For Phase 1-3 (BTP trial) we use path 1 — `/user-api/attributes`.
         */
        _fetchXsuaaToken: async function () {
            try {
                const oResponse = await fetch("/user-api/attributes", { credentials: "include" });
                if (!oResponse.ok) {
                    return "";
                }
                const oAttrs = await oResponse.json();
                // The attribute name varies by Approuter version; prefer
                // id_token, fall back to the legacy `token` key.
                return String(
                    oAttrs.id_token
                    || oAttrs.idToken
                    || oAttrs.access_token
                    || oAttrs.token
                    || ""
                ).trim();
            } catch (e) {
                return "";
            }
        },

        _handleBootstrapFailure: function (err) {
            // eslint-disable-next-line no-console
            console.error("[clearledgr] bootstrap failed", err);
            this._showError("Could not authenticate with Clearledgr (" + (err.message || err) + ").");
        },

        /* ─── Box load ──────────────────────────────────────────────── */

        _loadBox: async function () {
            const oBoxModel = this.getOwnerComponent().getModel("box");
            oBoxModel.setProperty("/_loading", true);
            oBoxModel.setProperty("/_error", null);
            oBoxModel.setProperty("/_empty", false);

            const oSessionModel = this.getOwnerComponent().getModel("session");
            const sToken = oSessionModel.getProperty("/clearledgr_token");
            const sUrl = "/clearledgr-api/extension/ap-items/by-sap-invoice"
                + "?company_code=" + encodeURIComponent(this._compositeKey.CompanyCode)
                + "&supplier_invoice=" + encodeURIComponent(this._compositeKey.SupplierInvoice)
                + "&fiscal_year=" + encodeURIComponent(this._compositeKey.FiscalYear);

            try {
                const oResponse = await fetch(sUrl, {
                    headers: {
                        "Authorization": "Bearer " + sToken,
                        "Accept": "application/json"
                    },
                    credentials: "include"
                });
                if (oResponse.status === 404) {
                    oBoxModel.setProperty("/_loading", false);
                    oBoxModel.setProperty("/_empty", true);
                    return;
                }
                if (!oResponse.ok) {
                    const sBody = await oResponse.text().catch(() => "");
                    throw new Error("load_failed_" + oResponse.status + ": " + sBody.slice(0, 200));
                }
                const oData = await oResponse.json();
                this._populateBoxModel(oData);
            } catch (err) {
                // eslint-disable-next-line no-console
                console.error("[clearledgr] load box failed", err);
                this._showError("Could not load Clearledgr Box (" + (err.message || err) + ").");
            } finally {
                oBoxModel.setProperty("/_loading", false);
            }
        },

        _populateBoxModel: function (oData) {
            const oBoxModel = this.getOwnerComponent().getModel("box");
            const oSummary = oData.summary || {};
            const fAmount = parseFloat(oSummary.amount);
            const sCurrency = String(oSummary.currency || "USD").toUpperCase();
            let sFormatted = "—";
            if (!isNaN(fAmount)) {
                try {
                    sFormatted = new Intl.NumberFormat(sap.ui.getCore().getConfiguration().getLanguage(), {
                        style: "currency",
                        currency: sCurrency
                    }).format(fAmount);
                } catch (e) {
                    sFormatted = sCurrency + " " + fAmount.toFixed(2);
                }
            }
            oSummary._amountFormatted = sFormatted;

            oBoxModel.setData({
                state: oData.state || "",
                summary: oSummary,
                timeline: oData.timeline || [],
                exceptions: oData.exceptions || [],
                outcome: oData.outcome || null,
                composite_key: oData.composite_key || "",
                ap_item_id: oData.ap_item_id || "",
                _loading: false,
                _error: null,
                _empty: false
            });
        },

        /* ─── Actions ───────────────────────────────────────────────── */

        onApprovePress: function () {
            this._dispatchAction("approve", "/extension/route-low-risk-approval", "Approving …");
        },

        onRejectPress: function () {
            const that = this;
            MessageBox.warning(
                "This will cancel the supplier invoice in S/4HANA. Continue?",
                {
                    title: "Reject & cancel",
                    actions: [MessageBox.Action.OK, MessageBox.Action.CANCEL],
                    emphasizedAction: MessageBox.Action.OK,
                    onClose: function (sAction) {
                        if (sAction === MessageBox.Action.OK) {
                            that._dispatchAction("reject", "/extension/reject-invoice", "Rejecting …");
                        }
                    }
                }
            );
        },

        _dispatchAction: async function (sAction, sPath, sBusyText) {
            const oBoxModel = this.getOwnerComponent().getModel("box");
            const oSessionModel = this.getOwnerComponent().getModel("session");
            const sApItemId = oBoxModel.getProperty("/ap_item_id");
            if (!sApItemId) {
                this._showError("No AP item linked to this invoice.");
                return;
            }
            MessageToast.show(sBusyText);
            const sToken = oSessionModel.getProperty("/clearledgr_token");
            try {
                const oResponse = await fetch("/clearledgr-api" + sPath, {
                    method: "POST",
                    headers: {
                        "Authorization": "Bearer " + sToken,
                        "Content-Type": "application/json",
                        "Accept": "application/json"
                    },
                    body: JSON.stringify({ ap_item_id: sApItemId, source: "sap_fiori_panel" }),
                    credentials: "include"
                });
                if (!oResponse.ok) {
                    const sBody = await oResponse.text().catch(() => "");
                    throw new Error(sAction + "_failed_" + oResponse.status + ": " + sBody.slice(0, 200));
                }
                MessageToast.show(sAction === "approve" ? "Approved." : "Rejected.");
                // Re-fetch the Box to reflect the new state.
                await this._loadBox();
            } catch (err) {
                // eslint-disable-next-line no-console
                console.error("[clearledgr] " + sAction + " failed", err);
                this._showError(sAction + " failed: " + (err.message || err));
            }
        },

        onOpenInClearledgrPress: function () {
            const sApItemId = this.getOwnerComponent().getModel("box").getProperty("/ap_item_id");
            if (!sApItemId) return;
            window.open("https://app.clearledgr.com/ap-items/" + encodeURIComponent(sApItemId), "_blank", "noopener");
        },

        /* ─── Helpers ───────────────────────────────────────────────── */

        _showError: function (sMessage) {
            const oBoxModel = this.getOwnerComponent().getModel("box");
            oBoxModel.setProperty("/_loading", false);
            oBoxModel.setProperty("/_error", sMessage);
        }

    });
});
