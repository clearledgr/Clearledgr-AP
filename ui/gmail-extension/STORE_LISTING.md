# Chrome Web Store Listing

## Basic Information

**Extension Name**: Clearledgr

**Short Description** (132 chars max):
```
AI finance agent for Gmail. Automatically detect invoices, bank statements & reconcile transactions without leaving your inbox.
```

**Detailed Description**:
```
Clearledgr embeds an autonomous finance agent directly in Gmail to eliminate manual data entry and accelerate your financial close.

WHAT IT DOES
- Automatically detects finance emails (invoices, bank statements, payment confirmations)
- Extracts transaction data using AI (amounts, dates, vendors, invoice numbers)
- Matches transactions across sources with 95%+ accuracy
- Categorizes expenses to GL accounts based on learned patterns
- Routes exceptions to the right team member for review

HOW IT WORKS
1. Install the extension
2. Connect to your Clearledgr account
3. Open Gmail - Clearledgr surfaces finance emails in a sidebar
4. Review AI-extracted data and approve with one click
5. Matched transactions flow to your ERP automatically

KEY FEATURES
- Finance Email Detection: AI identifies bank statements, invoices, and payment confirmations
- Smart Extraction: Pulls amounts, dates, vendors, and references automatically  
- Multi-Source Matching: Reconciles gateway, bank, and internal transactions
- GL Categorization: Learns your coding patterns and suggests accounts
- Exception Routing: Flags mismatches and routes to the right reviewer
- Audit Trail: Every action logged for compliance

INTEGRATIONS
- Google Sheets: Full reconciliation workflow
- Slack: Approvals and notifications
- SAP: Journal entry posting

SECURITY
- Data encrypted in transit (TLS 1.3) and at rest (AES-256)
- SOC 2 Type II compliant
- GDPR compliant
- No data sold to third parties

REQUIREMENTS
- Clearledgr account (free trial available)
- Google Workspace or personal Gmail

SUPPORT
- Documentation: docs.clearledgr.com
- Email: support@clearledgr.com
- In-app chat with Vita AI assistant
```

## Category

**Primary Category**: Productivity
**Secondary Category**: Business Tools

## Language

**Primary**: English
**Supported**: English, French, German, Spanish (roadmap)

## Pricing

**Price**: Free (requires Clearledgr subscription for full features)

## Screenshots Required

1. **Main Sidebar View** (1280x800)
   - Shows Gmail with Clearledgr sidebar open
   - Finance emails detected and displayed
   - Status indicators visible

2. **Email Detection** (1280x800)
   - Invoice email with extraction overlay
   - Highlighted amounts, dates, vendor

3. **Reconciliation Results** (1280x800)
   - Matched transactions with scores
   - Quick approve buttons

4. **Vita AI Chat** (1280x800)
   - Chat interface in sidebar
   - Example: "Show exceptions needing review"

5. **Settings Panel** (1280x800)
   - Configuration options
   - Connected integrations

## Promotional Images

1. **Small Promo Tile** (440x280)
   - Clearledgr logo
   - "AI Finance Agent for Gmail"

2. **Large Promo Tile** (920x680)
   - Dashboard preview
   - Key features highlighted
   - "Reconcile in Gmail"

3. **Marquee** (1400x560)
   - Hero image with Gmail integration
   - "From Weeks to Hours"

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
| `storage` | Store user preferences and cached data locally |
| `activeTab` | Access current Gmail tab to detect emails |
| `scripting` | Inject sidebar UI into Gmail |
| `tabs` | Detect when user navigates to Gmail |
| `mail.google.com` | Read email content to detect finance documents |
| `localhost` | Development/testing only (removed in prod build) |
| `api.clearledgr.com` | Connect to Clearledgr backend services |

## Review Notes for Chrome Team

```
This extension:
1. Only activates on mail.google.com
2. Only processes emails the user explicitly interacts with
3. Does not access email content until user clicks on an email
4. Does not modify or send emails on behalf of the user
5. Requires explicit user login to Clearledgr account
6. All data processing follows GDPR guidelines

For testing:
- Demo account: demo@clearledgr.com / DemoPassword123
- Test with finance-related emails (invoices, bank statements)
```

## Publication Checklist

- [ ] Privacy policy published at https://clearledgr.com/privacy
- [ ] Terms of service published at https://clearledgr.com/terms
- [ ] Support page live at https://clearledgr.com/support
- [ ] All 5 screenshots captured at 1280x800
- [ ] Promo tiles designed (440x280, 920x680, 1400x560)
- [ ] Demo account created for review team
- [ ] Production API endpoint configured
- [ ] localhost removed from host_permissions
- [ ] Version number updated
- [ ] Build tested on clean Chrome profile
