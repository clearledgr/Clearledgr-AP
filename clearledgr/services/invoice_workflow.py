"""
Invoice Workflow Service

Orchestrates the complete invoice lifecycle:
Gmail Detection → Data Extraction → Slack Approval → ERP Posting

This is the heart of "Streak for Finance" - bringing AP workflow into the tools
finance teams already use.
"""

import logging
from typing import Any, Dict, Optional
from datetime import datetime
from dataclasses import dataclass

from clearledgr.core.database import get_db
from clearledgr.services.slack_api import SlackAPIClient, get_slack_client
from clearledgr.integrations.erp_router import (
    Bill, Vendor, post_bill, get_or_create_vendor, get_erp_connection
)
from clearledgr.services.learning import get_learning_service

logger = logging.getLogger(__name__)


@dataclass
class InvoiceData:
    """Extracted invoice data from email."""
    gmail_id: str
    subject: str
    sender: str
    vendor_name: str
    amount: float
    currency: str = "USD"
    invoice_number: Optional[str] = None
    due_date: Optional[str] = None
    po_number: Optional[str] = None
    confidence: float = 0.0
    attachment_url: Optional[str] = None
    organization_id: Optional[str] = None
    user_id: Optional[str] = None
    # Raw invoice text for discount detection
    invoice_text: Optional[str] = None
    # Agent reasoning (added 2026-01-23)
    reasoning_summary: Optional[str] = None
    reasoning_factors: Optional[list] = None
    reasoning_risks: Optional[list] = None
    # Full intelligence (added 2026-01-23)
    vendor_intelligence: Optional[Dict] = None
    policy_compliance: Optional[Dict] = None
    priority: Optional[Dict] = None
    budget_impact: Optional[list] = None
    potential_duplicates: int = 0
    insights: Optional[list] = None


