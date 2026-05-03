# Solden SuiteApp — Marketplace Conversion + Submission

This document covers the path from the current Account Customization
Project (ACP) shape to a SuiteApp Marketplace listing.

| Step | What | Owner | Time |
|---|---|---|---|
| **1** | NetSuite SDN (Solution Developer Network) membership | Solden ops | 4–6 weeks |
| **2** | Reserve bundle ID + Application ID via NetSuite Partner Portal | Solden ops | 1–2 days after SDN approval |
| **3** | Convert ACP project → SuiteApp project (manifest + Objects rewrite) | Solden eng | 1 day |
| **4** | Build BFN submission package + listing copy | Solden eng + ops | 1 week |
| **5** | Submit for BFN review | Solden ops | (review takes 6–12 weeks — see [BFN_CERTIFICATION.md](BFN_CERTIFICATION.md)) |
| **6** | Listing live on SuiteApp Marketplace | Oracle | — |

Total realistic timeline: **3–5 months** from "decide to ship" to
"listing live." Steps 1 + 5 are the long poles and run mostly in
parallel.

---

## Step 1 — NetSuite SDN membership

The Solution Developer Network is Oracle's partner program for
ISVs publishing SuiteApps. Annual paid membership.

1. Go to <https://www.netsuite.com/portal/partners/sdn-application-form.shtml>.
2. Submit the application:
   - Legal entity: Solden's incorporated name.
   - Solution overview: paste the long-description from
     [BFN_CERTIFICATION.md §A.3](BFN_CERTIFICATION.md#a3-listing-copy).
   - Target customer: mid-market + enterprise finance teams using
     NetSuite for AP.
   - Distribution model: SaaS (Solden hosts the API; the SuiteApp
     is the install-once client).
3. Pay the SDN membership fee (typical $1,000–$5,000/yr depending on
   tier; verify current pricing at
   <https://www.netsuite.com/portal/partners.shtml>).
4. Sign the SDN partner agreement (SDN PA).
5. Wait 4–6 weeks for Oracle to review + approve.

Once approved, Solden gets:
- Access to the NetSuite Partner Portal
   (<https://partners.netsuite.com>).
- A SuiteApp Builder account (separate from Solden's normal
   NetSuite accounts).
- Bundle ID + Application ID reservation tooling.

## Step 2 — Reserve bundle ID + Application ID

In the NetSuite Partner Portal:

1. **SuiteApp Builder → Reserved Bundle IDs → Reserve New**.
   - Bundle name: `Solden`
   - Bundle internal ID: auto-generated (numeric, e.g. `567890`).
   - Note this ID — it goes into the SuiteApp project's
     `manifest.xml` as the bundle reference.
2. **Reserved Application IDs → Reserve New**.
   - Application name: `Solden Coordination Layer`
   - Application internal ID: auto-generated.
   - This becomes part of the manifest's `applicationid` element
     (used for licensing + telemetry).
3. Confirm both IDs are tied to the SDN partner account.

## Step 3 — Convert ACP → SuiteApp project

The current `manifest.xml` declares
`projecttype="ACCOUNTCUSTOMIZATIONPROJECT"`. SuiteApps require
`projecttype="SUITEAPPPROJECT"` plus a few additional manifest
elements.

Edit `integrations/netsuite-suiteapp/src/manifest.xml`:

```xml
<manifest projecttype="SUITEAPPPROJECT">
    <projectname>Solden</projectname>
    <publisherid>com.solden</publisherid>
    <projectid>solden_suiteapp</projectid>
    <projectversion>1.0.0</projectversion>
    <applicationid><!-- Application ID from Step 2 --></applicationid>
    <frameworkversion>1.0</frameworkversion>
    <dependencies>
        <features>
            <feature required="true">SERVERSIDESCRIPTING</feature>
            <feature required="true">CLIENTSIDESCRIPTING</feature>
            <feature required="true">CUSTOMRECORDS</feature>
        </features>
    </dependencies>
</manifest>
```

Then update the directory structure to the SuiteApp convention:

```
integrations/netsuite-suiteapp/src/
├── manifest.xml                                    # ← updated above
├── deploy.xml
├── AccountConfiguration/
└── FileCabinet/
    └── SuiteApps/
        └── com.solden.solden_suiteapp/             # ← was com.clearledgr.suiteapp
            ├── ue_clearledgr_panel.js              # rename to ue_solden_panel.js
            ├── sl_clearledgr_panel.js              # rename to sl_solden_panel.js
            └── ui/
                ├── panel.html
                ├── panel.js
                └── panel.css
```

Note: bundle directory name follows `<publisherid>.<projectid>` —
this is enforced by SDF.

Update each script's internal references:
- `customscript_cl_sl_panel` → `customscript_solden_sl_panel`
- `customscript_cl_ue_panel` → `customscript_solden_ue_panel`
- `customrecord_cl_settings` → `customrecord_solden_settings`
- Custom record fields stay on the `custrecord_cl_*` shape for
  back-compat with already-installed tenants. New tenants get the
  `custrecord_solden_*` shape.

Test locally:

```bash
cd integrations/netsuite-suiteapp/
suitecloud project:validate
suitecloud project:deploy --dryrun
```

Both commands should succeed with no warnings before submission.

## Step 4 — BFN submission package

See [BFN_CERTIFICATION.md](BFN_CERTIFICATION.md) for the complete
checklist. Summary:

- [ ] SDF project converted to SuiteApp (Step 3).
- [ ] 5–8 screenshots captured (BFN §A.2).
- [ ] Listing copy drafted: tagline, short description, long
       description (BFN §A.3).
- [ ] Demo video uploaded + URL ready (BFN §A.4 — optional but
       improves discovery).
- [ ] Test scenarios B1–C3 verified passing in sandbox (BFN §B).
- [ ] Security questionnaire pre-filled (BFN §C).
- [ ] Performance benchmarks measured + documented (BFN §D).
- [ ] Customer reference (Cowrywise) briefed (BFN §E).

## Step 5 — Submit for BFN review

In NetSuite Partner Portal:

1. **SuiteApp Builder → New Submission**.
2. Upload the SDF project zip:
   ```bash
   cd integrations/netsuite-suiteapp/
   zip -r solden-suiteapp-1.0.0.zip src/ -x "*.DS_Store"
   ```
3. Fill in the submission form:
   - **Categories**: Accounting / Finance / Workflow Automation.
   - **Pricing**: "Talk to sales for enterprise pricing" — link to
     soldenai.com/pricing or equivalent. (CLAUDE.md rule: no
     fabricated pricing on marketing surfaces.)
   - **Listing copy**: paste from BFN §A.3.
   - **Screenshots**: upload from BFN §A.2.
   - **Demo video URL**: from BFN §A.4.
   - **Security questionnaire**: paste pre-filled answers from
     BFN §C.
   - **Test instructions**: provide a sandbox tenant + test
     credentials Oracle's reviewer can drive Solden through.
4. Submit. Track status in **My Submissions**.

Oracle's review:
- Phase B (functional) typically completes within 2 weeks; reviewer
  may request iteration on any of the B1–B10 / W1–W6 scenarios.
- Phase C (security) runs in parallel; expect 2–4 weeks back-and-
  forth on ambiguous questionnaire answers.
- Phase D (performance) is fast (1–2 weeks) if the benchmarks are
  documented.
- Phase E (customer reference) is scheduled by Oracle directly with
  the customer; brief Cowrywise once the call is on the calendar.

Address Oracle's findings in the SDF project. Re-submit. Iterate
until **Approved**.

## Step 6 — Listing live

Once approved, the listing appears on
<https://www.netsuite.com/portal/partners/suiteapp/find-an-app.shtml>
and within NetSuite's in-product SuiteApp directory.

Post-launch:

1. Update `integrations/netsuite-suiteapp/README.md` to remove the
   "Phase 4: SuiteApp marketplace listing — not started" line.
2. Update Solden's marketing site (soldenai.com) with the
   "Available on SuiteApp Marketplace" badge.
3. Brief the Solden support team on the install flow that customers
   see (it's slightly different from sideloading: customers click
   "Install" from the Marketplace, then provision the
   `customrecord_solden_settings` row, rather than uploading a zip).

---

## Per-tenant install (post-listing)

Once the SuiteApp is on the Marketplace, customers install via:

1. Open NetSuite → **Customization → SuiteBundler → Search & Install
   Bundles**.
2. Search for "Solden". Click **Install**.
3. After install, open **Lists → Custom → Solden Settings → New**.
   - `custrecord_solden_api_base`: `https://api.solden.com`
   - `custrecord_solden_bundle_secret`: paste from Solden's admin
     console (one-time generated per tenant).
   - `custrecord_solden_org_id`: paste the tenant's Solden org ID.
4. Solden's CSM coordinates the secret provisioning over a screen-
   share for the first install per customer.

The customer-facing one-pager covering this flow lives at
<https://soldenai.com/install/netsuite> (post-listing).

---

## Pre-launch checklist for Solden ops

Before submitting the SDN application:

- [ ] Solden has a publicly available privacy policy
       (`https://soldenai.com/privacy`).
- [ ] Solden has a publicly available terms of use
       (`https://soldenai.com/terms`).
- [ ] Solden has a `security@soldenai.com` mailbox monitored 24/7.
- [ ] Solden has a public security disclosure page (or trust center)
       documenting encryption, auth, audit, incident response.
- [ ] Solden has a published SLA for the Solden API the SuiteApp
       depends on (uptime + latency targets).
- [ ] Cowrywise (or another deployed customer) is willing to act as
       the BFN customer reference.

If any checkbox is unticked, address before submitting — Oracle
flags missing trust-center artefacts as a blocker.
