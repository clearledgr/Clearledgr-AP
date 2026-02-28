# Gmail Runtime Nightly Runner Setup

Operational setup guide for `/.github/workflows/gmail-runtime-smoke-nightly.yml`.

This workflow is designed to run authenticated Gmail runtime smoke in a controlled environment and publish evidence artifacts under:

- `docs/ga-evidence/releases/<release_id>/artifacts/`
- `docs/ga-evidence/releases/<release_id>/GMAIL_RUNTIME_E2E.md`

## 1. Runner requirements

Provision a self-hosted GitHub Actions runner with label:

- `clearledgr-gmail-e2e`

Recommended baseline:

- Ubuntu 22.04+ (or stable Linux with Chromium deps support)
- Node 20
- Ability to run headed Chromium contexts when required
- Persistent filesystem path for a pre-authenticated Gmail profile

## 2. Prepare authenticated Gmail profile

1. On the runner host, create a dedicated profile directory (example):
   - `/opt/clearledgr/gmail-e2e-profile`
2. Launch Chrome/Chromium once with that profile.
3. Sign in to the controlled Gmail test account.
4. Confirm Gmail inbox loads successfully from that profile.
5. Restrict host/user access to this directory (least privilege).

## 3. Configure GitHub secret and vars

Required repository secret:

- `GMAIL_E2E_PROFILE_DIR`: absolute path to the authenticated profile directory on runner host.

Optional repository variables:

- `GMAIL_E2E_URL` (default Gmail inbox URL if unset)
- `GMAIL_E2E_TIMEOUT_MS` (e.g. `180000`)

### Via GitHub UI

Repository -> Settings -> Secrets and variables -> Actions:

1. Add secret `GMAIL_E2E_PROFILE_DIR`.
2. Add optional variables `GMAIL_E2E_URL`, `GMAIL_E2E_TIMEOUT_MS`.

### Via gh CLI (when API access is available)

```bash
gh auth login -h github.com
gh secret set GMAIL_E2E_PROFILE_DIR --repo clearledgr/Clearledgr-AP --body "/opt/clearledgr/gmail-e2e-profile"
gh variable set GMAIL_E2E_URL --repo clearledgr/Clearledgr-AP --body "https://mail.google.com/mail/u/0/#inbox"
gh variable set GMAIL_E2E_TIMEOUT_MS --repo clearledgr/Clearledgr-AP --body "180000"
```

## 4. Validate before first nightly run

Dispatch the workflow manually:

1. GitHub Actions -> `Gmail Runtime Smoke Nightly` -> Run workflow.
2. Optional input: `release_id` (example: `ap-v1-2026-03-01-pilot-rc1`).

Expected sequence:

1. `Validate controlled Gmail profile` passes.
2. `Runner preflight (profile + browser)` passes.
3. `Run authenticated Gmail smoke with evidence` passes.
4. Artifact upload step attaches release evidence bundle.

## 5. Runner-local preflight command

From runner host:

```bash
cd /path/to/Clearledgr-AP/ui/gmail-extension
npm ci
npx playwright install chromium
GMAIL_E2E_PROFILE_DIR=/opt/clearledgr/gmail-e2e-profile npm run test:e2e-runner:preflight
```

## 6. Failure handling

If preflight fails:

- `missing_profile_dir`: set secret or path correctly.
- `profile_dir_empty` or `profile_dir_missing_chromium_state`: reinitialize profile with authenticated Gmail session.
- `playwright_unavailable` / `playwright_launch_failed`: ensure dependencies + browser install on runner.

If smoke test fails:

- Inspect workflow logs + uploaded artifacts (if any).
- Re-run manually with fixed profile/auth/browser conditions.
- Do not mark release evidence complete until `GMAIL_RUNTIME_E2E.md` validates with passed status.
