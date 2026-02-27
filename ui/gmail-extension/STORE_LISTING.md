# Chrome Web Store Listing

## Basic Information

**Extension Name**: Clearledgr

**Short Description** (132 chars max):
```
Agentic AP for Gmail. Extract invoices, route approvals in Slack/Teams, and post to ERP with policy checks and audit trails.
```

**Detailed Description**:
```
Clearledgr is an embedded finance execution agent for Accounts Payable.

It runs AP workflows where finance teams already work: Gmail for operators, Slack/Teams for approvals, and ERP for system-of-record posting.

WHAT IT DOES
- Detects invoice and AP request emails in Gmail
- Extracts key invoice fields from email + attachments
- Applies deterministic policy checks and confidence gates
- Routes approvals to Slack and Teams
- Posts approved invoices to ERP with idempotency and full audit trails
- Surfaces exceptions and next actions directly in Gmail

HOW IT WORKS
1. Install the extension and connect your workspace
2. Open Gmail and view the AP operator sidebar in-thread
3. Review extracted data and policy/exception status
4. Route approvals; approvers decide in Slack/Teams
5. Clearledgr posts to ERP and records auditable outcomes

KEY FEATURES
- Gmail-first AP workflow (no separate daily dashboard required)
- Policy-aware routing and execution
- Human-in-the-loop controls for risky actions
- Runtime intent orchestration with auditable state transitions
- ERP write-back with idempotency and correlation metadata

INTEGRATIONS
- Slack / Teams: approval decisions
- ERP connectors: QuickBooks, Xero, NetSuite, SAP
- Gmail API + extension surface for intake and operations

SECURITY
- Data encrypted in transit and at rest
- Authenticated API boundaries
- Signed callback verification for chat approvals
- Audit trail for transitions and external writes

REQUIREMENTS
- Clearledgr account
- Gmail account with extension access
- Slack and/or Teams integration for approvals
- ERP credentials for write-back

SUPPORT
- Documentation: docs.clearledgr.com
- Email: support@clearledgr.com
```

## Category

**Primary Category**: Business Tools
**Secondary Category**: Productivity

## Language

**Primary**: English

## Pricing

**Price**: Free install (Clearledgr subscription required for production usage)

## Screenshots Required

1. **AP Thread Workspace** (1280x800)
   - Gmail thread with Clearledgr AP panel
   - Status, exceptions, and next action visible

2. **Approval Routing** (1280x800)
   - Invoice routed for Slack/Teams approval
   - Decision context and controls visible

3. **Exception Handling** (1280x800)
   - Needs-info / failed-post states with operator guidance

4. **Audit + Timeline** (1280x800)
   - Agent timeline and audit breadcrumbs for one AP item

5. **Batch Agent Ops** (1280x800)
   - Preview-first batch operations with deterministic selection reasons

## Promotional Images

1. **Small Promo Tile** (440x280)
   - "Clearledgr AP Agent for Gmail"

2. **Large Promo Tile** (920x680)
   - Gmail + Slack/Teams + ERP execution path

3. **Marquee** (1400x560)
   - "Embedded Agentic AP Execution"

## Store Listing URLs

- **Website**: https://clearledgr.com
- **Support**: https://clearledgr.com/support
- **Privacy Policy**: https://clearledgr.com/privacy
- **Terms of Service**: https://clearledgr.com/terms

## Developer Information

- **Developer Name**: Clearledgr Inc.
- **Developer Email**: developers@clearledgr.com
- **Developer Website**: https://clearledgr.com

## Permissions Justification

| Permission | Justification |
|------------|---------------|
| `storage` | Store user preferences and runtime settings |
| `activeTab` | Access Gmail tab context for operator UI |
| `scripting` | Inject AP workspace UI into Gmail |
| `tabs` | Detect Gmail navigation state |
| `mail.google.com` | Read finance-email context for AP execution |
| `api.clearledgr.com` | Connect to runtime/orchestration APIs |

## Review Notes for Chrome Team

```
This extension:
1. Activates on Gmail domains only
2. Supports AP workflow execution with policy gates and HITL controls
3. Uses authenticated backend APIs for runtime actions
4. Does not bypass approval controls for high-risk actions
5. Maintains auditable action/state history
```

## Publication Checklist

- [ ] Privacy policy published at https://clearledgr.com/privacy
- [ ] Terms of service published at https://clearledgr.com/terms
- [ ] Support page live at https://clearledgr.com/support
- [ ] Screenshots captured at 1280x800
- [ ] Promo assets prepared
- [ ] Production API endpoint configured
- [ ] localhost removed from production host permissions
- [ ] Version number updated
- [ ] Build tested on clean Chrome profile
