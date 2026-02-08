# Testing Clearledgr Before Marketplace Submission

No approval needed. Test everything locally first.

---

## 1. Google Sheets Add-on (5 minutes)

### Start Backend
```bash
cd /Users/mombalam/Desktop/Clearledgr.v1
python3 -m uvicorn main:app --host 0.0.0.0 --port 8000
```

### Deploy to Apps Script
```bash
# Install clasp
npm install -g @google/clasp

# Login
clasp login

# Create and push
cd ui/sheets
clasp create --type sheets --title "Clearledgr Test"
clasp push

# Open in browser
clasp open
```

### Test in Sheets
1. In Apps Script editor, click "Deploy" > "Test deployments"
2. Click "Install" next to your test deployment
3. Open any Google Sheet
4. Look for "Clearledgr" in the menu bar
5. Click "Clearledgr" > "Reconciliation" > "Run Reconciliation"

---

## 2. Gmail Add-on (5 minutes)

```bash
cd ui/gmail
clasp create --type gmail --title "Clearledgr Gmail Test"
clasp push
clasp open
```

### Test in Gmail
1. In Apps Script editor, click "Deploy" > "Test deployments"
2. Select "Gmail Add-on" and click "Install"
3. Open Gmail
4. Open any email
5. Look for Clearledgr icon in the right sidebar

---

## 3. Excel Add-in (5 minutes)

### Option A: Sideload via Network Share (Windows/Mac)

1. Start a local server for the taskpane:
```bash
cd ui/excel
python3 -m http.server 3000
```

2. In another terminal, use ngrok for HTTPS:
```bash
ngrok http 3000
```

3. Update manifest.xml with your ngrok URL

4. In Excel:
   - Mac: Insert > Add-ins > My Add-ins > Upload My Add-in
   - Windows: Insert > My Add-ins > Upload My Add-in
   - Browse to ui/excel/manifest.xml

### Option B: Use Office Add-in Dev Server
```bash
npm install -g office-addin-dev-certs
npm install -g office-addin-debugging

cd ui/excel
npx office-addin-debugging start manifest.xml
```

---

## 4. Outlook Add-in (5 minutes)

Same process as Excel:
```bash
cd ui/outlook
python3 -m http.server 3001
# Use ngrok for HTTPS
ngrok http 3001
```

Then sideload manifest.xml in Outlook:
- Insert > Get Add-ins > My Add-ins > Upload My Add-in

---

## 5. Slack App (10 minutes)

### Create Test App
1. Go to https://api.slack.com/apps
2. Click "Create New App" > "From manifest"
3. Paste contents of ui/slack/manifest.json
4. Replace YOUR_DOMAIN with your ngrok URL

### Start Backend with ngrok
```bash
# Terminal 1: Start backend
cd /Users/mombalam/Desktop/Clearledgr.v1
python3 -m uvicorn main:app --host 0.0.0.0 --port 8000

# Terminal 2: Expose via ngrok
ngrok http 8000
```

### Install to Workspace
1. In Slack App settings, go to "Install App"
2. Click "Install to Workspace"
3. Go to your Slack workspace
4. Type `/clearledgr status`

---

## 6. Teams App (10 minutes)

### Create App Package
```bash
cd ui/teams
zip -r clearledgr-test.zip manifest.json color.png outline.png
```

Note: You need color.png (192x192) and outline.png (32x32) in ui/teams/

### Upload to Teams
1. Open Microsoft Teams
2. Click "Apps" in sidebar
3. Click "Manage your apps" at bottom
4. Click "Upload an app"
5. Select "Upload a custom app"
6. Choose clearledgr-test.zip

### Test
- Type `@Clearledgr status` in any chat

---

## Quick Test Order

Recommended order (fastest to test):

1. **Google Sheets** - Easiest, no HTTPS needed for test deployment
2. **Slack** - Quick with ngrok
3. **Excel** - Sideload works well
4. **Gmail** - Same process as Sheets
5. **Outlook** - Same as Excel
6. **Teams** - Needs app package

---

## Common Issues

### "Add-in not loading"
- Check browser console for errors
- Ensure backend is running
- Verify URLs in manifest match your server

### "Network error"
- Backend must be accessible
- For Slack/Teams, must use HTTPS (ngrok)

### "Permission denied"
- Google: Re-authorize in Apps Script
- Office: Clear cache, re-sideload
- Slack: Reinstall app to workspace

