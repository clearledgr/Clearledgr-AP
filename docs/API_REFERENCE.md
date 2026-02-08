# Clearledgr API Reference

Complete API documentation for the Clearledgr backend.

## Base URL

```
Production: https://api.clearledgr.com
Development: http://localhost:8000
```

## Authentication

Most endpoints require authentication via JWT Bearer token.

```http
Authorization: Bearer <access_token>
```

### Get Token

**For Gmail Extension (Google Identity):**
```http
POST /auth/google-identity
Content-Type: application/json

{
  "email": "user@company.com",
  "google_id": "google-account-id"
}
```

**Response:**
```json
{
  "access_token": "eyJ...",
  "expires_in": 3600,
  "user_id": "usr_123",
  "organization_id": "company",
  "is_new_user": false
}
```

**For Web/API:**
```http
POST /auth/login
Content-Type: application/json

{
  "email": "user@example.com",
  "password": "password"
}
```

---

## Core Endpoints

### Health Check

```http
GET /health
```

Returns system health status.

---

## Analytics API

### Dashboard Metrics

```http
GET /analytics/dashboard/{organization_id}
```

**Response:**
```json
{
  "pending_review": 5,
  "auto_processed": 127,
  "total_posted": 45000,
  "exceptions": 2,
  "recent_activity": [
    {
      "action": "Invoice approved",
      "vendor": "AWS",
      "amount": 1500,
      "timestamp": "2024-01-15T10:30:00Z"
    }
  ]
}
```

### Spend by Vendor

```http
GET /analytics/spend-by-vendor/{organization_id}
```

### Spend by Category

```http
GET /analytics/spend-by-category/{organization_id}
```

### Processing Metrics

```http
GET /analytics/processing-metrics/{organization_id}
```

---

## AP Workflow API

### Payments

#### List Pending Payments

```http
GET /ap/payments/pending?organization_id={org_id}
```

**Response:**
```json
[
  {
    "payment_id": "pay_123",
    "invoice_id": "INV-001",
    "vendor_id": "v_456",
    "vendor_name": "Acme Corp",
    "amount": 1500.00,
    "currency": "USD",
    "method": "ach",
    "status": "pending",
    "scheduled_date": null,
    "created_at": "2024-01-15T10:00:00Z"
  }
]
```

#### Create Payment

```http
POST /ap/payments/create
Content-Type: application/json

{
  "invoice_id": "INV-001",
  "vendor_id": "v_456",
  "vendor_name": "Acme Corp",
  "amount": 1500.00,
  "currency": "USD",
  "method": "ach",
  "scheduled_date": "2024-01-20",
  "organization_id": "default"
}
```

**Methods:** `ach`, `wire`, `check`

#### Schedule Payment

```http
POST /ap/payments/schedule
Content-Type: application/json

{
  "payment_id": "pay_123",
  "scheduled_date": "2024-01-20",
  "organization_id": "default"
}
```

#### Create ACH Batch

```http
POST /ap/payments/batch
Content-Type: application/json

{
  "payment_ids": ["pay_123", "pay_456"],
  "execute_immediately": false,
  "organization_id": "default"
}
```

**Response:**
```json
{
  "batch_id": "batch_789",
  "payment_count": 2,
  "total_amount": 3000.00,
  "status": "pending",
  "nacha_file": "101 ..."
}
```

#### Get Wire Instructions

```http
POST /ap/payments/{payment_id}/wire-instructions?organization_id={org_id}
```

#### Mark Payment Sent

```http
POST /ap/payments/{payment_id}/mark-sent
Content-Type: application/json

{
  "payment_id": "pay_123",
  "confirmation_number": "CONF-12345",
  "organization_id": "default"
}
```

#### Mark Payment Completed

```http
POST /ap/payments/{payment_id}/mark-completed?organization_id={org_id}
```

#### Payment Summary

```http
GET /ap/payments/summary?organization_id={org_id}
```

