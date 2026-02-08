#!/bin/bash
# Deploy Clearledgr to Google Apps Script

set -e

echo "=== Clearledgr Google Workspace Deployment ==="

# Check for clasp
if ! command -v clasp &> /dev/null; then
    echo "Installing clasp..."
    npm install -g @google/clasp
fi

# Check login status
if ! clasp login --status &> /dev/null; then
    echo "Please login to Google..."
    clasp login
fi

# Deploy Sheets add-on
echo ""
echo "=== Deploying Sheets Add-on ==="
cd ../../ui/sheets

if [ ! -f ".clasp.json" ]; then
    echo "Creating new Sheets project..."
    clasp create --type sheets --title "Clearledgr for Sheets"
else
    echo "Using existing project..."
fi

echo "Pushing Sheets code..."
clasp push

echo "Creating deployment..."
SHEETS_DEPLOYMENT=$(clasp deploy --description "v1.0.0" 2>&1 | grep -oP '(?<=- )AKfycb[a-zA-Z0-9_-]+' | head -1)
echo "Sheets deployment ID: $SHEETS_DEPLOYMENT"

# Deploy Gmail add-on
echo ""
echo "=== Deploying Gmail Add-on ==="
cd ../gmail

if [ ! -f ".clasp.json" ]; then
    echo "Creating new Gmail project..."
    clasp create --type gmail --title "Clearledgr for Gmail"
else
    echo "Using existing project..."
fi

echo "Pushing Gmail code..."
clasp push

echo "Creating deployment..."
GMAIL_DEPLOYMENT=$(clasp deploy --description "v1.0.0" 2>&1 | grep -oP '(?<=- )AKfycb[a-zA-Z0-9_-]+' | head -1)
echo "Gmail deployment ID: $GMAIL_DEPLOYMENT"

echo ""
echo "=== Deployment Complete ==="
echo ""
echo "Next steps:"
echo "1. Go to https://console.cloud.google.com/apis/dashboard"
echo "2. Enable 'Google Workspace Marketplace SDK'"
echo "3. Configure your marketplace listing"
echo "4. Submit for review"
echo ""
echo "Deployment IDs (save these):"
echo "  Sheets: $SHEETS_DEPLOYMENT"
echo "  Gmail:  $GMAIL_DEPLOYMENT"