class InvoiceWorkflowService:
    """
    Manages the complete invoice workflow.
    
    Usage:
        service = InvoiceWorkflowService(organization_id="acme")
        
        # When invoice detected in Gmail
        result = await service.process_new_invoice(invoice_data)
        
        # When approved in Slack
        result = await service.approve_invoice(gmail_id, approved_by="user@acme.com")
        
        # When rejected in Slack
        result = await service.reject_invoice(gmail_id, reason="Duplicate", rejected_by="user@acme.com")
    """
    
    def __init__(
        self,
        organization_id: str,
        slack_channel: Optional[str] = None,
        auto_approve_threshold: float = 0.95,
    ):
        self.organization_id = organization_id
        self._slack_channel = slack_channel
        self._auto_approve_threshold = auto_approve_threshold
        self.db = get_db()
        self._slack_client: Optional[SlackAPIClient] = None
        self._settings_loaded = False
        self._settings: Optional[Dict] = None
    
    def _load_settings(self):
        """Load organization settings if not already loaded."""
        if self._settings_loaded:
            return
        
        try:
            org = self.db.get_organization(self.organization_id)
            if org:
                settings = org.get("settings", {})
                if isinstance(settings, str):
                    import json
                    settings = json.loads(settings) if settings else {}
                self._settings = settings
        except:
            self._settings = {}
        
        self._settings_loaded = True
    
    @property
    def slack_channel(self) -> str:
        """Get Slack channel, using settings if available."""
        if self._slack_channel:
            return self._slack_channel
        
        self._load_settings()
        if self._settings:
            channels = self._settings.get("slack_channels", {})
            return channels.get("invoices", "#finance-approvals")
        
        return "#finance-approvals"
    
    @property
    def auto_approve_threshold(self) -> float:
        """Get auto-approve threshold from settings."""
        self._load_settings()
        if self._settings:
            return self._settings.get("auto_approve_threshold", self._auto_approve_threshold)
        return self._auto_approve_threshold
    
    def get_approval_channel_for_amount(self, amount: float) -> str:
        """Get appropriate Slack channel based on amount thresholds."""
        self._load_settings()
        
        if not self._settings:
            return self.slack_channel
        
        thresholds = self._settings.get("approval_thresholds", [])
        
        for threshold in thresholds:
            min_amt = threshold.get("min_amount", 0)
            max_amt = threshold.get("max_amount")
            
            if amount >= min_amt and (max_amt is None or amount < max_amt):
                return threshold.get("approver_channel", self.slack_channel)
        
        return self.slack_channel
    
    @property
    def slack_client(self) -> SlackAPIClient:
        """Lazy-load Slack client."""
        if self._slack_client is None:
            self._slack_client = get_slack_client()
        return self._slack_client
    
    async def process_new_invoice(self, invoice: InvoiceData) -> Dict[str, Any]:
        """
        Process a newly detected invoice email.
        
        Flow:
        1. Save invoice to database with 'new' status
        2. Check if recurring (subscription) - different handling
        3. If confidence >= threshold, auto-approve and post
        4. Otherwise, send to Slack for approval
        
        Returns:
            Dict with status, invoice_id, and action taken
        """
        existing = self.db.get_invoice_status(invoice.gmail_id)
        if existing:
            if existing.get("status") == "posted":
                return {
                    "status": "already_posted",
                    "invoice_id": invoice.gmail_id,
                    "erp_bill_id": existing.get("erp_bill_id"),
                }
            if existing.get("status") == "pending_approval" and existing.get("slack_thread_id"):
                thread = self.db.get_slack_thread(invoice.gmail_id)
                return {
                    "status": "pending_approval",
                    "invoice_id": invoice.gmail_id,
                    "slack_channel": thread.get("channel_id") if thread else None,
                    "slack_ts": thread.get("thread_ts") if thread else None,
                    "existing": True,
                }

        # Check for recurring pattern first
        from clearledgr.services.recurring_detection import get_recurring_detector
        
        recurring_detector = get_recurring_detector(self.organization_id)
        recurring_analysis = recurring_detector.analyze_invoice(
            vendor=invoice.vendor_name,
            amount=invoice.amount,
            currency=invoice.currency,
            sender_email=invoice.sender,
        )
        
        # Save invoice to database
        invoice_id = self.db.save_invoice_status(
            gmail_id=invoice.gmail_id,
            status="new",
            email_subject=invoice.subject,
            vendor=invoice.vendor_name,
            amount=invoice.amount,
            currency=invoice.currency,
            invoice_number=invoice.invoice_number,
            due_date=invoice.due_date,
            confidence=invoice.confidence,
            organization_id=self.organization_id,
            user_id=invoice.user_id,
        )
        
        logger.info(f"New invoice detected: {invoice.vendor_name} ${invoice.amount} (confidence: {invoice.confidence})")
        
        # LEARNING: Check if we have a learned GL code for this vendor
        suggested_gl = None
        try:
            learning = get_learning_service(self.organization_id)
            suggestion = learning.suggest_gl_code(
                vendor=invoice.vendor_name,
                amount=invoice.amount,
            )
            if suggestion and suggestion.get("confidence", 0) > 0.5:
                suggested_gl = suggestion
                logger.info(f"Learning suggested GL {suggestion.get('gl_code')} for {invoice.vendor_name} (confidence: {suggestion.get('confidence'):.2f})")
                
                # Boost confidence if we've seen this vendor before
                if suggestion.get("confidence", 0) > 0.8:
                    invoice.confidence = min(0.99, invoice.confidence + 0.1)
        except Exception as e:
            logger.warning(f"Failed to get GL suggestion from learning: {e}")
        
        # Handle recurring subscriptions specially
        if recurring_analysis.get("is_recurring"):
            if recurring_analysis.get("auto_approve"):
                logger.info(f"Auto-approving recurring invoice from {invoice.vendor_name}")
                return await self._auto_approve_and_post(
                    invoice, 
                    reason="recurring_match",
                    recurring_info=recurring_analysis.get("pattern")
                )
            elif recurring_analysis.get("alerts"):
                # Has alerts - send for review with context
                return await self._send_for_approval(
                    invoice, 
                    extra_context={
                        "recurring": True,
                        "alerts": recurring_analysis.get("alerts"),
                        "pattern": recurring_analysis.get("pattern"),
                    }
                )
        
        # Check if we should auto-approve based on confidence
        if invoice.confidence >= self.auto_approve_threshold:
            logger.info(f"Auto-approving invoice (confidence {invoice.confidence} >= {self.auto_approve_threshold})")
            return await self._auto_approve_and_post(invoice)
        
        # Send to Slack for approval
        return await self._send_for_approval(invoice)
    
    async def _auto_approve_and_post(
        self, 
        invoice: InvoiceData, 
        reason: str = "high_confidence",
        recurring_info: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """Auto-approve invoice and post to ERP."""
        existing = self.db.get_invoice_status(invoice.gmail_id)
        if existing and existing.get("status") == "posted":
            return {
                "status": "already_posted",
                "invoice_id": invoice.gmail_id,
                "erp_bill_id": existing.get("erp_bill_id"),
            }

        # Update status
        self.db.update_invoice_status(
            gmail_id=invoice.gmail_id,
            status="approved",
            approved_by=f"clearledgr-auto:{reason}",
        )
        
        # Post to ERP
        result = await self._post_to_erp(invoice)
        
        if result.get("status") == "success":
            self.db.update_invoice_status(
                gmail_id=invoice.gmail_id,
                status="posted",
                erp_bill_id=result.get("bill_id"),
                erp_vendor_id=result.get("vendor_id"),
            )
            
            # LEARNING: Record auto-approval to learn vendor→GL mappings
            try:
                learning = get_learning_service(self.organization_id)
                learning.record_approval(
                    vendor=invoice.vendor_name,
                    gl_code=result.get("gl_code", ""),
                    gl_description=result.get("gl_description", "Accounts Payable"),
                    amount=invoice.amount,
                    currency=invoice.currency,
                    was_auto_approved=True,
                    was_corrected=False,
                )
                logger.info(f"Recorded auto-approval for learning: {invoice.vendor_name}")
            except Exception as e:
                logger.warning(f"Failed to record auto-approval for learning: {e}")
            
            # Notify in Slack (informational, not approval)
            try:
                await self._send_posted_notification(invoice, result, reason, recurring_info)
            except Exception as e:
                logger.warning(f"Failed to send Slack notification: {e}")
        
        return {
            "status": "auto_approved",
            "invoice_id": invoice.gmail_id,
            "reason": reason,
            "recurring": recurring_info,
            "erp_result": result,
        }
    
    async def _send_for_approval(
        self, 
        invoice: InvoiceData,
        extra_context: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """Send invoice to Slack for approval."""
        existing_thread = self.db.get_slack_thread(invoice.gmail_id)
        if existing_thread:
            # Ensure status is pending, but avoid duplicate Slack messages
            self.db.update_invoice_status(
                gmail_id=invoice.gmail_id,
                status="pending_approval",
                slack_thread_id=existing_thread.get("id"),
            )
            return {
                "status": "pending_approval",
                "invoice_id": invoice.gmail_id,
                "slack_channel": existing_thread.get("channel_id"),
                "slack_ts": existing_thread.get("thread_ts"),
                "existing": True,
            }

        # Update status to pending
        self.db.update_invoice_status(
            gmail_id=invoice.gmail_id,
            status="pending_approval",
        )
        
        # Build approval message
        blocks = self._build_approval_blocks(invoice, extra_context)
        
        # Get appropriate channel based on amount
        approval_channel = self.get_approval_channel_for_amount(invoice.amount)
        
        try:
            # Send to Slack
            message = await self.slack_client.send_message(
                channel=approval_channel,
                text=f"Invoice approval needed: {invoice.vendor_name} - ${invoice.amount:,.2f}",
                blocks=blocks,
            )
            
            # Save Slack thread reference
            thread_id = self.db.save_slack_thread(
                invoice_id=invoice.gmail_id,
                channel_id=message.channel,
                thread_ts=message.ts,
                gmail_id=invoice.gmail_id,
                organization_id=self.organization_id,
            )
            
            # Update invoice with thread reference
            self.db.update_invoice_status(
                gmail_id=invoice.gmail_id,
                status="pending_approval",
                slack_thread_id=thread_id,
            )
            
            logger.info(f"Sent approval request to Slack: {message.ts}")
            
            return {
                "status": "pending_approval",
                "invoice_id": invoice.gmail_id,
                "slack_channel": message.channel,
                "slack_ts": message.ts,
            }
            
        except Exception as e:
            logger.error(f"Failed to send Slack approval: {e}")
            return {
                "status": "error",
                "invoice_id": invoice.gmail_id,
                "error": str(e),
            }
    
    def _build_approval_blocks(
        self, 
        invoice: InvoiceData,
        extra_context: Optional[Dict] = None,
    ) -> list:
        """Build Slack Block Kit blocks for approval request with full intelligence."""
        # Confidence indicator
        if invoice.confidence >= 0.9:
            confidence_text = "High"
        elif invoice.confidence >= 0.7:
            confidence_text = "Medium"
        else:
            confidence_text = "Low"
        
        # Priority from intelligence
        priority_text = ""
        if invoice.priority:
            priority_text = invoice.priority.get("priority_label", "")
        
        # Due date warning
        due_warning = ""
        days_until = invoice.priority.get("days_until_due") if invoice.priority else None
        if days_until is not None:
            if days_until < 0:
                due_warning = f" OVERDUE by {abs(days_until)} days"
            elif days_until == 0:
                due_warning = " DUE TODAY"
            elif days_until <= 3:
                due_warning = f" Due in {days_until} days"
        elif invoice.due_date:
            try:
                due = datetime.strptime(invoice.due_date, "%Y-%m-%d")
                days_until = (due - datetime.now()).days
                if days_until < 0:
                    due_warning = f" OVERDUE by {abs(days_until)} days"
                elif days_until <= 3:
                    due_warning = f" Due in {days_until} days"
            except:
                pass
        
        # Header - indicate priority and if recurring
        is_recurring = extra_context.get("recurring") if extra_context else False
        if is_recurring:
            header_text = "Recurring Invoice - Review Required"
        elif priority_text == "URGENT":
            header_text = "URGENT Invoice Approval Required"
        else:
            header_text = "Invoice Approval Required"
        
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": header_text
                }
            },
        ]
        
        # ========== VENDOR INTELLIGENCE ==========
        vendor_display = invoice.vendor_name
        vendor_intel = invoice.vendor_intelligence
        if vendor_intel:
            blocks.append({
                "type": "context",
                "elements": [{
                    "type": "mrkdwn",
                    "text": f"*Known vendor:* {vendor_intel.get('category', '')} > {vendor_intel.get('subcategory', '')} | Suggested GL: {vendor_intel.get('suggested_gl', 'N/A')}"
                }]
            })
        
        # Main invoice details
        blocks.append({
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Vendor:*\n{vendor_display}"},
                {"type": "mrkdwn", "text": f"*Amount:*\n{invoice.currency} {invoice.amount:,.2f}"},
                {"type": "mrkdwn", "text": f"*Invoice #:*\n{invoice.invoice_number or 'N/A'}"},
                {"type": "mrkdwn", "text": f"*Due Date:*\n{invoice.due_date or 'N/A'}{due_warning}"},
            ]
        })
        
        # Priority and Confidence
        blocks.append({
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Priority:*\n{priority_text}" if priority_text else f"*Confidence:*\n{confidence_text}"},
                {"type": "mrkdwn", "text": f"*Confidence:*\n{confidence_text} ({invoice.confidence*100:.0f}%)"},
            ]
        })
        
        # ========== POLICY COMPLIANCE ==========
        if invoice.policy_compliance and not invoice.policy_compliance.get("compliant", True):
            policy_lines = []
            for violation in invoice.policy_compliance.get("violations", [])[:3]:
                policy_lines.append(f"{violation.get('message', '')}")
            
            if policy_lines:
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Policy Requirements:*\n" + "\n".join(policy_lines)
                    }
                })
            
            # Required approvers
            approvers = invoice.policy_compliance.get("required_approvers", [])
            if approvers:
                blocks.append({
                    "type": "context",
                    "elements": [{
                        "type": "mrkdwn",
                        "text": f"*Required approvers:* {', '.join(['@' + a for a in approvers])}"
                    }]
                })
        
        # ========== BUDGET IMPACT ==========
        if invoice.budget_impact:
            budget_warnings = []
            for budget in invoice.budget_impact[:2]:
                status = budget.get("after_approval_status", "healthy")
                if status in ["critical", "exceeded"]:
                    pct = budget.get("after_approval_percent", 0)
                    name = budget.get("budget_name", "Budget")
                    remaining = budget.get("budget_amount", 0) - budget.get("after_approval", 0)
                    severity = "CRITICAL" if status == "exceeded" else "WARNING"
                    budget_warnings.append(f"{severity}: {name}: {pct:.0f}% (${remaining:,.0f} remaining)")
            
            if budget_warnings:
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Budget Impact:*\n" + "\n".join(budget_warnings)
                    }
                })
        
        # ========== DUPLICATE WARNING ==========
        if invoice.potential_duplicates and invoice.potential_duplicates > 0:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Potential Duplicate:* Found {invoice.potential_duplicates} similar invoice(s)"
                }
            })
        
        # ========== INSIGHTS ==========
        if invoice.insights:
            insight_lines = [f"{i.get('title', '')}" for i in invoice.insights[:2]]
            if insight_lines:
                blocks.append({
                    "type": "context",
                    "elements": [{
                        "type": "mrkdwn",
                        "text": "\n".join(insight_lines)
                    }]
                })
        
        # Add recurring alerts if present
        if extra_context and extra_context.get("alerts"):
            alert_texts = []
            for alert in extra_context.get("alerts", []):
                severity_label = {
                    "high": "HIGH",
                    "warning": "WARNING",
                    "info": "INFO",
                }.get(alert.get("severity", "info"), "INFO")
                alert_texts.append(f"[{severity_label}] {alert.get('message')}")
            
            if alert_texts:
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Alerts:*\n" + "\n".join(alert_texts)
                    }
                })
        
        # Add recurring pattern info
        if extra_context and extra_context.get("pattern"):
            pattern = extra_context.get("pattern")
            blocks.append({
                "type": "context",
                "elements": [{
                    "type": "mrkdwn",
                    "text": f"Recurring: Usually {invoice.currency} {pattern.get('typical_amount', 0):,.2f} every ~{pattern.get('frequency_days', 30)} days ({pattern.get('invoice_count', 0)} previous invoices)"
                }]
            })
        
        # Add agent reasoning if available
        if invoice.reasoning_summary:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*AI Analysis:* {invoice.reasoning_summary}"
                }
            })
        
        # Add reasoning factors
        if invoice.reasoning_factors:
            factor_lines = []
            for f in invoice.reasoning_factors[:4]:
                score = f.get("score", 0)
                if score >= 0.8:
                    indicator = "HIGH"
                elif score >= 0.5:
                    indicator = "MEDIUM"
                else:
                    indicator = "LOW"
                factor_lines.append(f"[{indicator}] {f.get('detail', '')}")
            
            if factor_lines:
                blocks.append({
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn", "text": "\n".join(factor_lines)}
                    ]
                })
        
        # Add risks if present
        if invoice.reasoning_risks:
            risk_lines = [f"{risk}" for risk in invoice.reasoning_risks[:3]]
            if risk_lines:
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Risks:*\n" + "\n".join(risk_lines)
                    }
                })
        
        # Footer and actions
        blocks.extend([
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"From: {invoice.sender} | Subject: {invoice.subject[:50]}..."}
                ]
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Post to ERP"},
                        "style": "primary",
                        "action_id": f"post_to_erp_{invoice.gmail_id}",
                        "value": invoice.gmail_id,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Reject"},
                        "style": "danger",
                        "action_id": f"reject_invoice_{invoice.gmail_id}",
                        "value": invoice.gmail_id,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "View in Gmail"},
                        "action_id": f"view_invoice_{invoice.gmail_id}",
                        "url": f"https://mail.google.com/mail/u/0/#search/{invoice.gmail_id}",
                    },
                ]
            }
        ])
        
        return blocks
    
    async def approve_invoice(
        self,
        gmail_id: str,
        approved_by: str,
        slack_channel: Optional[str] = None,
        slack_ts: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Approve an invoice and post to ERP.
        
        Called when user clicks Approve in Slack or Gmail extension.
        """
        # Get invoice data
        invoice_data = self.db.get_invoice_status(gmail_id)
        if not invoice_data:
            return {"status": "error", "reason": "Invoice not found"}
        
        if invoice_data.get("status") == "posted":
            return {"status": "error", "reason": "Invoice already posted"}
        if invoice_data.get("erp_bill_id"):
            return {"status": "error", "reason": "Invoice already posted"}
        
        # Update status to approved
        self.db.update_invoice_status(
            gmail_id=gmail_id,
            status="approved",
            approved_by=approved_by,
        )
        
        # Build invoice object for ERP
        invoice = InvoiceData(
            gmail_id=gmail_id,
            subject=invoice_data.get("email_subject", ""),
            sender="",
            vendor_name=invoice_data.get("vendor", "Unknown"),
            amount=invoice_data.get("amount", 0),
            currency=invoice_data.get("currency", "USD"),
            invoice_number=invoice_data.get("invoice_number"),
            due_date=invoice_data.get("due_date"),
            organization_id=self.organization_id,
            invoice_text=invoice_data.get("email_body", ""),  # For discount detection
        )
        
        # Post to ERP
        result = await self._post_to_erp(invoice)
        
        if result.get("status") == "success":
            self.db.update_invoice_status(
                gmail_id=gmail_id,
                status="posted",
                erp_bill_id=result.get("bill_id"),
                erp_vendor_id=result.get("vendor_id"),
            )
            
            # LEARNING: Record this approval to learn vendor→GL mappings
            try:
                learning = get_learning_service(self.organization_id)
                learning.record_approval(
                    vendor=invoice.vendor_name,
                    gl_code=result.get("gl_code", ""),
                    gl_description=result.get("gl_description", "Accounts Payable"),
                    amount=invoice.amount,
                    currency=invoice.currency,
                    was_auto_approved=False,
                    was_corrected=False,  # TODO: Track if user changed suggested GL
                )
                logger.info(f"Recorded approval for learning: {invoice.vendor_name} → GL {result.get('gl_code')}")
            except Exception as e:
                logger.warning(f"Failed to record approval for learning: {e}")
            
            # Update Slack message
            if slack_channel and slack_ts:
                await self._update_slack_approved(
                    slack_channel, slack_ts, invoice, approved_by, result
                )
        else:
            # Revert to pending if ERP post failed
            self.db.update_invoice_status(
                gmail_id=gmail_id,
                status="pending_approval",
            )
        
        return {
            "status": "approved" if result.get("status") == "success" else "error",
            "invoice_id": gmail_id,
            "approved_by": approved_by,
            "erp_result": result,
        }
    
    async def reject_invoice(
        self,
        gmail_id: str,
        reason: str,
        rejected_by: str,
        slack_channel: Optional[str] = None,
        slack_ts: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Reject an invoice with reason."""
        invoice_data = self.db.get_invoice_status(gmail_id)
        if not invoice_data:
            return {"status": "error", "reason": "Invoice not found"}
        
        # Update status
        self.db.update_invoice_status(
            gmail_id=gmail_id,
            status="rejected",
            rejection_reason=reason,
        )
        
        # Update Slack thread status
        thread = self.db.get_slack_thread(gmail_id)
        if thread:
            self.db.update_slack_thread_status(
                thread_id=thread["id"],
                status="rejected",
                rejection_reason=reason,
            )
        
        # Update Slack message
        if slack_channel and slack_ts:
            await self._update_slack_rejected(
                slack_channel, slack_ts, invoice_data, rejected_by, reason
            )
        
        logger.info(f"Invoice rejected: {gmail_id} by {rejected_by} - {reason}")
        
        return {
            "status": "rejected",
            "invoice_id": gmail_id,
            "rejected_by": rejected_by,
            "reason": reason,
        }
    
    async def _post_to_erp(self, invoice: InvoiceData) -> Dict[str, Any]:
        """Post approved invoice to ERP as a Bill."""
        # First, get or create vendor
        vendor = Vendor(
            name=invoice.vendor_name,
            currency=invoice.currency,
        )
        
        vendor_result = await get_or_create_vendor(self.organization_id, vendor)
        
        if vendor_result.get("status") == "error":
            logger.error(f"Failed to get/create vendor: {vendor_result}")
            return vendor_result
        
        vendor_id = vendor_result.get("vendor_id")
        
        # Create and post bill
        bill = Bill(
            vendor_id=vendor_id,
            vendor_name=invoice.vendor_name,
            amount=invoice.amount,
            currency=invoice.currency,
            invoice_number=invoice.invoice_number,
            invoice_date=datetime.now().strftime("%Y-%m-%d"),
            due_date=invoice.due_date,
            description=f"Invoice from {invoice.vendor_name}",
            po_number=invoice.po_number,
        )
        
        result = await post_bill(self.organization_id, bill)
        
        if result.get("status") == "success":
            result["vendor_id"] = vendor_id
            logger.info(f"Posted bill to ERP: {result.get('bill_id')}")
            
            # Auto-schedule payment based on due date
            await self._schedule_payment(invoice, vendor_id, result.get('bill_id'))
        
        return result
    
    async def _schedule_payment(
        self,
        invoice: InvoiceData,
        vendor_id: str,
        erp_bill_id: str,
    ) -> None:
        """Auto-schedule payment after successful ERP posting."""
        try:
            from clearledgr.core.database import get_db
            from clearledgr.services.early_payment_discounts import get_discount_service
            
            db = get_db()
            
            # Check for early payment discount opportunity
            discount_service = get_discount_service(self.organization_id)
            discount = None
            
            if invoice.invoice_text:
                discount = discount_service.detect_discount(
                    invoice_id=invoice.gmail_id,
                    invoice_text=invoice.invoice_text,
                    vendor_name=invoice.vendor_name,
                    amount=invoice.amount,
                )
            
            # Determine payment date
            if discount and discount.discount_percent > 0:
                # Schedule for discount deadline to capture savings
                payment_date = discount.discount_deadline.isoformat() if discount.discount_deadline else None
                payment_note = f"Early payment discount: {discount.discount_percent}% if paid by {payment_date}"
                logger.info(f"Scheduling payment for discount capture: {invoice.vendor_name} - save {discount.discount_percent}%")
            else:
                # Schedule for due date
                payment_date = invoice.due_date
                payment_note = "Standard payment terms"
            
            # Create payment record
            payment = db.save_ap_payment(
                invoice_id=invoice.gmail_id,
                vendor_id=vendor_id,
                vendor_name=invoice.vendor_name,
                amount=invoice.amount,
                currency=invoice.currency,
                method="ach",  # Default to ACH
                status="scheduled",
                organization_id=self.organization_id,
            )
            
            # Update payment with scheduled date
            if payment_date:
                db.update_ap_payment(
                    payment['payment_id'],
                    scheduled_date=payment_date,
                )
            
            logger.info(f"Payment scheduled: {payment['payment_id']} for {invoice.vendor_name} - ${invoice.amount}")
            
            # Log to audit trail
            self.audit.log(
                invoice_id=invoice.gmail_id,
                event_type="PAYMENT_SCHEDULED",
                actor="clearledgr-auto",
                details={
                    "payment_id": payment['payment_id'],
                    "vendor": invoice.vendor_name,
                    "amount": invoice.amount,
                    "scheduled_date": payment_date,
                    "erp_bill_id": erp_bill_id,
                    "discount_captured": discount.discount_percent if discount else 0,
                },
            )
            
        except Exception as e:
            logger.warning(f"Failed to schedule payment for {invoice.gmail_id}: {e}")
            # Don't fail the posting - payment can be scheduled manually
    
    async def _send_posted_notification(
        self,
        invoice: InvoiceData,
        erp_result: Dict[str, Any],
        reason: str = "high_confidence",
        recurring_info: Optional[Dict] = None,
    ) -> None:
        """Send notification that invoice was auto-posted with reasoning."""
        # Different message based on reason
        if reason == "recurring_match":
            reason_text = f"Recurring subscription (matched {recurring_info.get('invoice_count', 0)} previous invoices)"
        else:
            # Use agent reasoning if available
            if invoice.reasoning_summary:
                reason_text = f"{invoice.reasoning_summary}"
            else:
                reason_text = f"Auto-approved (confidence: {invoice.confidence*100:.0f}%)"
        
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Invoice Auto-Posted*\n"
                            f"*{invoice.vendor_name}* - {invoice.currency} {invoice.amount:,.2f}\n"
                            f"Bill ID: `{erp_result.get('bill_id')}`"
                }
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": reason_text}
                ]
            }
        ]
        
        # Add reasoning factors if available
        if invoice.reasoning_factors:
            factor_lines = []
            for f in invoice.reasoning_factors[:3]:  # Top 3 factors
                score_value = int(f.get("score", 0) * 5)
                factor_lines.append(f"Score {score_value}/5 - {f.get('detail', '')}")
            
            if factor_lines:
                blocks.append({
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn", "text": "\n".join(factor_lines)}
                    ]
                })
        
        await self.slack_client.send_message(
            channel=self.slack_channel,
            text=f"Invoice auto-posted: {invoice.vendor_name} ${invoice.amount:,.2f}",
            blocks=blocks,
        )
    
    async def _update_slack_approved(
        self,
        channel: str,
        ts: str,
        invoice: InvoiceData,
        approved_by: str,
        erp_result: Dict[str, Any],
    ) -> None:
        """Update Slack message to show approved status."""
        doc_number = erp_result.get("doc_num") or erp_result.get("document_number") or erp_result.get("erp_document")
        bill_id = erp_result.get("bill_id")
        reference_line = f"Bill ID: `{bill_id}`" if bill_id else "Bill posted"
        if doc_number:
            reference_line = f"{reference_line} | Doc #: `{doc_number}`"

        blocks = [
            {
                "type": "section",
                "text": {
                "type": "mrkdwn",
                "text": f"*Invoice Approved & Posted*\n"
                        f"*{invoice.vendor_name}* - {invoice.currency} {invoice.amount:,.2f}\n"
                        f"Invoice #: {invoice.invoice_number or 'N/A'}"
            }
        },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"Approved by {approved_by} | {reference_line}"}
                ]
            }
        ]
        
        try:
            await self.slack_client.update_message(channel, ts, "Invoice approved", blocks)
        except Exception as e:
            logger.warning(f"Failed to update Slack message: {e}")
    
    async def _update_slack_rejected(
        self,
        channel: str,
        ts: str,
        invoice_data: Dict[str, Any],
        rejected_by: str,
        reason: str,
    ) -> None:
        """Update Slack message to show rejected status."""
        blocks = [
            {
                "type": "section",
                "text": {
                "type": "mrkdwn",
                "text": f"*Invoice Rejected*\n"
                        f"*{invoice_data.get('vendor', 'Unknown')}* - {invoice_data.get('currency', 'USD')} {invoice_data.get('amount', 0):,.2f}\n"
                        f"Reason: {reason}"
            }
        },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"Rejected by {rejected_by}"}
                ]
            }
        ]
        
        try:
            await self.slack_client.update_message(channel, ts, "Invoice rejected", blocks)
        except Exception as e:
            logger.warning(f"Failed to update Slack message: {e}")
    
    async def send_exception_alert(
        self,
        invoice: InvoiceData,
        exception_type: str,
        details: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Send exception alert to Slack.
        
        Exception types:
        - duplicate: Potential duplicate invoice detected
        - amount_mismatch: Amount doesn't match PO
        - vendor_unknown: Vendor not in system
        - overdue: Invoice is past due date
        """
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"Exception: {exception_type.replace('_', ' ').title()}"
                }
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Vendor:*\n{invoice.vendor_name}"},
                    {"type": "mrkdwn", "text": f"*Amount:*\n{invoice.currency} {invoice.amount:,.2f}"},
                ]
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Details:*\n{details.get('message', 'No details available')}"
                }
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Review"},
                        "action_id": f"review_exception_{invoice.gmail_id}",
                        "value": invoice.gmail_id,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Dismiss"},
                        "action_id": f"dismiss_exception_{invoice.gmail_id}",
                        "value": invoice.gmail_id,
                    },
                ]
            }
        ]
        
        try:
            message = await self.slack_client.send_message(
                channel=self.slack_channel,
                text=f"Exception: {exception_type} - {invoice.vendor_name}",
                blocks=blocks,
            )
            
            return {
                "status": "sent",
                "channel": message.channel,
                "ts": message.ts,
            }
        except Exception as e:
            logger.error(f"Failed to send exception alert: {e}")
            return {"status": "error", "error": str(e)}


# Convenience function
def get_invoice_workflow(
    organization_id: str,
    slack_channel: Optional[str] = None,
) -> InvoiceWorkflowService:
    """Get an invoice workflow service instance."""
    return InvoiceWorkflowService(
        organization_id=organization_id,
        slack_channel=slack_channel,
    )
