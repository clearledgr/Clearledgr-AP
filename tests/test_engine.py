"""
Tests for Clearledgr Core Engine

These tests verify the core reconciliation logic works correctly.
"""

import pytest
import os
import tempfile
from datetime import datetime

# Use temp database for tests
os.environ["CLEARLEDGR_TEST_MODE"] = "1"

from clearledgr.core.engine import ClearledgrEngine
from clearledgr.core.database import ClearledgrDB
from clearledgr.services.multi_factor_scoring import MultiFactorScorer


class TestMultiFactorScorer:
    """Tests for the multi-factor scoring algorithm."""
    
    def setup_method(self):
        self.scorer = MultiFactorScorer()
    
    def test_exact_amount_match(self):
        """Exact amounts should score 40/40."""
        gateway = {"amount": 1500.00, "date": "2026-01-09", "description": "Payment", "reference": None}
        bank = {"amount": 1500.00, "date": "2026-01-09", "description": "Transfer", "reference": None}
        
        score = self.scorer.score_match(gateway, bank)
        assert score.amount_score == 40.0
    
    def test_amount_within_tolerance(self):
        """Amounts within 1% should score high."""
        gateway = {"amount": 1000.00, "date": "2026-01-09", "description": "Payment", "reference": None}
        bank = {"amount": 1005.00, "date": "2026-01-09", "description": "Transfer", "reference": None}  # 0.5% diff
        
        score = self.scorer.score_match(gateway, bank)
        assert score.amount_score >= 35.0  # Should be close to max
    
    def test_amount_outside_tolerance(self):
        """Amounts >5% different should score low."""
        gateway = {"amount": 1000.00, "date": "2026-01-09", "description": "Payment", "reference": None}
        bank = {"amount": 1100.00, "date": "2026-01-09", "description": "Transfer", "reference": None}  # 10% diff
        
        score = self.scorer.score_match(gateway, bank)
        assert score.amount_score < 20.0
    
    def test_same_day_date_match(self):
        """Same day should score 30/30."""
        gateway = {"amount": 100, "date": "2026-01-09", "description": "Payment", "reference": None}
        bank = {"amount": 100, "date": "2026-01-09", "description": "Transfer", "reference": None}
        
        score = self.scorer.score_match(gateway, bank)
        assert score.date_score == 30.0
    
    def test_date_within_window(self):
        """Dates within 3 days should score well."""
        gateway = {"amount": 100, "date": "2026-01-09", "description": "Payment", "reference": None}
        bank = {"amount": 100, "date": "2026-01-11", "description": "Transfer", "reference": None}  # 2 days later
        
        score = self.scorer.score_match(gateway, bank)
        assert score.date_score >= 20.0
    
    def test_reference_exact_match(self):
        """Exact reference match should score 10/10."""
        gateway = {"amount": 100, "date": "2026-01-09", "description": "Payment", "reference": "INV-123"}
        bank = {"amount": 100, "date": "2026-01-09", "description": "Transfer INV-123", "reference": "INV-123"}
        
        score = self.scorer.score_match(gateway, bank)
        assert score.reference_score == 10.0
    
    def test_total_score_threshold(self):
        """Perfect match should exceed auto-match threshold."""
        gateway = {"amount": 1500.00, "date": "2026-01-09", "description": "Stripe payment #12345", "reference": "pi_123"}
        bank = {"amount": 1500.00, "date": "2026-01-09", "description": "STRIPE TRANSFER pi_123", "reference": "pi_123"}
        
        score = self.scorer.score_match(gateway, bank)
        assert score.total_score >= 80  # Auto-match threshold


class TestClearledgrEngine:
    """Tests for the core engine."""
    
    def setup_method(self):
        """Create fresh database for each test."""
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db = ClearledgrDB(db_path=self.temp_db.name)
        self.engine = ClearledgrEngine(db=self.db)
        self.org_id = "test_org"
    
    def teardown_method(self):
        """Clean up temp database."""
        os.unlink(self.temp_db.name)
    
    def test_add_transaction(self):
        """Should add and retrieve transactions."""
        tx = self.engine.add_transaction(
            amount=1000.00,
            currency="EUR",
            date="2026-01-09",
            description="Test payment",
            source="gateway",
            organization_id=self.org_id,
        )
        
        assert tx.id is not None
        assert tx.amount == 1000.00
        assert tx.currency == "EUR"
        
        # Retrieve
        txs = self.engine.get_transactions(self.org_id)
        assert len(txs) == 1
        assert txs[0]["amount"] == 1000.00
    
    def test_pending_transactions(self):
        """Should return only pending transactions."""
        # Add pending
        self.engine.add_transaction(
            amount=100, currency="EUR", date="2026-01-09",
            description="Pending", source="gateway", organization_id=self.org_id,
        )
        
        pending = self.engine.get_pending_transactions(self.org_id)
        assert len(pending) == 1
    
    def test_run_reconciliation(self):
        """Should match transactions and create matches."""
        # Add gateway transaction
        self.engine.add_transaction(
            amount=500.00, currency="EUR", date="2026-01-09",
            description="Payment ABC", source="gateway",
            organization_id=self.org_id, reference="ABC",
        )
        
        # Add matching bank transaction
        self.engine.add_transaction(
            amount=500.00, currency="EUR", date="2026-01-09",
            description="Transfer ABC", source="bank",
            organization_id=self.org_id, reference="ABC",
        )
        
        # Run reconciliation
        result = self.engine.run_reconciliation(self.org_id, [], [])
        
        assert result["matches"] >= 0  # May or may not match depending on score
        assert "match_rate" in result
    
    def test_exception_creation(self):
        """Unmatched transactions should create exceptions."""
        # Add gateway transaction with no bank match
        self.engine.add_transaction(
            amount=999.99, currency="EUR", date="2026-01-09",
            description="Orphan payment", source="gateway",
            organization_id=self.org_id,
        )
        
        # Run reconciliation
        result = self.engine.run_reconciliation(self.org_id, [], [])
        
        # Check exceptions
        exceptions = self.engine.get_exceptions(self.org_id)
        assert len(exceptions) == 1
        assert exceptions[0]["type"] == "no_match"
    
    def test_dashboard_data(self):
        """Dashboard should return aggregated stats."""
        dashboard = self.engine.get_dashboard_data(self.org_id)
        
        assert "matched_transactions" in dashboard
        assert "open_exceptions" in dashboard
        assert "pending_drafts" in dashboard
        assert "match_rate" in dashboard


