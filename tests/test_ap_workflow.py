"""
Tests for AP Workflow Services

Tests payment execution, GL corrections, and recurring invoice management.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, MagicMock

# Import services
from clearledgr.services.payment_execution import (
    get_payment_execution,
    PaymentMethod,
    PaymentStatus,
    PaymentExecutionService,
)
from clearledgr.services.gl_correction import (
    get_gl_correction,
    GLCorrectionService,
)
from clearledgr.services.recurring_management import (
    get_recurring_management,
    RecurringFrequency,
    RecurringAction,
    RecurringManagementService,
)


class TestPaymentExecution:
    """Tests for PaymentExecutionService."""
    
    def test_create_payment(self):
        """Test creating a new payment."""
        service = get_payment_execution("test-org")
        
        payment = service.create_payment(
            invoice_id="INV-001",
            vendor_id="V001",
            vendor_name="Acme Corp",
            amount=1500.00,
            currency="USD",
            method=PaymentMethod.ACH,
        )
        
        assert payment is not None
        assert payment.invoice_id == "INV-001"
        assert payment.vendor_name == "Acme Corp"
        assert payment.amount == 1500.00
        assert payment.status == PaymentStatus.PENDING
        assert payment.method == PaymentMethod.ACH
    
    def test_schedule_payment(self):
        """Test scheduling a payment for future date."""
        service = get_payment_execution("test-org")
        
        # Create payment first
        payment = service.create_payment(
            invoice_id="INV-002",
            vendor_id="V002",
            vendor_name="Test Vendor",
            amount=500.00,
            method=PaymentMethod.ACH,
        )
        
        # Schedule it
        future_date = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
        scheduled = service.schedule_payment(payment.payment_id, future_date)
        
        assert scheduled.status == PaymentStatus.SCHEDULED
        assert scheduled.scheduled_date == future_date
    
    def test_create_ach_batch(self):
        """Test creating ACH batch with NACHA file."""
        service = get_payment_execution("test-org")
        
        # Create multiple payments
        for i in range(3):
            service.create_payment(
                invoice_id=f"INV-BATCH-{i}",
                vendor_id=f"V-BATCH-{i}",
                vendor_name=f"Vendor {i}",
                amount=100.00 * (i + 1),
                method=PaymentMethod.ACH,
            )
        
        # Create batch
        batch = service.create_ach_batch()
        
        assert batch is not None
        assert batch.batch_id is not None
        assert len(batch.payments) == 3
        assert batch.total_amount == 600.00  # 100 + 200 + 300
        assert batch.file_content is not None
        assert "101" in batch.file_content  # NACHA file header record
    
    def test_payment_status_transitions(self):
        """Test payment status transitions."""
        service = get_payment_execution("test-org")
        
        payment = service.create_payment(
            invoice_id="INV-STATUS",
            vendor_id="V-STATUS",
            vendor_name="Status Test",
            amount=250.00,
            method=PaymentMethod.WIRE,
        )
        
        # Pending -> Sent
        sent = service.mark_payment_sent(payment.payment_id, "CONF-12345")
        assert sent.status == PaymentStatus.SENT
        assert sent.confirmation_number == "CONF-12345"
        
        # Sent -> Completed
        completed = service.mark_payment_completed(payment.payment_id)
        assert completed.status == PaymentStatus.COMPLETED
    
    def test_wire_transfer_instructions(self):
        """Test generating wire transfer instructions."""
        service = get_payment_execution("test-org")
        
        # Save vendor bank info
        service.save_vendor_bank_info(
            vendor_id="V-WIRE",
            bank_info={
                "bank_name": "Chase Bank",
                "routing_number": "021000021",
                "account_number": "123456789",
                "swift_code": "CHASUS33",
            }
        )
        
        payment = service.create_payment(
            invoice_id="INV-WIRE",
            vendor_id="V-WIRE",
            vendor_name="Wire Vendor",
            amount=10000.00,
            method=PaymentMethod.WIRE,
        )
        
        instructions = service.create_wire_request(payment.payment_id)
        
        assert instructions is not None
        assert "bank_name" in instructions
        assert "swift_code" in instructions
        assert instructions["amount"] == 10000.00
    
    def test_check_queue(self):
        """Test adding payment to check printing queue."""
        service = get_payment_execution("test-org")
        
        payment = service.create_payment(
            invoice_id="INV-CHECK",
            vendor_id="V-CHECK",
            vendor_name="Check Vendor",
            amount=750.00,
            method=PaymentMethod.CHECK,
        )
        
        result = service.add_to_check_queue(
            payment.payment_id,
            payee_address="123 Main St, City, ST 12345"
        )
        
        assert result is not None
        assert result["queued"] is True
        assert result["payee_address"] == "123 Main St, City, ST 12345"
    
    def test_payment_summary(self):
        """Test getting payment summary statistics."""
        service = get_payment_execution("test-org-summary")
        
        # Create payments in various states
        service.create_payment(
            invoice_id="INV-S1",
            vendor_id="V1",
            vendor_name="Vendor 1",
            amount=100.00,
            method=PaymentMethod.ACH,
        )
        
        summary = service.get_payment_summary()
        
        assert "pending" in summary
        assert "scheduled" in summary
        assert "processing_amount" in summary


class TestGLCorrection:
    """Tests for GLCorrectionService."""
    
    def test_correct_gl_code(self):
        """Test recording a GL code correction."""
        service = get_gl_correction("test-org")
        
        correction = service.correct_gl_code(
            invoice_id="INV-GL-001",
            vendor="Acme Corp",
            original_gl="5000",
            corrected_gl="5200",
            corrected_by="user@test.com",
            reason="Software subscription, not general expense",
        )
        
        assert correction is not None
        assert correction.original_gl == "5000"
        assert correction.corrected_gl == "5200"
        assert correction.reason == "Software subscription, not general expense"
    
    def test_gl_suggestion(self):
        """Test getting GL code suggestion for vendor."""
        service = get_gl_correction("test-org")
        
        # First, create some corrections to train
        for i in range(3):
            service.correct_gl_code(
                invoice_id=f"INV-TRAIN-{i}",
                vendor="AWS",
                original_gl="5000",
                corrected_gl="5200",  # Software
            )
        
        # Now get suggestion
        suggestion = service.get_suggested_gl(vendor="AWS")
        
        assert suggestion is not None
        assert suggestion["suggested_gl"] == "5200"
        assert suggestion["confidence"] > 0.5
    
    def test_add_gl_account(self):
        """Test adding a new GL account."""
        service = get_gl_correction("test-org")
        
        account = service.add_gl_account(
            code="5999",
            name="Test Account",
            account_type="expense",
            category="Operations",
        )
        
        assert account is not None
        assert account.code == "5999"
        assert account.name == "Test Account"
    
    def test_get_gl_accounts(self):
        """Test retrieving GL accounts with filters."""
        service = get_gl_correction("test-org")
        
        # Add some accounts
        service.add_gl_account("6001", "Revenue - Products", "revenue")
        service.add_gl_account("6002", "Revenue - Services", "revenue")
        service.add_gl_account("5001", "Office Supplies", "expense")
        
        # Get all expense accounts
        expense_accounts = service.get_gl_accounts(account_type="expense")
        assert all(a.account_type == "expense" for a in expense_accounts)
        
        # Search by name
        revenue_accounts = service.get_gl_accounts(search="revenue")
        assert len(revenue_accounts) >= 2
    
    def test_recent_corrections(self):
        """Test getting recent corrections."""
        service = get_gl_correction("test-org-recent")
        
        # Add corrections
        for i in range(5):
            service.correct_gl_code(
                invoice_id=f"INV-RECENT-{i}",
                vendor=f"Vendor {i}",
                original_gl="5000",
                corrected_gl=f"510{i}",
            )
        
        corrections = service.get_recent_corrections(limit=3)
        
        assert len(corrections) == 3
        # Should be in reverse chronological order
    
    def test_correction_stats(self):
        """Test getting correction statistics."""
        service = get_gl_correction("test-org-stats")
        
        # Add some corrections
        service.correct_gl_code("INV-1", "V1", "5000", "5100")
        service.correct_gl_code("INV-2", "V1", "5000", "5100")
        service.correct_gl_code("INV-3", "V2", "5000", "5200")
        
        stats = service.get_correction_stats()
        
        assert "total_corrections" in stats
        assert stats["total_corrections"] >= 3
        assert "accuracy" in stats
        assert "learned_rules" in stats


class TestRecurringManagement:
    """Tests for RecurringManagementService."""
    
    def test_create_rule(self):
        """Test creating a recurring rule."""
        service = get_recurring_management("test-org")
        
        rule = service.create_rule(
            vendor="Adobe",
            expected_frequency=RecurringFrequency.MONTHLY,
            expected_amount=99.99,
            amount_tolerance_pct=5.0,
            action=RecurringAction.AUTO_APPROVE,
            default_gl_code="5200",
            vendor_aliases=["ADOBE SYSTEMS", "Adobe Inc"],
        )
        
        assert rule is not None
        assert rule.vendor == "Adobe"
        assert rule.expected_frequency == RecurringFrequency.MONTHLY
        assert rule.expected_amount == 99.99
        assert rule.action == RecurringAction.AUTO_APPROVE
        assert "ADOBE SYSTEMS" in rule.vendor_aliases
    
    def test_process_invoice_matches_rule(self):
        """Test processing an invoice that matches a rule."""
        service = get_recurring_management("test-org")
        
        # Create rule
        service.create_rule(
            vendor="Slack",
            expected_frequency=RecurringFrequency.MONTHLY,
            expected_amount=12.50,
            amount_tolerance_pct=5.0,
            action=RecurringAction.AUTO_APPROVE,
        )
        
        # Process invoice
        result = service.process_invoice(
            invoice_id="INV-SLACK-001",
            vendor="Slack",
            amount=12.50,
        )
        
        assert result is not None
        assert result.matched is True
        assert result.action == RecurringAction.AUTO_APPROVE
    
    def test_process_invoice_amount_variance(self):
        """Test invoice with amount outside tolerance."""
        service = get_recurring_management("test-org")
        
        # Create rule with 5% tolerance
        service.create_rule(
            vendor="AWS",
            expected_frequency=RecurringFrequency.MONTHLY,
            expected_amount=100.00,
            amount_tolerance_pct=5.0,
            action=RecurringAction.AUTO_APPROVE,
        )
        
        # Process invoice with 15% variance (should flag for review)
        result = service.process_invoice(
            invoice_id="INV-AWS-VAR",
            vendor="AWS",
            amount=115.00,  # 15% above expected
        )
        
        assert result.matched is True
        assert result.amount_variance_pct > 5.0
        # Should flag for review due to variance
    
    def test_find_matching_rule_by_alias(self):
        """Test finding rule by vendor alias."""
        service = get_recurring_management("test-org")
        
        # Create rule with aliases
        service.create_rule(
            vendor="Microsoft",
            expected_frequency=RecurringFrequency.MONTHLY,
            expected_amount=9.99,
            vendor_aliases=["MSFT", "Microsoft Corp", "MICROSOFT CORPORATION"],
        )
        
        # Find by alias
        rule = service.find_matching_rule("MICROSOFT CORPORATION")
        
        assert rule is not None
        assert rule.vendor == "Microsoft"
    
    def test_get_upcoming_invoices(self):
        """Test getting expected upcoming invoices."""
        service = get_recurring_management("test-org")
        
        # Create monthly rule (should have upcoming)
        service.create_rule(
            vendor="Notion",
            expected_frequency=RecurringFrequency.MONTHLY,
            expected_amount=10.00,
            action=RecurringAction.AUTO_APPROVE,
        )
        
        # Get upcoming (next 30 days)
        upcoming = service.get_upcoming_invoices(days=30)
        
        assert upcoming is not None
        # Should include Notion
    
    def test_subscription_summary(self):
        """Test getting subscription summary."""
        service = get_recurring_management("test-org-summary")
        
        # Create various rules
        service.create_rule("Service A", RecurringFrequency.MONTHLY, 50.00)
        service.create_rule("Service B", RecurringFrequency.MONTHLY, 100.00)
        service.create_rule("Service C", RecurringFrequency.ANNUAL, 1200.00)
        
        summary = service.get_subscription_summary()
        
        assert "active_rules" in summary
        assert "monthly_spend" in summary
        assert summary["active_rules"] >= 3
    
    def test_update_rule(self):
        """Test updating a recurring rule."""
        service = get_recurring_management("test-org")
        
        # Create rule
        rule = service.create_rule(
            vendor="Test Service",
            expected_frequency=RecurringFrequency.MONTHLY,
            expected_amount=50.00,
        )
        
        # Update it
        updated = service.update_rule(rule.rule_id, {
            "expected_amount": 60.00,
            "action": "flag_for_review",
        })
        
        assert updated.expected_amount == 60.00
        assert updated.action == RecurringAction.FLAG_FOR_REVIEW
    
    def test_delete_rule(self):
        """Test deleting a recurring rule."""
        service = get_recurring_management("test-org")
        
        # Create rule
        rule = service.create_rule(
            vendor="Temp Service",
            expected_frequency=RecurringFrequency.MONTHLY,
            expected_amount=25.00,
        )
        
        # Delete it
        result = service.delete_rule(rule.rule_id)
        assert result is True
        
        # Should not find it anymore
        found = service.get_rule(rule.rule_id)
        assert found is None or found.enabled is False
    
    def test_detect_recurring_pattern(self):
        """Test detecting recurring pattern from invoice history."""
        service = get_recurring_management("test-org")
        
        # Provide invoice history
        invoices = [
            {"date": "2024-01-15", "amount": 99.00},
            {"date": "2024-02-15", "amount": 99.00},
            {"date": "2024-03-15", "amount": 99.00},
            {"date": "2024-04-15", "amount": 99.00},
        ]
        
        suggestion = service.detect_new_recurring(
            vendor="Consistent Vendor",
            invoices=invoices,
        )
        
        if suggestion:
            assert suggestion["detected_frequency"] == "monthly"
            assert suggestion["detected_amount"] == 99.00


class TestIntegration:
    """Integration tests for AP workflow."""
    
    def test_full_payment_flow(self):
        """Test complete payment flow from invoice to completion."""
        payment_service = get_payment_execution("test-integration")
        
        # 1. Create payment
        payment = payment_service.create_payment(
            invoice_id="INV-FLOW-001",
            vendor_id="V-FLOW",
            vendor_name="Flow Test Vendor",
            amount=5000.00,
            method=PaymentMethod.ACH,
        )
        assert payment.status == PaymentStatus.PENDING
        
        # 2. Schedule it
        scheduled = payment_service.schedule_payment(
            payment.payment_id,
            (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")
        )
        assert scheduled.status == PaymentStatus.SCHEDULED
        
        # 3. Create batch
        batch = payment_service.create_ach_batch(payment_ids=[payment.payment_id])
        assert batch is not None
        
        # 4. Mark as sent
        sent = payment_service.mark_payment_sent(payment.payment_id, "BATCH-001")
        assert sent.status == PaymentStatus.SENT
        
        # 5. Mark as completed
        completed = payment_service.mark_payment_completed(payment.payment_id)
        assert completed.status == PaymentStatus.COMPLETED
    
    def test_gl_correction_learning_integration(self):
        """Test that GL corrections feed into learning system."""
        gl_service = get_gl_correction("test-learning")
        
        # Make corrections
        for _ in range(5):
            gl_service.correct_gl_code(
                invoice_id=f"INV-LEARN-{_}",
                vendor="Recurring Vendor",
                original_gl="5000",
                corrected_gl="5300",  # Professional Services
                reason="Consulting fees",
            )
        
        # Check suggestion reflects learning
        suggestion = gl_service.get_suggested_gl(
            vendor="Recurring Vendor",
            category="consulting",
        )
        
        assert suggestion is not None
        # After multiple corrections, should suggest 5300


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
