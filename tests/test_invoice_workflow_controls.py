import asyncio

from clearledgr.services.invoice_workflow import InvoiceData, InvoiceWorkflowService


class _FakeDB:
    def __init__(self) -> None:
        self._rows = {}

    def get_invoice_status(self, gmail_id: str):
        return self._rows.get(gmail_id)

    def save_invoice_status(self, **kwargs):
        gmail_id = kwargs.get("gmail_id")
        self._rows[gmail_id] = dict(kwargs)
        return gmail_id

    def update_invoice_status(self, gmail_id: str = "", **kwargs):
        key = gmail_id or kwargs.pop("gmail_id", "")
        self._rows.setdefault(key, {})
        self._rows[key].update(kwargs)
        return True

    def get_slack_thread(self, gmail_id: str):
        return None


class _RecurringDetector:
    def __init__(self, is_recurring: bool = False, auto_approve: bool = False):
        self._is_recurring = is_recurring
        self._auto_approve = auto_approve

    def analyze_invoice(self, **_kwargs):
        return {
            "is_recurring": self._is_recurring,
            "auto_approve": self._auto_approve,
            "alerts": [],
            "pattern": {"invoice_count": 3},
        }


def _setup_service(monkeypatch):
    service = InvoiceWorkflowService(organization_id="default", auto_approve_threshold=0.95)
    service.db = _FakeDB()

    monkeypatch.setattr(
        "clearledgr.services.recurring_detection.get_recurring_detector",
        lambda _org: _RecurringDetector(is_recurring=False, auto_approve=False),
    )

    calls = {"auto": 0, "send": 0, "send_context": None}

    async def fake_auto(_invoice, reason="high_confidence", recurring_info=None):
        calls["auto"] += 1
        return {
            "status": "auto_approved",
            "reason": reason,
            "recurring_info": recurring_info,
        }

    async def fake_send(_invoice, extra_context=None):
        calls["send"] += 1
        calls["send_context"] = extra_context
        return {
            "status": "pending_approval",
            "extra_context": extra_context,
        }

    monkeypatch.setattr(service, "_auto_approve_and_post", fake_auto)
    monkeypatch.setattr(service, "_send_for_approval", fake_send)
    return service, calls


def test_po_required_policy_violation_forces_manual_approval(monkeypatch):
    service, calls = _setup_service(monkeypatch)

    invoice = InvoiceData(
        gmail_id="gmail-1",
        subject="Invoice 1001",
        sender="billing@vendor.com",
        vendor_name="Vendor Inc",
        amount=2500.0,
        confidence=0.99,
        policy_compliance={
            "compliant": False,
            "violations": [
                {
                    "policy_id": "po_required",
                    "message": "PO required for invoices over $1,000.00",
                    "severity": "warning",
                    "action": "flag_for_review",
                }
            ],
            "required_approvers": ["manager"],
        },
    )

    result = asyncio.run(service.process_new_invoice(invoice))

    assert result["status"] == "pending_approval"
    assert calls["auto"] == 0
    assert calls["send"] == 1
    assert "po_required_missing" in result["reason_codes"]
    assert result["validation_gate"]["passed"] is False


def test_po_match_exception_forces_manual_approval(monkeypatch):
    service, calls = _setup_service(monkeypatch)

    class _FakeMatch:
        def to_dict(self):
            return {
                "status": "exception",
                "exceptions": [
                    {
                        "type": "price_mismatch",
                        "message": "Invoice amount differs from PO",
                        "severity": "medium",
                    }
                ],
            }

    class _FakePOService:
        def match_invoice_to_po(self, **_kwargs):
            return _FakeMatch()

    monkeypatch.setattr(
        "clearledgr.services.invoice_workflow.get_purchase_order_service",
        lambda _org: _FakePOService(),
    )

    invoice = InvoiceData(
        gmail_id="gmail-2",
        subject="Invoice 1002",
        sender="billing@vendor.com",
        vendor_name="Vendor Inc",
        amount=3000.0,
        po_number="PO-1002",
        confidence=0.99,
        policy_compliance={"compliant": True, "violations": []},
        budget_impact=[{"after_approval_status": "healthy"}],
    )

    result = asyncio.run(service.process_new_invoice(invoice))

    assert result["status"] == "pending_approval"
    assert calls["auto"] == 0
    assert calls["send"] == 1
    assert "po_match_price_mismatch" in result["reason_codes"]
    assert result["validation_gate"]["passed"] is False


