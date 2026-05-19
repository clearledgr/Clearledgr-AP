# Solden for Microsoft Teams — Install Runbook

This document covers everything from zero to a Solden Teams bot that
an enterprise tenant can install and use.

The build is in two parts:

| Part | Owner | Time |
|---|---|---|
| **A. Microsoft-side registration** — partner program + Entra app + Bot Framework | Solden ops / partnerships | ~3–6 weeks total (mostly waiting on Microsoft) |
| **B. Solden-side build + deploy** — bot endpoint, app package, sideload test | Solden engineering | ~1–2 days once Part A is done |
| **C. AppSource certification** — listing on the Microsoft Teams Store | Solden ops + Microsoft review | ~4–8 weeks |

Parts A and C are mostly waiting; the engineering work fits in Part B.

---

## Part A — Microsoft-side registration

### A.1 Microsoft Cloud Partner Program enrollment

You need a Microsoft Partner Network (MPN) ID before you can list on
AppSource. This is annual paid membership.

1. Go to <https://partner.microsoft.com/dashboard/account/v3/enrollment/welcome>.
2. Sign in with the Microsoft Entra (Azure AD) tenant that will own
   the Solden bot.
3. Choose "Cloud Partner" and complete the legal entity form.
4. Pay the enrollment fee (typical ~$475/yr for the basic ISV tier).
5. Note the **MPN ID** — paste it into `manifest.json` field
   `developer.mpnId` (currently empty).

Allow 1–2 weeks for Microsoft to validate the legal entity.

### A.2 Microsoft Entra (Azure AD) app registration

The bot is identified to Microsoft by an Entra application registration.

1. Open <https://entra.microsoft.com> and sign in.
2. Identity → Applications → App registrations → **New registration**.
   - Name: `Solden Teams Bot`
   - Supported account types: **Accounts in any organizational directory (multi-tenant)** — required for distribution to other tenants.
   - Redirect URI: leave blank (bot does not use OIDC redirects).
3. After creation, note the **Application (client) ID**. This is the
   `MICROSOFT_APP_ID` referenced everywhere below.
4. Under **Certificates & secrets**, create a new client secret.
   Copy the value immediately (Microsoft only shows it once). This is
   the `MICROSOFT_APP_PASSWORD` env var.
5. Under **API permissions**, add: Microsoft Graph > Delegated >
   `User.Read` (Solden uses no Graph APIs today, but adding this
   covers any SSO follow-up). Click **Grant admin consent**.

### A.3 Bot Framework registration

Bots that talk to Microsoft Teams must be registered with the Bot
Framework Channel registration in Azure.

1. Open <https://portal.azure.com> → **Create a resource** → search
   for **Azure Bot**.
2. Pick **Multi-tenant** and use the **Microsoft App ID** from A.2
   (don't let Azure auto-create a new one — you want the same App ID
   the manifest references).
3. After creation, open the bot resource → **Configuration**:
   - **Messaging endpoint**: `https://api.solden.com/teams/invoices/interactive`
     (production) or your staging hostname during the sideload test.
4. **Channels** tab → add **Microsoft Teams**. Accept the terms.
5. Confirm the bot is reachable: click **Test in Web Chat**. If your
   endpoint isn't live yet, this will 502 — that's fine for now.

### A.4 Domain verification

The messaging endpoint domain (`api.solden.com`) must be reachable
over HTTPS with a valid certificate. The manifest's `validDomains`
list is enforced by Teams when the bot deep-links to web pages.

