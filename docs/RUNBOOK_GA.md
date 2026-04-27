# GA Deployment Runbook

External-account steps Mo needs to take before GA. Each section is
self-contained and idempotent — re-running is safe.

---

## 1. Microsoft OAuth (workspace login)

**Why:** SAP-shop and Microsoft 365 enterprise prospects can't sign
in via Google. Microsoft OAuth lets them use their work Microsoft
account.

**Steps:**

1. Open https://portal.azure.com → Azure Active Directory → App
   registrations → **New registration**.
2. Fill in:
   - **Name:** Clearledgr workspace
   - **Supported account types:** "Accounts in any organizational
     directory (Any Azure AD directory — Multitenant)"
   - **Redirect URI (Web):** `https://api.clearledgr.com/auth/microsoft/callback`
3. After registration, copy the **Application (client) ID** from the
   Overview page.
4. **Certificates & secrets** → **New client secret** → 24 month
   expiry → copy the secret **Value** (not the ID).
5. **API permissions** → **Add a permission** → Microsoft Graph →
   **Delegated permissions** → check `openid`, `email`, `profile`,
   `User.Read`. Click **Add**. Then **Grant admin consent for the
   tenant** if you control the tenant.
6. Set on the api Railway service:
   ```
   railway variable set "MICROSOFT_CLIENT_ID=<application_id>" --service api
   railway variable set "MICROSOFT_CLIENT_SECRET=<secret_value>" --service api
   ```
7. Wait for the api to redeploy (~2 min). The "Continue with
   Microsoft" button on the SPA login page is already wired and will
   start working as soon as the env vars are live.

**Verify:**
- Click **Continue with Microsoft** in incognito at
  https://workspace.clearledgr.com/login
- Pick a Microsoft account, complete consent
- You should land on the workspace home page

If you see `503 microsoft_oauth_not_configured`: env vars haven't
propagated yet, retry after 30s.

---

## 2. Microsoft Teams adapter (FEATURE_TEAMS_ENABLED)

**Why:** ~80% of SAP-shop enterprise customers use Teams, not Slack.
Without Teams approvals, half the outbound funnel can't approve
invoices.

**Code is ready.** What's missing is the Bot Framework registration
in Azure + the env vars + the Teams app manifest packaged.

**Steps:**

1. **Register a Bot Framework application:**
   - https://dev.botframework.com/bots/new (or Azure Portal → Create
     a resource → Azure Bot)
   - **Bot handle:** `clearledgr` (or any unique handle)
   - **Microsoft App ID:** Auto-generate, OR reuse the App
     registration from Step 1 above (recommended — single Azure
     identity for both auth + bot)
   - **Messaging endpoint:** `https://api.clearledgr.com/teams/messages`
2. Copy the **Microsoft App ID** and **App Secret** (or generate a
   new client secret on the App registration if reusing).
3. Set on the api / worker / beat services:
   ```
   railway variable set "TEAMS_APP_ID=<microsoft_app_id>" --service api
   railway variable set "TEAMS_APP_SECRET=<microsoft_app_secret>" --service api
   railway variable set "FEATURE_TEAMS_ENABLED=true" --service api
   ```
4. **Build the Teams app manifest** (zip with manifest.json + icons):
   - `manifest.json` should declare:
     - `"id": "<Microsoft App ID>"`
     - `"bots": [{"botId": "<Microsoft App ID>", ...}]`
     - `"validDomains": ["api.clearledgr.com", "workspace.clearledgr.com"]`
   - Package with `color.png` (192×192) + `outline.png` (32×32).
   - Distribute the zip via "Upload a custom app" inside Teams admin
     center, OR submit to AppSource for public listing.

**Verify:**
- After flag flip, the Connections page in the workspace should show
  a Teams card with a "Connect" button.
- Approval cards should render in Teams when an AP item enters
  `needs_approval` state.

---

## 3. Google OAuth verification

**Why:** Mo verified Google OAuth works mid-session after several
fixes, but no end-to-end integration test confirms current behaviour.