def test_budget_exceeded_forces_manual_approval(monkeypatch):
    service, calls = _setup_service(monkeypatch)

    invoice = InvoiceData(
        gmail_id="gmail-3",
        subject="Invoice 1003",
        sender="billing@vendor.com",
        vendor_name="Vendor Inc",
        amount=1200.0,
        confidence=0.99,
        policy_compliance={"compliant": True, "violations": []},
        budget_impact=[
            {
                "budget_name": "Software",
                "after_approval_status": "exceeded",
                "after_approval_percent": 112.0,
                "warning_message": "Will exceed budget by $180.00",
            }
        ],
    )

    result = asyncio.run(service.process_new_invoice(invoice))

    assert result["status"] == "pending_approval"
    assert calls["auto"] == 0
    assert calls["send"] == 1
    assert "budget_exceeded" in result["reason_codes"]
    assert result["validation_gate"]["passed"] is False


def test_healthy_invoice_can_auto_approve(monkeypatch):
    service, calls = _setup_service(monkeypatch)

    invoice = InvoiceData(
        gmail_id="gmail-4",
        subject="Invoice 1004",
        sender="billing@vendor.com",
        vendor_name="Vendor Inc",
        amount=250.0,
        confidence=0.99,
        policy_compliance={"compliant": True, "violations": []},
        budget_impact=[
            {
                "budget_name": "Software",
                "after_approval_status": "healthy",
                "after_approval_percent": 44.0,
            }
        ],
    )

    result = asyncio.run(service.process_new_invoice(invoice))

    assert result["status"] == "auto_approved"
    assert calls["auto"] == 1
    assert calls["send"] == 0


def test_approve_invoice_blocks_when_budget_requires_decision(monkeypatch):
    service, _ = _setup_service(monkeypatch)
    service.db._rows["gmail-approve-1"] = {
        "gmail_id": "gmail-approve-1",
        "status": "pending_approval",
        "vendor": "Vendor Inc",
        "amount": 1800.0,
        "currency": "USD",
        "invoice_number": "INV-APP-1",
        "due_date": "2026-02-20",
    }

    monkeypatch.setattr(
        service,
        "_load_budget_context_from_invoice_row",
        lambda _row: [
            {
                "budget_name": "Software",
                "after_approval_status": "exceeded",
                "after_approval_percent": 112.0,
                "budget_amount": 10000.0,
                "after_approval": 11200.0,
                "invoice_amount": 1800.0,
            }
        ],
    )

    result = asyncio.run(
        service.approve_invoice(
            gmail_id="gmail-approve-1",
            approved_by="manager@example.com",
        )
    )

    assert result["status"] == "needs_budget_decision"
    assert result["reason"] == "budget_requires_decision"


def test_approve_invoice_allows_budget_override_with_justification(monkeypatch):
    service, _ = _setup_service(monkeypatch)
    service.db._rows["gmail-approve-2"] = {
        "gmail_id": "gmail-approve-2",
        "status": "pending_approval",
        "vendor": "Vendor Inc",
        "amount": 2100.0,
        "currency": "USD",
        "invoice_number": "INV-APP-2",
        "due_date": "2026-02-21",
    }

    monkeypatch.setattr(
        service,
        "_load_budget_context_from_invoice_row",
        lambda _row: [
            {
                "budget_name": "Software",
                "after_approval_status": "critical",
                "after_approval_percent": 94.0,
                "budget_amount": 10000.0,
                "after_approval": 9400.0,
                "invoice_amount": 2100.0,
            }
        ],
    )

    async def fake_post(_invoice):
        return {"status": "success", "bill_id": "BILL-1", "vendor_id": "VEN-1"}

    async def fake_update(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "_post_to_erp", fake_post)
    monkeypatch.setattr(service, "_update_slack_approved", fake_update)

    result = asyncio.run(
        service.approve_invoice(
            gmail_id="gmail-approve-2",
            approved_by="manager@example.com",
            allow_budget_override=True,
            override_justification="Critical vendor contract",
        )
    )

    assert result["status"] == "approved"
    assert result["budget_override"] is True
