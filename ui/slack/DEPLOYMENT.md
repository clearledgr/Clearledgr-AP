# Clearledgr Slack App Deployment

## What Users Experience

After deployment, users will:
1. Go to Slack App Directory
2. Search "Clearledgr"
3. Click "Add to Slack"
4. Done - no configuration needed

## Deployment Steps

### 1. Create Slack App

1. Go to https://api.slack.com/apps
2. Click "Create New App" → "From manifest"
3. Select your workspace
4. Paste contents of `manifest.json`
5. Update `YOUR_DOMAIN` with your actual domain
6. Click "Create"

### 2. Configure App Credentials

After creation, note these values from "Basic Information":
- **App ID**
- **Client ID** 
- **Client Secret**
- **Signing Secret**

Set as environment variables:
```bash
export SLACK_BOT_TOKEN="xoxb-..."
export SLACK_SIGNING_SECRET="..."
export SLACK_CLIENT_ID="..."
export SLACK_CLIENT_SECRET="..."
```

### 3. Install Bot Token

1. Go to "OAuth & Permissions"
2. Click "Install to Workspace"
3. Copy the "Bot User OAuth Token" (starts with `xoxb-`)
4. Set as `SLACK_BOT_TOKEN`

### 4. Deploy Backend

Ensure your API is accessible at a public URL:
```bash
# Example with your cloud provider
https://api.clearledgr.com
```

Update manifest.json URLs from `YOUR_DOMAIN` to your actual domain.

### 5. Submit for App Directory (Optional)

For public distribution:
1. Go to "Manage Distribution"
2. Complete all requirements
3. Submit for review

## Available Commands

Once installed, users can:

| Command | Description |
|---------|-------------|
| `/clearledgr status` | View reconciliation status |
| `/clearledgr run` | Trigger reconciliation |
| `/clearledgr exceptions` | List open exceptions |
| `/clearledgr tasks` | View pending tasks |
| `/reconcile` | Quick reconciliation |

## Features

- **Slash Commands**: Quick actions from anywhere in Slack
- **Interactive Buttons**: Resolve exceptions, complete tasks inline
- **App Home**: Dashboard with stats and quick actions
- **Message Shortcuts**: Turn any message into a finance task
- **Rich Notifications**: Adaptive messages with action buttons

## Testing Locally

Use ngrok to expose your local server:
```bash
ngrok http 8000
```

Update manifest URLs to ngrok URL, then reinstall app.