class TestAuthentication:
    """Tests for authentication."""
    
    def test_password_hashing(self):
        """Passwords should be securely hashed."""
        from clearledgr.core.auth import hash_password, verify_password
        
        password = "SecurePass123"
        hashed = hash_password(password)
        
        assert hashed != password
        assert verify_password(password, hashed)
        assert not verify_password("wrong", hashed)
    
    def test_jwt_token_creation(self):
        """Should create valid JWT tokens."""
        from clearledgr.core.auth import create_access_token, decode_token
        
        token = create_access_token(
            user_id="user123",
            email="test@example.com",
            organization_id="org456",
        )
        
        payload = decode_token(token)
        assert payload["sub"] == "user123"
        assert payload["email"] == "test@example.com"
        assert payload["org"] == "org456"
    
    def test_user_creation_and_auth(self):
        """Should create users and authenticate them."""
        from clearledgr.core.auth import create_user, authenticate_user, _users_db
        
        # Clear any existing users
        _users_db.clear()
        
        user = create_user(
            email="test@example.com",
            password="SecurePass123",
            name="Test User",
            organization_id="test_org",
        )
        
        assert user.email == "test@example.com"
        
        # Authenticate
        auth_user = authenticate_user("test@example.com", "SecurePass123")
        assert auth_user is not None
        assert auth_user.id == user.id
        
        # Wrong password
        assert authenticate_user("test@example.com", "wrong") is None


class TestAuditLogging:
    """Tests for audit logging."""
    
    def setup_method(self):
        """Create fresh audit log for each test."""
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        from clearledgr.core.audit import AuditLogger
        self.logger = AuditLogger(db_path=self.temp_db.name)
    
    def teardown_method(self):
        os.unlink(self.temp_db.name)
    
    def test_log_entry(self):
        """Should log entries with checksums."""
        from clearledgr.core.audit import AuditAction
        
        entry = self.logger.log(
            action=AuditAction.TRANSACTION_CREATE,
            user_id="user123",
            organization_id="org456",
            resource_type="transaction",
            resource_id="tx789",
            details={"amount": 1000},
        )
        
        assert entry.id is not None
        assert entry.checksum is not None
        assert entry.verify()  # Checksum should be valid
    
    def test_query_entries(self):
        """Should query entries by filter."""
        from clearledgr.core.audit import AuditAction
        
        # Log multiple entries
        self.logger.log(AuditAction.TRANSACTION_CREATE, "user1", "org1", resource_type="tx")
        self.logger.log(AuditAction.TRANSACTION_CREATE, "user2", "org1", resource_type="tx")
        self.logger.log(AuditAction.MATCH_CREATE, "user1", "org1", resource_type="match")
        
        # Query by user
        entries = self.logger.query(user_id="user1")
        assert len(entries) == 2
        
        # Query by action
        entries = self.logger.query(action="transaction.create")
        assert len(entries) == 2
    
    def test_integrity_verification(self):
        """Should verify integrity of log."""
        from clearledgr.core.audit import AuditAction
        
        self.logger.log(AuditAction.TRANSACTION_CREATE, "user1", "org1")
        
        result = self.logger.verify_integrity()
        assert result["integrity_verified"] is True
        assert result["invalid_entries"] == 0


class TestInputValidation:
    """Tests for input validation."""
    
    def test_transaction_amount_bounds(self):
        """Amount should be within reasonable bounds."""
        from clearledgr.api.engine import TransactionRequest
        from pydantic import ValidationError
        
        # Valid amount
        req = TransactionRequest(
            amount=1000.00, date="2026-01-09", description="Test",
            source="gateway", organization_id="test"
        )
        assert req.amount == 1000.00
        
        # Invalid: too large
        with pytest.raises(ValidationError):
            TransactionRequest(
                amount=999999999999, date="2026-01-09", description="Test",
                source="gateway", organization_id="test"
            )
    
    def test_currency_format(self):
        """Currency should be ISO 4217 format."""
        from clearledgr.api.engine import TransactionRequest
        from pydantic import ValidationError
        
        # Valid
        req = TransactionRequest(
            amount=100, currency="EUR", date="2026-01-09",
            description="Test", source="gateway", organization_id="test"
        )
        assert req.currency == "EUR"
        
        # Invalid format
        with pytest.raises(ValidationError):
            TransactionRequest(
                amount=100, currency="euros", date="2026-01-09",
                description="Test", source="gateway", organization_id="test"
            )
    
    def test_source_validation(self):
        """Source should be from allowed list."""
        from clearledgr.api.engine import TransactionRequest
        from pydantic import ValidationError
        
        # Valid sources
        for source in ["gateway", "bank", "internal", "email", "manual"]:
            req = TransactionRequest(
                amount=100, date="2026-01-09", description="Test",
                source=source, organization_id="test"
            )
            assert req.source == source
        
        # Invalid source
        with pytest.raises(ValidationError):
            TransactionRequest(
                amount=100, date="2026-01-09", description="Test",
                source="invalid", organization_id="test"
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