**Response:**
```json
{
  "pending": 5,
  "scheduled": 3,
  "processing_amount": 15000.00,
  "completed_30d": 42
}
```

---

### GL Corrections

#### Get GL Accounts

```http
GET /ap/gl/accounts?organization_id={org_id}&account_type={type}&search={query}
```

**Response:**
```json
[
  {
    "code": "5000",
    "name": "Operating Expenses",
    "account_type": "expense",
    "category": "Operations"
  }
]
```

#### Add GL Account

```http
POST /ap/gl/accounts
Content-Type: application/json

{
  "code": "5200",
  "name": "Software & Subscriptions",
  "account_type": "expense",
  "category": "Technology",
  "organization_id": "default"
}
```

#### Get GL Suggestion

```http
GET /ap/gl/suggest?vendor={vendor}&amount={amount}&organization_id={org_id}
```

**Response:**
```json
{
  "suggested_gl": "5200",
  "confidence": 0.95,
  "reason": "Based on 12 previous invoices from this vendor"
}
```

#### Record GL Correction

```http
POST /ap/gl/correct
Content-Type: application/json

{
  "invoice_id": "INV-001",
  "vendor": "AWS",
  "original_gl": "5000",
  "corrected_gl": "5200",
  "amount": 1500.00,
  "reason": "Cloud infrastructure cost",
  "corrected_by": "user@company.com",
  "organization_id": "default"
}
```

#### Get Recent Corrections

```http
GET /ap/gl/corrections?vendor={vendor}&limit={n}&organization_id={org_id}
```

#### GL Correction Stats

```http
GET /ap/gl/stats?organization_id={org_id}
```

**Response:**
```json
{
  "total_corrections": 156,
  "accuracy": 0.94,
  "learned_rules": 23
}
```

---

### Recurring Invoices

#### List Rules

```http
GET /ap/recurring/rules?enabled_only=true&organization_id={org_id}
```

**Response:**
```json
[
  {
    "rule_id": "rule_123",
    "vendor": "Adobe",
    "expected_frequency": "monthly",
    "expected_amount": 99.99,
    "amount_tolerance_pct": 5.0,
    "action": "auto_approve",
    "default_gl_code": "5200",
    "vendor_aliases": ["ADOBE SYSTEMS"],
    "enabled": true,
    "created_at": "2024-01-01T00:00:00Z"
  }
]
```

#### Create Rule

```http
POST /ap/recurring/rules
Content-Type: application/json

{
  "vendor": "Adobe",
  "expected_frequency": "monthly",
  "expected_amount": 99.99,
  "amount_tolerance_pct": 5.0,
  "action": "auto_approve",
  "default_gl_code": "5200",
  "vendor_aliases": ["ADOBE SYSTEMS", "Adobe Inc"],
  "notes": "Creative Cloud subscription",
  "organization_id": "default"
}
```

**Frequencies:** `weekly`, `monthly`, `quarterly`, `annual`
**Actions:** `auto_approve`, `flag_for_review`, `notify_only`

#### Update Rule

```http
PUT /ap/recurring/rules/{rule_id}
Content-Type: application/json

{
  "updates": {
    "expected_amount": 109.99,
    "action": "flag_for_review"
  },
  "organization_id": "default"
}
```

#### Delete Rule

```http
DELETE /ap/recurring/rules/{rule_id}?organization_id={org_id}
```

#### Process Invoice

```http
POST /ap/recurring/process
Content-Type: application/json

{
  "invoice_id": "INV-001",
  "vendor": "Adobe",
  "amount": 99.99,
  "currency": "USD",
  "invoice_date": "2024-01-15",
  "organization_id": "default"
}
```

**Response:**
```json
{
  "matched": true,
  "rule_id": "rule_123",
  "action": "auto_approve",
  "amount_variance_pct": 0.0,
  "auto_approved": true
}
```

#### Get Upcoming Invoices

