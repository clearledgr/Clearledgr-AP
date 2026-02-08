"""
Expense Workflow Service

Handles expense reimbursements from Slack messages.

Flow:
1. Employee posts in Slack: "I spent $30 on lunch"
2. Clearledgr detects expense request
3. Extracts: amount, category, receipt
4. Routes for manager approval
5. Posts to ERP as expense/reimbursement
"""

import re
import logging
from typing import Any, Dict, List, Optional
from datetime import datetime
from dataclasses import dataclass

from clearledgr.core.database import get_db
from clearledgr.services.slack_api import SlackAPIClient, get_slack_client

logger = logging.getLogger(__name__)


@dataclass
class ExpenseRequest:
    """Represents an expense reimbursement request from Slack."""
    slack_user_id: str
    slack_channel_id: str
    slack_message_ts: str
    message_text: str
    amount: Optional[float] = None
    currency: str = "USD"
    category: Optional[str] = None
    description: Optional[str] = None
    receipt_url: Optional[str] = None
    organization_id: Optional[str] = None


class ExpenseWorkflowService:
    """
    Handles expense reimbursement requests from Slack.
    
    Usage:
        service = ExpenseWorkflowService(organization_id="acme")
        
        # When message detected in Slack
        result = await service.process_expense_message(message_text, user_id, channel_id, ts)
    """
    
    # Patterns to detect expense requests
    EXPENSE_PATTERNS = [
        r"(?:spent|paid|cost|charged)\s*\$?([\d,]+\.?\d*)",
        r"\$?([\d,]+\.?\d*)\s*(?:expense|reimbursement|receipt)",
        r"(?:need|want|requesting)\s*(?:reimbursement|expense)",
        r"(?:business\s*)?(?:lunch|dinner|travel|uber|lyft|flight|hotel|supplies)",
    ]
    
    # Category detection patterns
    CATEGORY_PATTERNS = {
        "meals": r"lunch|dinner|breakfast|meal|food|coffee|restaurant",
        "travel": r"uber|lyft|taxi|flight|airfare|hotel|airbnb|travel|gas|mileage",
        "supplies": r"office|supplies|equipment|hardware|software",
        "software": r"subscription|saas|software|license|tool",
        "other": r"expense|reimbursement|cost|paid|spent",
    }
    
    def __init__(
        self,
        organization_id: str,
        approval_channel: Optional[str] = None,
    ):
        self.organization_id = organization_id
        self.approval_channel = approval_channel or "#expense-approvals"
        self.db = get_db()
        self._slack_client: Optional[SlackAPIClient] = None
    
    @property
    def slack_client(self) -> SlackAPIClient:
        if self._slack_client is None:
            self._slack_client = get_slack_client()
        return self._slack_client
    
    def is_expense_request(self, message: str) -> bool:
        """Check if a Slack message is an expense request."""
        message_lower = message.lower()
        
        # Check for expense-related patterns
        for pattern in self.EXPENSE_PATTERNS:
            if re.search(pattern, message_lower):
                return True
        
        return False
    
    def extract_expense_data(self, message: str) -> Dict[str, Any]:
        """Extract expense data from a Slack message."""
        message_lower = message.lower()
        
        result = {
            "amount": None,
            "currency": "USD",
            "category": "other",
            "description": message[:200],
        }
        
        # Extract amount
        amount_patterns = [
            r"\$\s*([\d,]+\.?\d*)",
            r"([\d,]+\.?\d*)\s*(?:dollars?|usd)",
            r"(?:spent|paid|cost|charged)\s*\$?([\d,]+\.?\d*)",
        ]
        
        for pattern in amount_patterns:
            match = re.search(pattern, message_lower)
            if match:
                amount_str = match.group(1).replace(",", "")
                try:
                    result["amount"] = float(amount_str)
                    break
                except ValueError:
                    pass
        
        # Extract currency
        if "€" in message or "eur" in message_lower:
            result["currency"] = "EUR"
        elif "£" in message or "gbp" in message_lower:
            result["currency"] = "GBP"
        
        # Detect category
        for category, pattern in self.CATEGORY_PATTERNS.items():
            if re.search(pattern, message_lower):
                result["category"] = category
                break
        
        return result
    
    async def process_expense_message(
        self,
        message_text: str,
        user_id: str,
        channel_id: str,
        message_ts: str,
        files: Optional[List[Dict]] = None,
    ) -> Dict[str, Any]:
        """
        Process a potential expense request from Slack.
        
        Returns:
            Dict with status and details
        """
        # Check if this is an expense request
        if not self.is_expense_request(message_text):
            return {"status": "not_expense", "message": "Not an expense request"}
        
        # Extract expense data
        expense_data = self.extract_expense_data(message_text)
        
        # Check for receipt attachment
        receipt_url = None
        if files:
            for file in files:
                if file.get("mimetype", "").startswith("image/"):
                    receipt_url = file.get("url_private")
                    break
        
        # Create expense request
        expense = ExpenseRequest(
            slack_user_id=user_id,
            slack_channel_id=channel_id,
            slack_message_ts=message_ts,
            message_text=message_text,
            amount=expense_data.get("amount"),
            currency=expense_data.get("currency", "USD"),
            category=expense_data.get("category"),
            description=expense_data.get("description"),
            receipt_url=receipt_url,
            organization_id=self.organization_id,
        )
        
        # Save to database
        expense_id = self._save_expense(expense)
        
        # Send for approval
        result = await self._send_for_approval(expense, expense_id)
        
        # React to original message to show we're processing
        try:
            await self.slack_client.add_reaction(channel_id, message_ts, "eyes")
        except:
            pass
        
        return result
    
    def _save_expense(self, expense: ExpenseRequest) -> str:
        """Save expense request to database."""
        import uuid
        expense_id = str(uuid.uuid4())
        
        # Use the invoice_status table for now (could create dedicated expense table)
        self.db.save_invoice_status(
            gmail_id=f"slack:{expense.slack_message_ts}",  # Use Slack ts as ID
            status="pending_approval",
            email_subject=f"Expense: {expense.category}",
            vendor=f"Employee: {expense.slack_user_id}",
            amount=expense.amount,
            currency=expense.currency,
            organization_id=self.organization_id,
        )
        
        return expense_id
    
    async def _send_for_approval(
        self,
        expense: ExpenseRequest,
        expense_id: str,
    ) -> Dict[str, Any]:
        """Send expense request to approval channel."""
        
        # Get user info
        user_info = await self.slack_client.get_user_info(expense.slack_user_id)
        user_name = user_info.get("real_name", expense.slack_user_id) if user_info else expense.slack_user_id
        
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "Expense Reimbursement Request"
                }
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Employee:*\n<@{expense.slack_user_id}>"},
                    {"type": "mrkdwn", "text": f"*Amount:*\n{expense.currency} {expense.amount:,.2f}" if expense.amount else "*Amount:*\nNot specified"},
                    {"type": "mrkdwn", "text": f"*Category:*\n{expense.category.title()}"},
                    {"type": "mrkdwn", "text": f"*Receipt:*\n{'Attached' if expense.receipt_url else 'Missing'}"},
                ]
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Description:*\n>{expense.description[:200]}"
                }
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"<https://slack.com/app_redirect?channel={expense.slack_channel_id}&message_ts={expense.slack_message_ts}|View original message>"
                    }
                ]
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Approve"},
                        "style": "primary",
                        "action_id": f"approve_expense_{expense_id}",
                        "value": expense_id,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Reject"},
                        "style": "danger",
                        "action_id": f"reject_expense_{expense_id}",
                        "value": expense_id,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Need Receipt"},
                        "action_id": f"need_receipt_{expense_id}",
                        "value": expense_id,
                    },
                ]
            }
        ]
        
        try:
            message = await self.slack_client.send_message(
                channel=self.approval_channel,
                text=f"Expense request from {user_name}: {expense.currency} {expense.amount:,.2f}" if expense.amount else f"Expense request from {user_name}",
                blocks=blocks,
            )
            
            logger.info(f"Sent expense approval request: {expense_id}")
            
            return {
                "status": "pending_approval",
                "expense_id": expense_id,
                "slack_channel": message.channel,
                "slack_ts": message.ts,
            }
            
        except Exception as e:
            logger.error(f"Failed to send expense approval: {e}")
            return {"status": "error", "error": str(e)}
    
    async def approve_expense(
        self,
        expense_id: str,
        approved_by: str,
    ) -> Dict[str, Any]:
        """Approve an expense and post to ERP."""
        from clearledgr.integrations.erp_router import (
            Bill, Vendor, post_bill, get_or_create_vendor
        )
        
        # Get expense data
        expense_data = self.db.get_invoice_status(f"slack:{expense_id}")
        if not expense_data:
            return {"status": "error", "reason": "Expense not found"}
        
        # For expenses, we typically post as a bill from "Employee Reimbursement" vendor
        # or directly as an expense entry
        
        vendor = Vendor(
            name="Employee Reimbursements",
            currency=expense_data.get("currency", "USD"),
        )
        
        vendor_result = await get_or_create_vendor(self.organization_id, vendor)
        
        if vendor_result.get("status") == "error":
            return vendor_result
        
        bill = Bill(
            vendor_id=vendor_result.get("vendor_id"),
            vendor_name="Employee Reimbursements",
            amount=expense_data.get("amount", 0),
            currency=expense_data.get("currency", "USD"),
            description=expense_data.get("email_subject", "Employee Expense"),
            line_items=[{
                "description": expense_data.get("email_subject"),
                "amount": expense_data.get("amount", 0),
                "account_id": "6200",  # Typical expense account
            }]
        )
        
        result = await post_bill(self.organization_id, bill)
        
        if result.get("status") == "success":
            self.db.update_invoice_status(
                gmail_id=f"slack:{expense_id}",
                status="posted",
                approved_by=approved_by,
                erp_bill_id=result.get("bill_id"),
            )
        
        return result


def get_expense_workflow(organization_id: str) -> ExpenseWorkflowService:
    """Get expense workflow service instance."""
    return ExpenseWorkflowService(organization_id=organization_id)
