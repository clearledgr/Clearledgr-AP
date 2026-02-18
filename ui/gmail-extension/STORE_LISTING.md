# Chrome Web Store Listing

## Basic Information

Extension Name: Clearledgr

Short Description (132 chars max):
```
AP execution inside Gmail. Detect invoices, route approvals in Slack, and post to your ERP with audit logs.
```

Detailed Description:
```
Clearledgr embeds an Accounts Payable execution agent directly in Gmail.

WHAT IT DOES
- Detects invoice emails in Gmail
- Extracts vendor, amount, invoice number, and due date
- Requests approvals in Slack
- Posts approved invoices to your ERP
- Maintains an immutable audit trail

HOW IT WORKS
1. Install the extension
2. Open Gmail
3. Clearledgr surfaces AP items inline
4. Approve in Slack
5. ERP posting and audit trail update automatically

KEY FEATURES
- Gmail embedded workflow
- Slack approve and reject actions
- ERP posting with reference capture
- Audit trail for every action

INTEGRATIONS
- Slack for approvals
- ERP connectors for posting

SECURITY
- Data encrypted in transit and at rest
- No dashboards or external portals
- Audit trail retained for compliance

REQUIREMENTS
- Gmail account
- Slack workspace for approvals
```

## Category

Primary Category: Productivity
Secondary Category: Business Tools

## Permissions Justification

| Permission | Justification |
|------------|---------------|
| `storage` | Store user preferences and cached queue data |
| `activeTab` | Access Gmail tab context |
| `scripting` | Inject InboxSDK UI into Gmail |
| `mail.google.com` | Read invoice emails for AP workflow |
| `localhost` | Development only |
| `api.clearledgr.com` | Clearledgr backend API |

## Review Notes

```
This extension:
1. Activates only on mail.google.com
2. Processes AP emails and attachments
3. Does not send emails on behalf of the user
4. Uses Slack for approvals
```