**Steps:**

1. Open https://console.cloud.google.com/apis/credentials
2. Find the OAuth 2.0 Client ID `333271407440-j42m0b6sh4j42bvlkr0vko7l058uf3ja…`
3. Confirm **Authorized redirect URIs** includes:
   - `https://api.clearledgr.com/auth/google/callback`
   - `https://api.clearledgr.com/gmail/callback`
4. Confirm **Authorized JavaScript origins** includes:
   - `https://workspace.clearledgr.com`
   - `https://web-app-production-a046.up.railway.app` (until cert clears)

**Verify:**
- Open https://workspace.clearledgr.com/login in incognito
- Click **Continue with Google**, select an account
- Should redirect to workspace home with a session cookie set

---

## 4. Chrome Web Store listing (Gmail extension)

**Why:** Customers can't install the extension without a public
listing. Today the extension is dev-mode only.

**Steps:**

1. Go to https://chrome.google.com/webstore/devconsole
2. Pay the one-time $5 developer registration fee (if not already paid)
3. **New item** → upload the production zip:
   ```
   cd ui/gmail-extension && npm run build:prod
   # produces clearledgr-extension-prod.zip in the ui/gmail-extension folder
   ```
4. Fill the listing:
   - **Description:** copy from `ui/gmail-extension/STORE_LISTING.md`
   - **Screenshots:** capture from the live extension running against
     production (sidebar with a real invoice, three banner states,
     compose-time linkage)
   - **Privacy Policy URL:** `https://workspace.clearledgr.com/privacy`
   - **Support URL:** `mailto:hello@clearledgr.com`
5. Submit for review. Google typically approves in 1–3 business days.

**Update flow after the first publish:**
- The repo's GitHub Actions workflow at
  `.github/workflows/chrome-store-publish.yml` (per memory) handles
  push-to-main → Chrome Web Store auto-publish once these GitHub
  secrets are added: `CHROME_CLIENT_ID`, `CHROME_CLIENT_SECRET`,
  `CHROME_REFRESH_TOKEN`, `CHROME_EXTENSION_ID`.

---

## 5. workspace.clearledgr.com — Let's Encrypt cert

**Why:** Currently stuck in `VALIDATING_OWNERSHIP` on Railway despite
DNS propagated.

**Steps:**

1. Wait 24h after the most recent CNAME change. Let's Encrypt's per-
   domain rate limit (5 failed validations / hour) often clears
   overnight without intervention.
2. If still stuck after 24h, file a Railway support ticket at
   https://railway.com/help with:
   - Project ID: `2479d82b-6d89-4f35-8ac7-5a6224dd6943`
   - Service: `web-app`
   - Domain: `workspace.clearledgr.com`
   - Notes: "DNS PROPAGATED across 8.8.8.8 / 1.1.1.1 / authoritative
     dns1.registrar-servers.com. Cert stuck in VALIDATING_OWNERSHIP
     for 24h+. Please force re-validation."
3. While waiting, the Railway-issued
   `web-app-production-a046.up.railway.app` continues to serve the
   SPA cleanly.

---

## 6. SOC2 Type 1 evidence package

**Why:** Every enterprise security questionnaire asks for SOC2
attestation or, at minimum, evidence of controls in lieu of attestation.

**What's shipped:** the full security packet lives in
[`docs/security/`](security/) and is sales-ready today:

- [`README.md`](security/README.md) — entry point + how to use it
- [`CONTROLS.md`](security/CONTROLS.md) — every TSC control with
  file:line citations into the source
- [`SUB_PROCESSORS.md`](security/SUB_PROCESSORS.md) — Railway,
  Anthropic, Google, Microsoft, Slack, Sentry, Stripe (when active)
- [`INCIDENT_RESPONSE.md`](security/INCIDENT_RESPONSE.md) — full IR
  plan with severity tiers, SLAs, and 72-hour breach notification