```http
GET /ap/recurring/upcoming?days=30&organization_id={org_id}
```

**Response:**
```json
[
  {
    "vendor": "Adobe",
    "expected_amount": 99.99,
    "frequency": "monthly",
    "expected_date": "2024-02-01",
    "days_until": 15,
    "action": "auto_approve"
  }
]
```

#### Subscription Summary

```http
GET /ap/recurring/summary?organization_id={org_id}
```

**Response:**
```json
{
  "active_rules": 12,
  "monthly_spend": 1500.00,
  "annual_spend": 18000.00,
  "due_this_week": 3,
  "auto_approved": 8
}
```

#### Detect Recurring Pattern

```http
POST /ap/recurring/detect
Content-Type: application/json

{
  "vendor": "New Vendor",
  "invoices": [
    {"date": "2024-01-15", "amount": 99.00},
    {"date": "2024-02-15", "amount": 99.00},
    {"date": "2024-03-15", "amount": 99.00}
  ],
  "organization_id": "default"
}
```

**Response:**
```json
{
  "detected": true,
  "suggestion": {
    "detected_frequency": "monthly",
    "detected_amount": 99.00,
    "confidence": 0.92
  }
}
```

---

## Gmail Extension API

### Triage Email

```http
POST /extension/triage
Content-Type: application/json

{
  "email_id": "gmail-message-id",
  "subject": "Invoice #12345",
  "sender": "billing@vendor.com",
  "body": "Invoice content...",
  "attachments": [],
  "organization_id": "default"
}
```

**Response:**
```json
{
  "classification": "invoice",
  "confidence": 0.95,
  "extracted": {
    "vendor": "Vendor Name",
    "amount": 1500.00,
    "invoice_number": "12345",
    "due_date": "2024-02-01"
  },
  "suggested_action": "review",
  "suggested_gl": "5200"
}
```

### Get Invoice Pipeline

```http
GET /extension/invoice-pipeline/{organization_id}
```

**Response:**
```json
{
  "detected": [
    {"email_id": "...", "vendor": "AWS", "amount": 1500, "status": "detected"}
  ],
  "review": [
    {"email_id": "...", "vendor": "Adobe", "amount": 99, "status": "review"}
  ],
  "approved": [],
  "posted": []
}
```

### Submit for Approval

```http
POST /extension/submit-for-approval
Content-Type: application/json

{
  "email_id": "gmail-message-id",
  "invoice_data": {
    "vendor": "AWS",
    "amount": 1500.00,
    "gl_code": "5200"
  },
  "organization_id": "default"
}
```

### Approve and Post

```http
POST /extension/approve-and-post
Content-Type: application/json

{
  "email_id": "gmail-message-id",
  "approved_by": "user@company.com",
  "gl_code": "5200",
  "erp_target": "quickbooks",
  "organization_id": "default"
}
```

---

## Gmail Pub/Sub Webhooks

### Authorization

```http
GET /gmail/authorize?user_id={user_id}&redirect_url={url}
```

Returns OAuth URL to redirect user to.

### OAuth Callback

```http
GET /gmail/callback?code={code}&state={state}
```

Exchanges OAuth code for tokens and sets up Gmail watch.

### Push Notification (Pub/Sub)

```http
POST /gmail/push
Content-Type: application/json

{
  "message": {
    "data": "base64-encoded-notification"
  },
  "subscription": "projects/project/subscriptions/sub"
}
```

This endpoint is called by Google Cloud Pub/Sub.

### Check Status

```http
GET /gmail/status/{user_id}
```

**Response:**
```json
{
  "connected": true,
  "email": "user@company.com",
  "expires_at": "2024-02-15T10:00:00Z",
  "is_expired": false
}
```

### Disconnect

```http
POST /gmail/disconnect
Content-Type: application/json

{
  "user_id": "user-id"
}
```

---

## ERP Integration

### Connection Status

```http
GET /erp/status/{organization_id}
```