If `api.solden.com` is already serving the FastAPI app, you're done.
If it's brand-new, provision the DNS A/CNAME record + TLS cert
(Caddy/Let's Encrypt or your CDN) and confirm:

```bash
curl -I https://api.solden.com/healthz   # expect 200
```

---

## Part B — Solden-side build + deploy

### B.1 Configure environment variables

Add to your production env (Railway / Heroku / wherever the FastAPI
app runs):

```
MICROSOFT_APP_ID=<the App ID from A.2>
MICROSOFT_APP_PASSWORD=<the client secret from A.2>
TEAMS_APP_ID=<same as MICROSOFT_APP_ID — Solden's Teams JWT verifier reads this>
FEATURE_TEAMS_ENABLED=true
```

Note: `TEAMS_APP_ID` and `MICROSOFT_APP_ID` carry the same value but
are read by different parts of the codebase (the JWT verifier reads
`TEAMS_APP_ID`; the outbound `TeamsBotClient` reads `MICROSOFT_APP_ID`).
Set both.

Without `FEATURE_TEAMS_ENABLED=true`, every Teams route returns 404 —
this is the V1-rollout boundary. Lift the flag after sideloaded
testing passes.

### B.2 Build the app package

```bash
cd ui/teams/manifest
MICROSOFT_APP_ID=<the App ID from A.2> ./build_package.sh
```

Output: `ui/teams/manifest/dist/solden-teams-1.0.0.zip`.

The script:
1. Substitutes `${MICROSOFT_APP_ID}` in `manifest.json`.
2. Validates the JSON (via `jq` if installed).
3. Zips manifest.json + color.png + outline.png with flat paths.

### B.3 Sideload + smoke-test

1. Open Microsoft Teams (web or desktop).
2. Apps → **Manage your apps** → **Upload an app** → **Upload a
   custom app**.
3. Select `solden-teams-1.0.0.zip`.
4. Add the bot to a 1:1 chat with yourself first (smallest blast
   radius). Type `help` — you should see Solden respond.
5. Drive a test AP item through to `needs_approval` and confirm the
   Adaptive Card lands in your channel. Click Approve. Verify:
   - The card updates to show "Approved by …".
   - `audit_events` table has a `state_transition` row with
     `payload_json.decision_context.ui_surface = "teams"`,
     `actor_type = "user"`, and `actor_id` matching your email.
   - The AP item's state advances to `approved` (or further).

If any of those fail, check:
- `MICROSOFT_APP_ID` matches between Entra, Bot Framework, env, and
  manifest.
- The messaging endpoint URL is reachable (run `curl -I` against it
  from outside your network).
- `FEATURE_TEAMS_ENABLED` is `true` in the running process (not just
  in the env file — restart the app).

### B.4 Distribute internally

Once sideload tests pass, you can ship the same .zip to:

- **Solden internal Teams tenant** — Teams Admin Center > Manage
  apps > Upload new app > approve for the org.
- **Pilot customer tenants** — same process, run by their Teams admin.
  Solden ships the .zip + the runbook section below as a pilot guide.

---

## Part C — AppSource certification

Run this in parallel with Part B.3+B.4. The Microsoft review usually
takes 4–8 weeks.

1. Go to <https://partner.microsoft.com/dashboard/marketplace-offers>.
2. **+ New offer** → **Microsoft 365 and Copilot apps** → **Teams app**.
3. Upload `solden-teams-1.0.0.zip`.
4. Fill out the listing:
   - Categories: Productivity, Project management, Workflow & business management.
   - Plans: Free trial / Solden licence (depending on pricing).
   - Pricing: per Solden's pricing page (or "Contact us" for
     enterprise per the no-fabricated-pricing rule in CLAUDE.md).
   - Privacy + terms URLs: the ones already in `manifest.json`.
   - Test instructions: provide a sandbox tenant + test user the
     reviewer can drive Solden through end-to-end.
5. Submit for **Validation**. Microsoft runs:
   - Manifest validation (schema 1.16 compliance).
   - Microsoft 365 Certification: security questionnaire, encryption
     audit, data-handling review.
   - Functional review: a human at Microsoft drives the bot through
     the test scenarios you provide.
6. Address any review findings. Iterate until **Publish**.

---

## Pilot-customer install guide (single-tenant)

For customers who want to install Solden on their own Teams tenant
ahead of AppSource availability:

1. Get `solden-teams-<version>.zip` from Solden support.
2. Open Microsoft Teams Admin Center (`admin.teams.microsoft.com`).
3. **Teams apps** → **Manage apps** → **Upload new app** → upload
   the .zip.
4. Approve for the org → set permission policy to allow Solden.
5. Users can now install the Solden bot from Teams' app catalog
   inside the tenant.

Solden's support team coordinates pilot installs over a screen-share
the first time per tenant.

---

## Operational runbook references

- Bot logs: tail the FastAPI app logs and grep for
  `teams_invoices` — every interactive callback writes one structured
  log line per audit_event emitted.
- JWT verifier failures: 401s logged at WARNING level in
  `solden.core.teams_verify`. Common cause: stale env after a
  Bot Framework key rotation (5-min cache + stale-fallback handles
  most cases automatically).
- Card-update failures: enqueued for retry via
  `pending_notifications` table with `channel='teams_card_update'`.
- Feature flag: `FEATURE_TEAMS_ENABLED`. Flip OFF as a kill switch
  if a regression lands; the router immediately starts 404ing all
  Teams routes.