- [`VULNERABILITY_DISCLOSURE.md`](security/VULNERABILITY_DISCLOSURE.md)
  — coordinated-disclosure policy + safe-harbor
- [`DPA.md`](security/DPA.md) — GDPR-aligned DPA summary
- [`SECURITY_QUESTIONNAIRE.md`](security/SECURITY_QUESTIONNAIRE.md) —
  pre-fills for SIG/CAIQ recurring questions

**Send-to-prospect workflow:**

```
zip -r clearledgr-security-packet.zip docs/security/
# attach to email, or drop in shared deal room
```

**Path to attestation:** engage a SOC2 auditor (Vanta, Drata,
Secureframe all start at ~$10K). 6-month observation window for Type 2;
Type 1 is a point-in-time snapshot achievable in ~6 weeks. The control
implementations cited in `CONTROLS.md` cover every CC area an auditor
will examine for Type 1.

---

## 7. Status page (optional polish)

**What's shipped:** `/status` page that polls the api's `/health`
endpoint. Sufficient for first GA iteration.

**Optional upgrade for serious enterprise customers:** subscribe to
[statuspage.io](https://statuspage.io) ($29/mo) and embed at
`status.clearledgr.com`. Subscribe link in the StatusPage footer can
point at the third-party service for incident notifications.

---

## 8. Demo tenant for sales

**Why:** Outbound demos can't run against real customer data, and a
brand-new tenant has nothing to show. The demo seed creates an
"Acme Manufacturing" org with 3 entities, 5 users, 12 vendors, and
~40 AP items spanning every state.

**Steps:**

1. From a machine with Railway access:
   ```
   railway run --service api python scripts/seed_demo_tenant.py
   # or, to wipe and reseed:
   railway run --service api python scripts/seed_demo_tenant.py --reset
   ```
2. The script prints the demo emails + password at the end. Default is
   `Demo!2026` — override with `--password XYZ`.

**Verify:**
- Sign in at https://workspace.clearledgr.com/login as
  `controller@acme-demo.clearledgr.dev` with the printed password.
- The home dashboard should show non-zero KPI tiles, the pipeline page
  should show items in every column, and the entity switcher should
  list Acme HQ / Acme Europe / Acme APAC.

**Important:** the seed only writes under `organization_id="acme-demo"`,
so it cannot collide with a real tenant. The `--reset` flag truncates
ONLY that org. Never run this against a tenant ID with real data.

---

## 9. Marketing site at clearledgr.com

**Why:** Cold prospects from outbound need a destination that isn't
the workspace login. Pricing, ICP messaging, "Request a demo" all
live here.

**Stack recommendation:** Astro or Next.js static site, deploy to
Vercel or Railway. Separate from the workspace SPA; only shares the
domain.

**Pages required:**
- `/` — value prop + "Request a demo" CTA
- `/pricing` — three tiers (Starter / Growth / Enterprise)
- `/security` — SOC2 status, sub-processors, DPA download
- `/about` — team, mission, investors (if disclosed)
- `/customers` — Cowrywise + Booking.com case studies (when ready)
- `/blog` — content marketing
- `/contact` — sales contact

**SEO:** title tags, OG images, sitemap.xml. Robots.txt allowing
indexing.

---

## Order of operations

For fastest time-to-GA:

1. **Demo tenant seed** (5 min, no external accounts) — unblocks sales demos
2. **Microsoft OAuth env vars** (5 min after Azure registration done) — unblocks SAP-shop signin
3. **Chrome Web Store submit** (30 min + 1–3 days for Google review) — unblocks extension install
4. **Google OAuth verification** (5 min) — confirms Google signin works
5. **workspace.clearledgr.com cert wait** (24h) — friendly URL
6. **Teams adapter env vars + flag flip** (1h after Bot registration)
7. **Marketing site build** (1–2 weeks part-time)
8. **SOC2 evidence package** (6 weeks for Type 1; engage auditor)

Items 2–6 can ship in parallel since each touches a different external
account. Item 1 is local-only and can ship today.