**Response:**
```json
{
  "quickbooks": {
    "connected": true,
    "company_name": "Company Inc",
    "expires_at": "2024-02-15T10:00:00Z"
  },
  "xero": {
    "connected": false
  },
  "netsuite": {
    "connected": false
  }
}
```

### OAuth URLs

```http
GET /oauth/quickbooks/authorize?organization_id={org_id}
GET /oauth/xero/authorize?organization_id={org_id}
```

### Refresh Connection

```http
POST /erp/refresh/{organization_id}/{erp_type}
```

---

## Organization Settings

### Get Settings

```http
GET /settings/{organization_id}
```

### Update Approval Thresholds

```http
PUT /settings/{organization_id}/approval-thresholds
Content-Type: application/json

{
  "auto_approve_limit": 500,
  "manager_approval_limit": 5000,
  "executive_approval_limit": 25000
}
```

### GL Mappings

```http
GET /settings/{organization_id}/gl-mappings
POST /settings/{organization_id}/gl-mappings
DELETE /settings/{organization_id}/gl-mappings/{mapping_id}
```

---

## Error Responses

All endpoints return errors in this format:

```json
{
  "detail": "Error message describing what went wrong"
}
```

Common status codes:
- `400` - Bad request (invalid input)
- `401` - Unauthorized (invalid/missing token)
- `403` - Forbidden (insufficient permissions)
- `404` - Not found
- `422` - Validation error
- `500` - Internal server error

---

## Rate Limits

- **Standard endpoints:** 100 requests/minute
- **Webhook endpoints:** 1000 requests/minute
- **Batch operations:** 10 requests/minute

Rate limit headers:
```http
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 95
X-RateLimit-Reset: 1705320000
```

---

## Webhooks

### Slack Notifications

Configure Slack webhook in settings to receive:
- Invoice approval requests
- Payment confirmations
- Exception alerts

### Custom Webhooks

```http
POST /settings/{organization_id}/webhooks
Content-Type: application/json

{
  "url": "https://your-server.com/webhook",
  "events": ["invoice.detected", "invoice.approved", "payment.completed"],
  "secret": "webhook-secret"
}
```

---

## SDK & Client Libraries

### JavaScript (Gmail Extension)

```javascript
const BACKEND_URL = 'http://localhost:8000';

// Authenticate with Google Identity
async function authenticate(email, googleId) {
  const response = await fetch(`${BACKEND_URL}/auth/google-identity`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, google_id: googleId }),
  });
  return response.json();
}

// Triage an email
async function triageEmail(emailData, token) {
  const response = await fetch(`${BACKEND_URL}/extension/triage`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}`,
    },
    body: JSON.stringify(emailData),
  });
  return response.json();
}
```

### Python

```python
import requests

class ClearledgrClient:
    def __init__(self, base_url='http://localhost:8000', token=None):
        self.base_url = base_url
        self.token = token
    
    def _headers(self):
        h = {'Content-Type': 'application/json'}
        if self.token:
            h['Authorization'] = f'Bearer {self.token}'
        return h
    
    def get_dashboard(self, org_id='default'):
        return requests.get(
            f'{self.base_url}/analytics/dashboard/{org_id}',
            headers=self._headers()
        ).json()
    
    def create_payment(self, invoice_id, vendor_name, amount, method='ach'):
        return requests.post(
            f'{self.base_url}/ap/payments/create',
            headers=self._headers(),
            json={
                'invoice_id': invoice_id,
                'vendor_id': vendor_name.lower().replace(' ', '-'),
                'vendor_name': vendor_name,
                'amount': amount,
                'method': method,
                'organization_id': 'default',
            }
        ).json()
```

---

## Changelog

### v1.0.0 (Current)
- Full AP workflow (payments, GL corrections, recurring)
- Gmail Pub/Sub integration
- ERP integrations (QuickBooks, Xero, NetSuite, SAP)
- Slack approval workflow
